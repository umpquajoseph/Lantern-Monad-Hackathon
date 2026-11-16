"""One campaign run with sampled navigation (docs/campaign-spec.md, part 1).

    python -m sim.campaign --seed 42                 # one run, defaults (3 Scouts x 16 sorties)
    python -m sim.campaign --seed 42 --set n_scouts=1 check_policy=p95

A run is (world seed, Settings). It writes, under
data/campaigns/<batch_id>/<run_id>/, `record.json` and `poses.npz`, exactly
as docs/handoffs.md "Campaign simulator" says.

How a run works:

- The world (terrain window, true craters, the Scouts' starting map, hidden
  ice, drift, dock registration error) is drawn from the seed alone, so every
  design choice runs on the same worlds. Until part 3's `campaign_world`
  lands, `standin_world` builds it on the Shoemaker LOLA tile.
- The dock launches Scouts in turn. Each sortie is stepped along its plan in
  `reading_spacing_m` steps: descent corridor, searches and drills, survey
  lanes, return. The plan and the Scout's estimate live in the map frame;
  the true position is the estimate minus the Scout's hidden error.
- Position error is sampled, never computed by a filter (draft 4.2): after a
  fix the error is drawn from C23 Table 2 (trajectory 1 on a wrong match,
  2 and 3 pooled otherwise) and grows by drift_frac per metre driven.
- A sortie's results reach the shared maps when it docks. Sorties that are
  out at the same time plan on the maps as they stood at their launch.

Random streams are keyed by (seed, purpose, Scout, sortie number), so the
same Scout's n-th sortie meets the same faults and draws under every design
choice.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.stats import norm

from sim.settings import Settings, WorldRanges, sources

from sim import crossing as X              # part 2: cross-checks and crater merging

try:
    from sim.terrain import campaign_world  # part 3
except ImportError:
    campaign_world = None

from sim.terrain import DATA, LOLA_PX, LOLA_SHOEMAKER, LOLA_X_LEFT, LOLA_Y_TOP

FORMAT = "campaign-record/1"
CAMPAIGN_DIR = DATA / "campaigns"
ACT = {"descent": 0, "survey": 1, "return": 2, "check": 3, "reading": 4, "drill": 5,
       "search": 6, "docked": 7, "waiting": 8}
# Radial 95th percentile of a 2-D isotropic Gaussian, in units of its 1-sigma:
# sqrt(chi2_2(0.95)) = sqrt(-2 ln 0.05). Mathematics, not a setting.
K95_2D = math.sqrt(-2.0 * math.log(0.05))
POSE_COLS = ["t", "true_x", "true_y", "true_heading", "est_x", "est_y", "bound95", "energy_kwh", "activity"]


def rnd(x, nd=3):
    return None if x is None else round(float(x), nd)


def rng_for(seed: int, *key: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([int(seed), *map(int, key)]))


# ======================================================================= world

@dataclass
class World:
    site: str
    file: str
    x0: float               # centre of cell [0, 0], rows run north
    y0: float
    cell: float
    height: np.ndarray      # (H, W) float32
    slope: np.ndarray       # (H, W) degrees
    floor: np.ndarray       # (H, W) bool, PSR floor (stand-in: see standin_world)
    craters: np.ndarray     # (N, 3) true craters [x, y, d], detectable sizes only
    map_start: np.ndarray   # (M, 3) the Scouts' starting map
    dock: tuple             # true dock position
    draws: dict             # every WorldRanges draw, plus dock_reg_err
    ice: list               # hidden deposits [{x, y, radius, peak}]
    standin: str | None = None

    @property
    def bounds(self):
        h, w = self.height.shape
        c = self.cell
        return [self.x0 - c / 2, self.y0 - c / 2, self.x0 + (w - 0.5) * c, self.y0 + (h - 0.5) * c]


_TIF_CACHE: dict = {}


def _shoemaker_window(row0, col0, n):
    key = (row0, col0, n)
    if key not in _TIF_CACHE:
        import tifffile
        z = tifffile.imread(str(LOLA_SHOEMAKER).format("surf"))[row0:row0 + n, col0:col0 + n]
        sl = tifffile.imread(str(LOLA_SHOEMAKER).format("slp"))[row0:row0 + n, col0:col0 + n]
        # Flip so rows run north (docs/handoffs.md, Step 2).
        _TIF_CACHE[key] = (np.ascontiguousarray(z[::-1]).astype(np.float32),
                           np.ascontiguousarray(sl[::-1]).astype(np.float32))
    return _TIF_CACHE[key]


def draw_world_values(seed: int, ranges: WorldRanges, rng) -> dict:
    """Every WorldRanges draw for this seed, in a fixed order."""
    lo, hi = ranges.crater_n_ge10_per_km2
    d = {
        "crater_n_ge10_per_km2": float(math.exp(rng.uniform(math.log(lo), math.log(hi)))),
        "orbital_map_min_d_m": float(ranges.orbital_map_min_d_m),
        "missing_share_large": float(rng.uniform(*ranges.missing_share_large)),
        "n_deposits": int(rng.integers(ranges.n_deposits[0], ranges.n_deposits[1] + 1)),
        "drift_frac": float(rng.uniform(*ranges.drift_frac)),
        "dock_reg_sigma_m": float(rng.uniform(*ranges.dock_reg_sigma_m)),
    }
    d["dock_reg_err"] = [float(v) for v in rng.normal(0.0, d["dock_reg_sigma_m"], 2)]
    return d


def standin_world(seed: int, s: Settings, ranges: WorldRanges) -> World:
    """Shoemaker LOLA window with seeded craters and ice. Stand-in for part 3.

    The window is not PSR floor (spec 11.1). The dock sits 300 m inside the
    west edge; "floor" is every cell at least descent_m from the dock and
    50 m inside the window, so the descent corridor is about descent_m long.
    """
    rng = rng_for(seed, 0)
    row0, col0, n = s.standin_window_px
    z, sl = _shoemaker_window(row0, col0, n)
    cell = LOLA_PX
    x0 = LOLA_X_LEFT + cell * (col0 + 0.5)
    y0 = LOLA_Y_TOP - cell * (row0 + n - 0.5)     # after the flip, row 0 is the south edge
    size = n * cell
    dock = (x0 + 300.0, y0 + size / 2)
    jj, ii = np.meshgrid(np.arange(n), np.arange(n))
    gx, gy = x0 + jj * cell, y0 + ii * cell
    margin = 50.0
    floor = ((np.hypot(gx - dock[0], gy - dock[1]) >= s.descent_m)
             & (gx > x0 + margin) & (gx < x0 + size - margin)
             & (gy > y0 + margin) & (gy < y0 + size - margin))

    draws = draw_world_values(seed, ranges, rng)
    # Craters: truncated power law N(>D) = n10 (D/10)^-b over detectable sizes.
    b, dmin, dmax = s.standin_crater_sfd_b, s.standin_crater_d_min_m, s.standin_crater_d_max_m
    area_km2 = (size / 1000.0) ** 2
    n_tot = draws["crater_n_ge10_per_km2"] * area_km2 * ((dmin / 10) ** -b - (dmax / 10) ** -b)
    nc = int(rng.poisson(n_tot))
    u = rng.random(nc)
    a_, b_ = dmin ** -b, dmax ** -b
    d = (a_ - u * (a_ - b_)) ** (-1.0 / b)
    xy = rng.uniform(0, size, (nc, 2)) + [x0 - cell / 2, y0 - cell / 2]
    craters = np.column_stack([xy, d])
    in_map = (d >= draws["orbital_map_min_d_m"]) & (rng.random(nc) >= draws["missing_share_large"])
    map_start = craters[in_map]

    # Hidden ice: inside the floor, within survey reach of the descent end.
    route = descent_route_cached(z, sl, floor, x0, y0, cell, dock, s.slope_limit_deg)
    ex, ey = route[-1]
    ice = []
    tries = 0
    while len(ice) < draws["n_deposits"] and tries < 10000:
        tries += 1
        r, th = s.survey_reach_m * math.sqrt(rng.random()), rng.uniform(0, 2 * math.pi)
        x, y = ex + r * math.cos(th), ey + r * math.sin(th)
        j, i = int(round((x - x0) / cell)), int(round((y - y0) / cell))
        if not (0 <= i < n and 0 <= j < n and floor[i, j]):
            continue
        ice.append({"x": x, "y": y, "radius": float(rng.uniform(*ranges.deposit_radius_m)),
                    "peak": float(rng.uniform(*ranges.deposit_peak_wt))})
    return World(site="shoemaker", file="shoemaker_5m_window.npz", x0=x0, y0=y0, cell=cell,
                 height=z, slope=sl, floor=floor, craters=craters, map_start=map_start, dock=dock,
                 draws=draws, ice=ice,
                 standin="world: Shoemaker LOLA 5 m window, not PSR floor; seeded craters 7.5-20 m "
                         "(sim/campaign.py standin_world, site 'shoemaker')")


def make_world(seed: int, s: Settings, ranges: WorldRanges, site: str | None = None) -> World:
    site = site or s.site
    if site == "shoemaker":
        return standin_world(seed, s, ranges)
    if campaign_world is not None:
        w = campaign_world(site, seed, ranges)
        if not isinstance(w, World):   # part 3's own type: copy the fields we read
            w = World(**{f.name: getattr(w, f.name) for f in fields(World) if hasattr(w, f.name)})
        return w
    raise ValueError(f"site {site!r}: sim.terrain.campaign_world is missing")


_ROUTE_CACHE: dict = {}


def descent_route_cached(z, slope, floor, x0, y0, cell, dock, limit_deg):
    """Route per (terrain, dock), cached in memory and on disk so batch
    workers load it instead of each building the grid graph (a few hundred
    MB at its peak on a 6 km window)."""
    import hashlib
    h = hashlib.sha1()
    for arr in (slope, floor):
        h.update(np.ascontiguousarray(arr).tobytes())
    h.update(json.dumps([float(x0), float(y0), float(cell), list(map(float, dock)), float(limit_deg)]).encode())
    key = h.hexdigest()[:16]
    if key in _ROUTE_CACHE:
        return _ROUTE_CACHE[key]
    path = CAMPAIGN_DIR / "_routes" / f"{key}.json"
    if path.exists():
        route = [tuple(p) for p in json.loads(path.read_text(encoding="utf-8"))]
    else:
        route = descent_route(slope, floor, x0, y0, cell, dock, limit_deg)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(route), encoding="utf-8")
        os.replace(tmp, path)
    _ROUTE_CACHE[key] = route
    return route


def descent_route(slope, floor, x0, y0, cell, dock, limit_deg):
    """Shortest path on the 5 m grid from the dock to the nearest floor cell,
    through cells under the slope limit (8-neighbour Dijkstra). Returns the
    waypoints [(x, y), ...], about every 25 m, dock first, floor entry last."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    h, w = slope.shape
    di, dj = int(round((dock[1] - y0) / cell)), int(round((dock[0] - x0) / cell))
    ok = np.nan_to_num(slope, nan=90.0) < limit_deg
    ok[di, dj] = True
    idx = np.arange(h * w).reshape(h, w)
    rows, cols, wts = [], [], []
    for oi, oj in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = idx[max(0, -oi):h - max(0, oi), max(0, -oj):w - max(0, oj)]
        bb = idx[max(0, oi):h - max(0, -oi) if oi <= 0 else h, max(0, oj):w - max(0, -oj) if oj <= 0 else w]
        a_ok = ok[max(0, -oi):h - max(0, oi), max(0, -oj):w - max(0, oj)]
        b_ok = ok.ravel()[bb.ravel()].reshape(bb.shape)
        m = a_ok & b_ok
        rows.append(a[m]); cols.append(bb[m])
        wts.append(np.full(m.sum(), cell * math.hypot(oi, oj)))
    r = np.concatenate(rows).astype(np.int32); c = np.concatenate(cols).astype(np.int32)
    wt = np.concatenate(wts).astype(np.float32)
    del rows, cols, wts
    g = coo_matrix((wt, (r, c)), shape=(h * w, h * w)).tocsr()
    del r, c, wt
    dist, pred = dijkstra(g, directed=False, indices=idx[di, dj], return_predecessors=True)
    del g
    dist_f = np.where(floor.ravel() & np.isfinite(dist), dist, np.inf)
    end = int(np.argmin(dist_f))
    if not np.isfinite(dist_f[end]):
        raise RuntimeError("no slope-feasible route from the dock to the floor")
    path = [end]
    while path[-1] != idx[di, dj]:
        path.append(int(pred[path[-1]]))
    path = path[::-1]
    pts = [(x0 + (p % w) * cell, y0 + (p // w) * cell) for p in path]
    keep = pts[::5]
    if keep[-1] != pts[-1]:
        keep.append(pts[-1])
    keep[0] = (float(dock[0]), float(dock[1]))
    return [(float(x), float(y)) for x, y in keep]


# =================================================================== the model

def e95_rows(rows, z):
    """Draft 4.2: 95th percentile of a row = mean + z * s.d."""
    return [m + z * sd for m, sd in rows]


@lru_cache(maxsize=4096)
def _e95_mix_cached(p_milli: int, good: tuple, wrong: tuple) -> float:
    p = p_milli / 1000.0

    def cdf(x):
        g = sum(norm.cdf((x - m) / sd) for m, sd in good) / len(good)
        return (1 - p) * g + p * norm.cdf((x - wrong[0]) / wrong[1])

    lo, hi = 0.0, 100.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if cdf(mid) < 0.95:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def e95_mix(p: float, s: Settings) -> float:
    """95th percentile of the error just after a fix, if a share p of fixes are
    wrong matches: normal mixture of the C23 rows (draft 4.2's normal reading)."""
    return _e95_mix_cached(int(round(min(max(p, 0.0), 1.0) * 1000)), tuple(s.fix_err_good),
                           tuple(s.fix_err_wrong_match))


def fixed_spacing(s: Settings) -> float:
    """'fixed' policy: one spacing for the whole campaign, from draft Table 1."""
    if s.fixed_row == "best":
        e95 = min(e95_rows(s.fix_err_good, s.fix_z95))
    else:
        e95 = e95_rows([s.fix_err_wrong_match], s.fix_z95)[0]
    sp = (s.drill_limit_m - e95) / s.drift_frac
    return sp if sp > s.check_min_spacing_m else s.check_min_spacing_m


def energy_model(s: Settings) -> dict:
    """Per-activity draws, scaled so a baseline sortie averages avg_load_w.

    Baseline (brief p.6): descent_m down, survey_m, descent_m back at top
    speed, drill_sites_max drills of drill_h, the rest of sortie_h stopped.
    """
    base_km = (2 * s.descent_m + s.survey_m) / 1000.0
    drive_h = base_km * 1000.0 / s.top_speed_mps / 3600.0
    drill_h = s.drill_sites_max * s.drill_h
    e_raw = s.hotel_w * s.sortie_h + s.drive_w * drive_h + s.drill_w * drill_h
    k = s.avg_load_w * s.sortie_h / e_raw
    return {"scale": k, "baseline_km": base_km, "baseline_drive_h": drive_h,
            "baseline_avg_w": s.avg_load_w,
            "p_drive_w": k * (s.drive_w + s.hotel_w), "p_stop_w": k * s.hotel_w,
            "p_drill_w": k * (s.drill_w + s.hotel_w),
            "note": "drive_w, hotel_w, drill_w are assumed; all are scaled by `scale` so the "
                    "baseline sortie averages avg_load_w"}


def concentration(x, y, ice, threshold) -> float:
    """sim/ice.py field: each deposit is peak * (threshold/peak)^(r^2/R^2),
    equal to the threshold at its radius R. The same field crossing.deposit_at
    scores against."""
    c = 0.0
    for d in ice:
        r2 = ((x - d["x"]) ** 2 + (y - d["y"]) ** 2) / d["radius"] ** 2
        c += d["peak"] * (threshold / d["peak"]) ** r2
    return c


def footprint(s):
    """sim/ice.py IceSensor: the centre plus six points at half the footprint radius."""
    r = s.footprint_radius_m / 2
    return [(0.0, 0.0)] + [(r * math.cos(a), r * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)]


# ===================================================================== sortie

class Sortie:
    """One sortie, stepped from launch to dock. Reads the campaign's shared
    state as it stood at launch; returns what the dock applies on arrival."""

    def __init__(self, camp: "Campaign", scout: str, n: int, t0: float):
        self.c, self.s, self.w = camp, camp.s, camp.world
        s = self.s
        self.scout, self.n = scout, n
        self.sid = f"{scout}-{n:02d}"
        si = camp.scouts.index(scout)
        self.rng = rng_for(camp.seed, 1, si, n)           # navigation draws
        self.rng_ice = rng_for(camp.seed, 3, si, n)       # reading noise
        rf = rng_for(camp.seed, 2, si, n)                 # faults
        planned = 2 * camp.corridor_len + s.survey_m
        self.fault_at = {}
        for mod, p in (("camera", s.p_fail_camera), ("lamp", s.p_fail_lamp), ("neutron", s.p_fail_neutron)):
            hit, at = rf.random() < p, rf.uniform(0, planned)
            if hit:
                self.fault_at[mod] = at
        self.t = t0
        self.energy = s.battery_kwh
        self.em = camp.energy
        self.v = s.top_speed_mps
        self.drift_true = camp.world.draws["drift_frac"]
        # Estimate starts at the dock's registered position; truth at the dock.
        reg = camp.world.draws["dock_reg_err"]
        self.errx, self.erry = float(reg[0]), float(reg[1])
        self.ex, self.ey = camp.dock_reg
        self.heading = 0.0
        self.b_fix = s.dock_reg_bound95_m
        self.d_fix = self.d_chk = self.d_total = self.d_log = self.d_read = 0.0
        self.t_fix = t0
        self._new_drift_dir()
        self.hdg_err, self.hsign = 0.0, (1.0 if self.rng.random() < 0.5 else -1.0)
        self.anchor = "dock"
        self.n_checks = self.n_fixes = self.n_wrong = 0
        self.rims = deque(camp.rim_window, maxlen=s.p_est_window_rims)
        self.nav_ok = True
        self.neutron_ok = True
        self.faults = []
        self.returning = False
        self.turn_reason = None
        self.lost = False
        self.survey_used = 0.0
        self.time = {"drive": 0.0, "check": 0.0, "reading": 0.0, "drill": 0.0, "wait": 0.0}
        self.rows: list = []
        self.events: list = []
        self.sightings: list = []
        self.new_finds: list = []
        self.search_results: list = []     # (find_id, result, detail)
        self.drilled: list = []            # find ids
        self.lanes_done: list = []
        self.drills_left = s.drill_sites_max
        self.act = ACT["descent"]
        self.true_find_xy = camp.true_find_xy  # engine-side truth, never in a Scout field
        self.spacing_nominal = self._nominal_spacing()

    # ------------------------------------------------------------ state
    @property
    def tx(self):
        return self.ex - self.errx

    @property
    def ty(self):
        return self.ey - self.erry

    @property
    def bound(self):
        s = self.s
        b = self.b_fix + s.drift_frac * self.d_fix
        if not s.star_tracker:
            b += 0.5 * s.heading_drift_rad_per_m * self.d_fix ** 2
        return b

    def p_est(self):
        share = (sum(self.rims) / len(self.rims)) if self.rims else self.s.missing_share_prior
        return min(max(self.s.p_wrong_base + self.s.p_wrong_per_missing * share, 0.0), 1.0)

    def belief_e95(self):
        """The 95th-pct error a Scout believes it has just after a fix.
        'mixture': the C23 rows mixed at its own estimated wrong-match rate;
        'good_rows': as if every fix were good (C23 trajectories 2 and 3)."""
        return e95_mix(self.p_est() if self.s.bound_model == "mixture" else 0.0, self.s)

    def _nominal_spacing(self):
        s = self.s
        if s.check_policy == "fixed":
            return fixed_spacing(s)
        if s.check_policy == "fixed_time":
            return max(s.check_interval_h * 3600 * self.v, s.check_min_spacing_m)
        if s.check_policy == "before_work":
            sp = (s.nav_limit_m - self.belief_e95()) / s.drift_frac
        else:
            sp = (s.drill_limit_m - self.belief_e95()) / s.drift_frac
        return max(sp, s.check_min_spacing_m)

    def _new_drift_dir(self):
        a = self.rng.uniform(0, 2 * math.pi)
        self.dux, self.duy = math.cos(a), math.sin(a)
        self.dsign = 1.0 if self.rng.random() < 0.5 else -1.0

    # ------------------------------------------------------------ logging
    def row(self, act=None):
        self.rows.append([self.t, self.tx, self.ty, self.heading, self.ex, self.ey, self.bound,
                          self.energy, self.act if act is None else act])

    def event(self, typ, **kw):
        e = {"t": rnd(self.t, 1), "sortie_id": self.sid, "scout": self.scout, "type": typ,
             "x": rnd(self.tx, 2), "y": rnd(self.ty, 2)}
        e.update(kw)
        self.events.append(e)
        self.row()
        return e

    # ------------------------------------------------------------ motion
    def stop(self, seconds, kind, power_w, act):
        self.t += seconds
        self.energy -= power_w * seconds / 3.6e6
        self.time[kind] += seconds
        if seconds > 0:
            self.row(act)

    def drive_to(self, gx, gy, act) -> bool:
        """Drive the estimate to (gx, gy). False if the sortie must stop early."""
        self.act = act
        step = self.s.reading_spacing_m
        while True:
            if self.lost or (self.returning and act not in (ACT["return"],)):
                return False
            dx, dy = gx - self.ex, gy - self.ey
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                return True
            ds = min(step, dist)
            self._advance(ds, dx / dist, dy / dist)

    def _advance(self, ds, ux, uy):
        s = self.s
        self.ex += ds * ux
        self.ey += ds * uy
        if s.drift_mode == "along_track":
            self.errx += self.drift_true * ds * ux * self.dsign
            self.erry += self.drift_true * ds * uy * self.dsign
        else:
            self.errx += self.drift_true * ds * self.dux
            self.erry += self.drift_true * ds * self.duy
        if not s.star_tracker:
            self.hdg_err += s.heading_drift_rad_per_m * ds * self.hsign
            self.errx += -uy * self.hdg_err * ds
            self.erry += ux * self.hdg_err * ds
        self.heading = math.atan2(uy, ux)
        self.d_fix += ds; self.d_chk += ds; self.d_total += ds; self.d_log += ds; self.d_read += ds
        dt = ds / self.v
        self.t += dt
        self.energy -= self.em["p_drive_w"] * dt / 3.6e6
        self.time["drive"] += dt
        if self.act in (ACT["survey"], ACT["search"]):
            self.survey_used += ds
        if self.d_log >= s.log_spacing_m:
            self.d_log = 0.0
            self.row()
        for mod, at in list(self.fault_at.items()):
            if self.d_total >= at:
                del self.fault_at[mod]
                self._fault(mod)
        if self.energy <= 0:
            self.lost = True
            self.event("lost", energy_kwh=0.0)
            return
        if self.act == ACT["survey"] and self.d_read >= s.reading_spacing_m:
            self.d_read = 0.0
            self.reading()
        if self.nav_ok and self._check_due() and self._map_predicts():
            self.check("schedule")
        if not self.returning and self._must_turn_back():
            self.returning = True
            self.turn_reason = "energy"
            self.event("turn_back", reason="energy")

    def _fault(self, mod):
        self.faults.append(mod)
        self.event("fault", module=mod)
        if mod in ("camera", "lamp"):
            self.nav_ok = False
            if not self.returning:
                self.returning = True
                self.turn_reason = "fault"
                self.event("turn_back", reason="fault")
        else:
            self.neutron_ok = False

    def _return_energy(self):
        d = math.hypot(self.ex - self.c.entry[0], self.ey - self.c.entry[1]) + self.c.corridor_len
        e = d / self.v * self.em["p_drive_w"] / 3.6e6
        if self.nav_ok:
            e += d / self.spacing_nominal * self.s.check_stop_s * self.em["p_stop_w"] / 3.6e6
        return e

    def _must_turn_back(self):
        return self.energy - self.s.reserve_frac * self.s.battery_kwh <= self._return_energy()

    # ------------------------------------------------------------ checks
    def _check_due(self):
        s = self.s
        if self.d_chk < s.check_min_spacing_m:
            return False
        pol = s.check_policy
        if pol == "fixed":
            return self.d_fix >= self.spacing_nominal
        if pol == "fixed_time":
            return self.t - self.t_fix >= s.check_interval_h * 3600
        if pol == "before_work":
            return self.bound >= s.nav_limit_m
        return self.bound >= s.drill_limit_m      # 'p95' and 'filter'

    def _map_predicts(self):
        """The Scout stops to check only where its own map shows enough craters."""
        return len(self.c.map_tree.query_ball_point((self.ex, self.ey), self.s.fix_range_m)) >= self.s.fix_min_craters \
            if self.c.map_tree is not None else False

    def check(self, why):
        s, c = self.s, self.c
        self.stop(s.check_stop_s, "check", self.em["p_stop_w"], ACT["check"])
        self.n_checks += 1
        self.d_chk = 0.0
        tx, ty = self.tx, self.ty
        err_before = math.hypot(self.errx, self.erry)
        reach = s.fix_range_m + s.rim_detect_d_max_m / 2 + s.map_match_tol_m   # crater_view's own box
        ti = c.true_tree.query_ball_point((tx, ty), reach) if c.true_tree is not None else []
        mi = c.map_tree.query_ball_point((tx, ty), reach) if c.map_tree is not None else []
        ti, mi = sorted(ti), sorted(mi)
        tc, mc = c.world.craters[ti], c.map_arr[mi]
        view = X.crater_view((tx, ty), tc, mc, s)
        # What the Scout itself observes: rims it saw and could not match, and
        # mapped craters it expected and did not see.
        miss = set(view["missing"])
        for k in view["in_view"]:
            self.rims.append(1 if k in miss else 0)
        for _ in view["misplaced"]:
            self.rims.append(1)
        n_view = len(view["in_view"])
        n_match = len(view["matched"])
        fixed = n_match >= s.fix_min_craters
        wrong, cause = False, None
        if fixed:
            p = min(max(s.p_wrong_base + s.p_wrong_per_missing * view["share_bad"], 0.0), 1.0)
            wrong = bool(self.rng.random() < p)
            if wrong:
                cause = X.wrong_match_cause(view, s, self.rng)
                if cause.startswith("misplaced:"):
                    # crossing names a row of the local map cut; name the crater instead.
                    row = mi[int(cause.split(":")[1])]
                    n0 = len(c.world.map_start)
                    cause = "misplaced:" + (c.added[row - n0]["crater_id"] if row >= n0 else str(row))
                m, sd = s.fix_err_wrong_match
                sm, ssd = s.fix_sigma_wrong_match
            else:
                k = int(self.rng.integers(len(s.fix_err_good)))
                m, sd = s.fix_err_good[k]
                sm, ssd = s.fix_sigma_good[k]
            mag = abs(self.rng.normal(m, sd))
            sig = abs(self.rng.normal(sm, ssd))
            a = self.rng.uniform(0, 2 * math.pi)
            ox, oy = mag * math.cos(a), mag * math.sin(a)
            # Added craters carry the error of the Scouts that saw them (draft
            # 4.3): crossing.crater_view gives the matched craters' mean offset.
            ox += float(view["fix_bias"][0]); oy += float(view["fix_bias"][1])
            self.errx, self.erry = ox, oy
            self.ex, self.ey = tx + ox, ty + oy
            self.b_fix = K95_2D * sig if s.check_policy == "filter" else self.belief_e95()
            self.d_fix = 0.0
            self.t_fix = self.t
            self.hdg_err = 0.0 if s.star_tracker else self.hdg_err
            self._new_drift_dir()
            self.n_fixes += 1
            self.n_wrong += int(wrong)
            self.anchor = f"{self.sid}#{self.n_fixes}"
            if s.check_policy in ("p95", "before_work"):
                self.spacing_nominal = self._nominal_spacing()
        self.event("check", est=[rnd(self.ex, 2), rnd(self.ey, 2)], bound95=rnd(self.bound, 2),
                   wrong_match=wrong, wrong_match_cause=cause, craters_in_view=n_view, matched=n_match, fixed=fixed,
                   reason=why, true_err_before_m=rnd(err_before, 3), true_err_m=rnd(math.hypot(self.errx, self.erry), 3))
        for k in view["missing"]:
            sg = X.make_sighting(tc[k], (tx, ty), (self.errx, self.erry), self.bound, self.anchor, self.rng, s)
            ev = self.event("sighting", est=[rnd(v, 2) for v in sg["est"]], d_est=rnd(sg["d_est"], 2),
                            bound95=rnd(sg["bound95"], 2), anchor=sg["anchor"])
            self.sightings.append(ev)

    def work_check(self, why, force=False):
        """'before_work' fixes before a find is logged, a search and a drill;
        `force` asks for one under any policy."""
        if (force or self.s.check_policy == "before_work") and self.nav_ok and self._map_predicts() \
                and self.d_chk >= self.s.check_min_spacing_m:
            self.check(why)

    # ------------------------------------------------------------ ice
    def read_here(self):
        x, y = self.tx, self.ty
        foot = self.c.foot
        c = sum(concentration(x + ox, y + oy, self.w.ice, self.s.find_threshold_wt) for ox, oy in foot) / len(foot)
        wt = max(0.0, c + self.rng_ice.normal(0.0, self.s.sensor_noise_wt))
        if self.s.reading_stop_s:
            self.stop(self.s.reading_stop_s, "reading", self.em["p_stop_w"], ACT["reading"])
        return wt

    def reading(self):
        if not self.neutron_ok:
            return
        s = self.s
        wt = self.read_here()
        if wt < s.find_threshold_wt:
            return
        self.event("reading", reading_wt=rnd(wt, 3), est=[rnd(self.ex, 2), rnd(self.ey, 2)],
                   bound95=rnd(self.bound, 2), anchor=self.anchor)
        near = self._nearest_find(self.ex, self.ey)
        if near is not None:
            if near in self.new_finds and wt > near["reading_wt"]:
                near.update(est=[rnd(self.ex, 2), rnd(self.ey, 2)], bound95=rnd(self.bound, 2),
                            reading_wt=rnd(wt, 3), anchor=self.anchor)
                self.true_find_xy[near["find_id"]] = (self.tx, self.ty)
            return
        self.work_check("find")
        fid = self.c.next_find_id()
        f = {"find_id": fid, "scout": self.scout, "sortie_id": self.sid,
             "est": [rnd(self.ex, 2), rnd(self.ey, 2)], "bound95": rnd(self.bound, 2),
             "reading_wt": rnd(wt, 3), "status": "find", "anchor": self.anchor}
        self.true_find_xy[fid] = (self.tx, self.ty)
        self.new_finds.append(f)
        self.event("find", find_id=fid, est=f["est"], bound95=f["bound95"], reading_wt=f["reading_wt"],
                   anchor=self.anchor)

    def _nearest_find(self, x, y):
        best, bd = None, self.s.find_sep_m
        for f in self.c.finds_at_launch + self.new_finds:
            d = math.hypot(f["est"][0] - x, f["est"][1] - y)
            if d <= bd:
                best, bd = f, d
        return best

    # ------------------------------------------------------------ work items
    def search(self, f) -> bool:
        s = self.s
        if not self.neutron_ok:
            return False
        if not self.drive_to(f["est"][0], f["est"][1], ACT["survey"]):
            return False
        self.work_check("search")
        if self.anchor == "dock":
            # crossing brief: a search is fix-anchored, never on the dock's error alone.
            self.work_check("search", force=True)
        radius = X.search_area(f, self.bound, s)
        pts = X.search_points(f, radius, s)
        comb = math.hypot(f["bound95"], self.bound)
        self.event("search_start", find_id=f["find_id"], radius_m=rnd(radius, 2),
                   find_bound95=f["bound95"], searcher_bound95=rnd(self.bound, 2), combined_bound95=rnd(comb, 2))
        readings, true_xy = [], []
        for px, py in pts:
            if not self.drive_to(float(px), float(py), ACT["search"]):
                return False
            if not self.neutron_ok:
                return False
            wt = self.read_here()
            readings.append((self.ex, self.ey, self.bound, wt, self.anchor))
            true_xy.append((self.tx, self.ty))
            if wt >= s.find_threshold_wt:
                self.event("reading", reading_wt=rnd(wt, 3), est=[rnd(self.ex, 2), rnd(self.ey, 2)],
                           bound95=rnd(self.bound, 2), anchor=self.anchor)
        det = X.judge_search_detail(f, readings, s)
        match_xy = true_xy[det["match"]] if det.get("match") is not None else None
        sc = X.score_search(self.true_find_xy[f["find_id"]], det["result"], self.w.ice, s, match_xy)
        self.event("search_end", find_id=f["find_id"], radius_m=rnd(radius, 2), result=det["result"],
                   reason=det.get("reason"), combined_bound95=rnd(comb, 2),
                   truth_has_deposit=bool(sc["truth_has_deposit"]),
                   false_confirmed=bool(sc["false_confirmed"]), missed=bool(sc["missed"]))
        self.search_results.append((f["find_id"], det["result"], det))
        if det["result"] == "confirmed" and self.drills_left > 0:
            tgt = det.get("est") or f["est"]
            self.drill(f, tgt)
        return True

    def drill(self, f, tgt) -> bool:
        if not self.drive_to(tgt[0], tgt[1], ACT["survey"]):
            return False
        self.work_check("drill")
        s = self.s
        self.stop(s.drill_h * 3600, "drill", self.em["p_drill_w"], ACT["drill"])
        tfx, tfy = self.true_find_xy[f["find_id"]]
        self.event("drill", find_id=f["find_id"], est=[rnd(self.ex, 2), rnd(self.ey, 2)],
                   true_err_m=rnd(math.hypot(self.errx, self.erry), 3),
                   miss_m=rnd(math.hypot(self.tx - tfx, self.ty - tfy), 3),
                   hit=X.deposit_at((self.tx, self.ty), self.w.ice, s) is not None)
        self.drills_left -= 1
        self.drilled.append(f["find_id"])
        return True

    def lane(self, ln) -> bool:
        (ax, ay), (bx, by) = ln["a"], ln["b"]
        if math.hypot(bx - self.ex, by - self.ey) < math.hypot(ax - self.ex, ay - self.ey):
            (ax, ay), (bx, by) = (bx, by), (ax, ay)
        if not self.drive_to(ax, ay, ACT["survey"]):
            return False
        if not self.drive_to(bx, by, ACT["survey"]):
            return False
        self.lanes_done.append(ln["id"])
        return True

    # ------------------------------------------------------------ the sortie
    def run(self, searches, drills, lanes):
        s, c = self.s, self.c
        self.row(ACT["descent"])
        self.event("launch", energy_kwh=rnd(self.energy, 4))
        for wx, wy in c.corridor[1:]:
            if not self.drive_to(wx, wy, ACT["descent"]):
                break
        items = [("search", f) for f in searches] + [("drill", f) for f in drills]
        # Visit work items nearest-first, then the lanes reserved at launch.
        while items and not self.returning and not self.lost:
            k = min(range(len(items)), key=lambda i: math.hypot(items[i][1]["est"][0] - self.ex,
                                                                 items[i][1]["est"][1] - self.ey))
            kind, f = items.pop(k)
            if self.survey_used + math.hypot(f["est"][0] - c.entry[0], f["est"][1] - c.entry[1]) >= s.survey_m:
                break
            if kind == "search":
                self.search(f)
            elif self.drills_left > 0:
                self.drill(f, f["est"])
        ex, ey = c.entry
        for i, ln in enumerate(lanes):
            if self.returning or self.lost:
                break
            (ax, ay), (bx, by) = ln["a"], ln["b"]
            da, db = math.hypot(ax - self.ex, ay - self.ey), math.hypot(bx - self.ex, by - self.ey)
            fx, fy = (bx, by) if da <= db else (ax, ay)
            need = min(da, db) + s.lane_segment_m + math.hypot(fx - ex, fy - ey)
            if i and self.survey_used + need > s.survey_m:
                break
            self.lane(ln)
        self.survey_back = math.hypot(self.ex - ex, self.ey - ey)
        if not self.lost:
            self.returning = True
            self.drive_to(c.entry[0], c.entry[1], ACT["return"])
        for wx, wy in c.corridor[::-1][1:]:
            if self.lost:
                break
            self.drive_to(wx, wy, ACT["return"])
        if not self.lost:
            # Home on the dock itself: drive the true remaining distance.
            d_home = math.hypot(self.tx - self.w.dock[0], self.ty - self.w.dock[1])
            if d_home > 0:
                self.act = ACT["return"]
                dt = d_home / self.v
                self.t += dt
                self.energy -= self.em["p_drive_w"] * dt / 3.6e6
                self.time["drive"] += dt
                self.d_total += d_home
            self.ex, self.ey = self.w.dock
            self.errx = self.erry = 0.0
            if self.energy <= 0:
                self.lost = True
                self.event("lost", energy_kwh=0.0)
        if self.lost:
            outcome = "lost"
        else:
            self.act = ACT["docked"]
            self.event("dock_arrive", energy_kwh=rnd(self.energy, 4))
            if self.turn_reason == "fault":
                outcome = "fault_return"
            elif self.energy < s.reserve_frac * s.battery_kwh - 1e-9:
                outcome = "below_reserve"
            else:
                outcome = "ok"
        self.outcome = outcome
        return self


# =================================================================== campaign

class Campaign:
    def __init__(self, seed: int, s: Settings, ranges: WorldRanges | None = None, site: str | None = None):
        self.seed, self.s = int(seed), s
        self.ranges = ranges or WorldRanges(drift_frac=(s.drift_frac, s.drift_frac))
        self.world = make_world(seed, s, self.ranges, site)
        w = self.world
        self.energy = energy_model(s)
        self.scouts = [f"S{i + 1}" for i in range(s.n_scouts)]
        self.dock_reg = (w.dock[0] + w.draws["dock_reg_err"][0], w.dock[1] + w.draws["dock_reg_err"][1])
        route = descent_route_cached(w.height, w.slope, w.floor, w.x0, w.y0, w.cell, w.dock, s.slope_limit_deg)
        off = np.array(self.dock_reg) - np.array(w.dock)
        # The dock plans in the map frame from its registered position.
        self.corridor = [(float(x + off[0] * (1 - i / max(len(route) - 1, 1))),
                          float(y + off[1] * (1 - i / max(len(route) - 1, 1))))
                         for i, (x, y) in enumerate(route)]
        self.entry = self.corridor[-1]
        self.corridor_len = float(sum(math.hypot(b[0] - a[0], b[1] - a[1])
                                      for a, b in zip(self.corridor, self.corridor[1:])))
        self.true_tree = cKDTree(w.craters[:, :2]) if len(w.craters) else None
        # True craters absent from the starting map (crossing.score_added indexes these).
        if len(w.craters) and len(w.map_start):
            dm, _ = cKDTree(w.map_start[:, :2]).query(w.craters[:, :2])
            self.craters_missing = w.craters[dm > 1e-6]
        else:
            self.craters_missing = w.craters
        self.foot = footprint(s)
        self.map_rows = [list(map(float, r)) for r in w.map_start]
        self._rebuild_map()
        self.added: list = []
        self.pool: list = []
        self.finds: list = []
        self.true_find_xy: dict = {}
        self._fid = 0
        self.rim_window = deque(maxlen=s.p_est_window_rims)
        self.lanes = self._plan_lanes()
        self.lane_state = {ln["id"]: "pending" for ln in self.lanes}
        self.sorties: list = []
        self.events: list = []
        self.poses: dict = {}
        self.added_by_sortie: dict = {}

    # ------------------------------------------------------------ helpers
    def _rebuild_map(self):
        self.map_arr = np.array(self.map_rows, float).reshape(-1, 3)
        self.map_tree = cKDTree(self.map_arr[:, :2]) if len(self.map_arr) else None

    def next_find_id(self):
        self._fid += 1
        return self._fid

    def _plan_lanes(self):
        s, w = self.s, self.world
        ex, ey = self.entry
        sp, L = s.survey_line_spacing_m, s.lane_segment_m
        reach = s.survey_reach_m
        h, wd = w.floor.shape
        good = w.floor & (np.nan_to_num(w.slope, nan=90.0) < s.slope_limit_deg)
        lanes = []
        ks = range(-int(reach // sp), int(reach // sp) + 1)
        for k in ks:
            y = ey + (k + 0.5) * sp
            for m in range(-int(reach // L) - 1, int(reach // L) + 1):
                xa, xb = ex + m * L, ex + (m + 1) * L
                xm = (xa + xb) / 2
                if math.hypot(xm - ex, y - ey) > reach:
                    continue
                xs = np.arange(xa, xb + 1e-9, w.cell)
                jj = np.round((xs - w.x0) / w.cell).astype(int)
                ii = int(round((y - w.y0) / w.cell))
                if ii < 0 or ii >= h or jj.min() < 0 or jj.max() >= wd or not good[ii, jj].all():
                    continue
                lanes.append({"id": len(lanes), "a": (xa, y), "b": (xb, y),
                              "dist_entry": math.hypot(xm - ex, y - ey)})
        return lanes

    def _reserve_lanes(self, start_xy, budget):
        """Greedy chain of pending lanes, nearest first. The survey budget
        includes the drive back to the descent end, so a planned sortie is
        descent_m + survey_m + descent_m (brief p.6: 1.5 + 4 km + return)."""
        out, used = [], 0.0
        x, y = start_xy
        ex, ey = self.entry
        pending = [ln for ln in self.lanes if self.lane_state[ln["id"]] == "pending"]
        while pending:
            k = min(range(len(pending)), key=lambda i: min(math.hypot(pending[i]["a"][0] - x, pending[i]["a"][1] - y),
                                                           math.hypot(pending[i]["b"][0] - x, pending[i]["b"][1] - y)))
            ln = pending[k]
            da = math.hypot(ln["a"][0] - x, ln["a"][1] - y)
            db = math.hypot(ln["b"][0] - x, ln["b"][1] - y)
            nx, ny = ln["a"] if da > db else ln["b"]
            step = min(da, db) + self.s.lane_segment_m
            if out and used + step + math.hypot(nx - ex, ny - ey) > budget:
                break
            pending.pop(k)
            used += step
            x, y = nx, ny
            out.append(ln)
            self.lane_state[ln["id"]] = "reserved"
        return out

    # ------------------------------------------------------------ the loop
    def run(self):
        s = self.s
        t_wall = time.perf_counter()
        heap = []
        seq = 0
        for i, sc in enumerate(self.scouts):
            heapq.heappush(heap, (i * s.dock_charge_h * 3600.0, 1, seq, "launch", sc, None)); seq += 1
        count = {sc: 0 for sc in self.scouts}
        alive = set(self.scouts)
        dock_free = 0.0
        while heap:
            t, _pri, _, kind, sc, payload = heapq.heappop(heap)
            if kind == "launch":
                if sc not in alive or count[sc] >= s.sorties_per_scout:
                    continue
                count[sc] += 1
                so = self._launch(sc, count[sc], t)
                if so.outcome == "lost":
                    alive.discard(sc)
                    self._apply(so, t_arr=None)
                else:
                    heapq.heappush(heap, (so.t, 0, seq, "arrive", sc, so)); seq += 1
            else:
                so = payload
                self._apply(so, t_arr=t)
                start = max(t, dock_free)
                wait = start - t
                used = s.battery_kwh - so.energy
                charge = used / s.dock_power_kw * 3600.0
                dock_free = start + charge
                so.time["wait"] = wait
                rows = self.poses[so.sid]
                last = list(rows[-1])
                if wait > 0:
                    rows.append([t] + last[1:8] + [ACT["waiting"]])
                    rows.append([start] + last[1:8] + [ACT["waiting"]])
                rows.append([start] + last[1:8] + [ACT["docked"]])
                rows.append([dock_free, *last[1:7], s.battery_kwh, ACT["docked"]])
                down = s.turnaround_h * 3600.0
                for mod in so.faults:
                    self.events.append({"t": rnd(t, 1), "sortie_id": so.sid, "scout": sc, "type": "swap",
                                        "x": rnd(self.world.dock[0], 2), "y": rnd(self.world.dock[1], 2),
                                        "module": mod, "downtime_h": s.swap_downtime_h})
                if so.faults:
                    down = max(down, s.swap_downtime_h * 3600.0)
                rec = self._sortie_record(so)
                rec["time_s"]["wait"] = rnd(wait, 1)
                heapq.heappush(heap, (dock_free + down, 1, seq, "launch", sc, None)); seq += 1
        self.t_end = max(r["t_end"] for r in self.sorties) if self.sorties else 0.0
        self._contact_gaps()
        self.events.sort(key=lambda e: (e["t"], e["sortie_id"] or "", e["type"]))
        self.wall_s = time.perf_counter() - t_wall
        return self

    def _launch(self, sc, n, t):
        s = self.s
        self.finds_at_launch = list(self.finds)
        so = Sortie(self, sc, n, t)
        searches = []
        for f in sorted(self.finds, key=lambda f: math.hypot(f["est"][0] - self.entry[0], f["est"][1] - self.entry[1])):
            if len(searches) >= s.searches_per_sortie_max:
                break
            if f.get("_busy") is None and X.pick_searcher(f, [sc]) == sc:
                searches.append(f)
                f["_busy"] = so.sid
        drills = []
        for f in sorted(self.finds, key=lambda f: math.hypot(f["est"][0] - self.entry[0], f["est"][1] - self.entry[1])):
            if len(drills) >= s.drill_sites_max:
                break
            if f["status"] == "confirmed" and f.get("_busy") is None:
                drills.append(f)
                f["_busy"] = so.sid
        # Work items eat into survey_m as they are driven; reserved lanes left
        # undriven go back to the pool on arrival.
        lanes = self._reserve_lanes(self.entry, s.survey_m)
        so.planned_lanes = [ln["id"] for ln in lanes]
        so.planned_drills = len(drills)
        so.run(searches, drills, lanes)
        self.poses[so.sid] = so.rows
        return so

    def _apply(self, so: Sortie, t_arr):
        """What the dock does when a sortie ends: maps, finds, lanes, merge."""
        s = self.s
        self.events.extend(so.events)
        for lid in so.planned_lanes:
            self.lane_state[lid] = "done" if lid in so.lanes_done else "pending"
        for f in self.finds:
            if f.get("_busy") == so.sid:
                f["_busy"] = None
        by_id = {f["find_id"]: f for f in self.finds}
        for fid, result, det in so.search_results:
            f = by_id[fid]
            f["status"] = result
            f.setdefault("history", []).append([so.sid, result])
            if det.get("est") is not None:
                f["confirm_est"] = [rnd(v, 2) for v in det["est"]]
        for f in so.new_finds:
            f["history"] = [[so.sid, "find"]]
            f["_busy"] = None
            self.finds.append(f)
        by_id = {f["find_id"]: f for f in self.finds}
        for fid in so.drilled:
            by_id[fid]["status"] = "drilled"
            by_id[fid]["history"].append([so.sid, "drilled"])
        self.rim_window.extend(so.rims)
        self.pool.extend(so.sightings)
        self.added_by_sortie[so.sid] = 0
        if s.map_refinement and self.pool and t_arr is not None:
            added = X.merge_sightings(self.pool, self.map_arr, s, len(self.added) + 1)
            if added:
                drop = set()
                for a in added:
                    drop.update(a["members"])
                    # true_crater indexes self.craters_missing here; record()
                    # re-indexes it into the record's truth.craters_missing.
                    sc = X.score_added(a, self.craters_missing, s)
                    rec = {"crater_id": a["crater_id"], "x": rnd(a["x"], 2), "y": rnd(a["y"], 2),
                           "d": rnd(a["d"], 2), "after_sortie": so.sid, "sightings": a["sightings"],
                           "bound95": rnd(a.get("bound95"), 2), "true_crater": sc["true_crater"],
                           "true_offset_m": sc["true_offset_m"], "misplaced": sc["misplaced"],
                           "scouts": a.get("scouts"), "sorties": a.get("sorties")}
                    self.added.append(rec)
                    self.map_rows.append([a["x"], a["y"], a["d"]])
                    self.events.append({"t": rnd(t_arr, 1), "sortie_id": so.sid, "scout": so.scout,
                                        "type": "crater_added", "x": rnd(a["x"], 2), "y": rnd(a["y"], 2),
                                        "crater_id": a["crater_id"]})
                self.pool = [p for i, p in enumerate(self.pool) if i not in drop]
                self.added_by_sortie[so.sid] = len(added)
                self._rebuild_map()
        self.map_size_after = len(self.map_rows)
        if t_arr is None:
            self._sortie_record(so)

    def _sortie_record(self, so: Sortie):
        rec = {"sortie_id": so.sid, "scout": so.scout, "n": so.n, "t_start": rnd(so.rows[0][0], 1),
               "t_end": rnd(so.t, 1), "outcome": so.outcome, "faults": so.faults,
               "time_s": {k: rnd(v, 1) for k, v in so.time.items()},
               "energy_end_kwh": rnd(so.energy, 4), "distance_m": rnd(so.d_total, 1),
               "checks": so.n_checks, "fixes": so.n_fixes, "wrong_matches": so.n_wrong,
               "survey_m": rnd(so.survey_used, 1), "map_craters": len(self.map_rows),
               "turn_back": so.turn_reason}
        rec.update(self._completion(so))
        self.sorties.append(rec)
        so.rec = rec
        return rec

    def _completion(self, so: Sortie) -> dict:
        """Did the sortie do its planned work (draft 4.1-4.2)?

        Planned work is survey_m of survey and drill_sites_max drill sites
        (brief p.6). `completed` means: home with the reserve; the survey is
        done (survey_m driven, or every lane it was given); every drill it
        was given is done; and the sortie, plus the drill time of any of its
        drill_sites_max sites it did not drill, fits in sortie_h and in the
        energy above the reserve. A sortie with nothing to drill is charged
        for the drill slots, so fixes that eat the stop budget show up even
        before any find is confirmed.
        """
        s = self.s
        back = getattr(so, "survey_back", 0.0)
        survey_done = so.survey_used + back >= s.survey_m - s.lane_segment_m or (
            set(so.planned_lanes) <= set(so.lanes_done))
        n_drill = len(so.drilled)
        slots = max(s.drill_sites_max - n_drill, 0)
        dur_h = (so.t - so.rows[0][0]) / 3600.0
        fits_time = dur_h + slots * s.drill_h <= s.sortie_h
        e_left = so.energy - slots * s.drill_h * self.energy["p_drill_w"] / 1000.0
        fits_energy = e_left >= s.reserve_frac * s.battery_kwh
        completed = (so.outcome == "ok" and survey_done and n_drill >= getattr(so, "planned_drills", 0)
                     and fits_time and fits_energy)
        return {"completed": bool(completed), "survey_done": bool(survey_done), "drills": n_drill,
                "drills_planned": getattr(so, "planned_drills", 0), "duration_h": rnd(dur_h, 3),
                "fits_time": bool(fits_time), "fits_energy": bool(fits_energy)}

    def _contact_gaps(self):
        s = self.s
        rng = rng_for(self.seed, 4)
        days = max(self.t_end, 1.0) / 86400.0
        n = int(rng.poisson(s.contact_gaps_per_month * days / 30.0))
        for _ in range(n):
            t0 = rng.uniform(0, self.t_end)
            dur = rng.uniform(0, s.contact_gap_max_days) * 86400.0
            for typ, tt in (("contact_lost", t0), ("contact_regained", min(t0 + dur, self.t_end))):
                self.events.append({"t": rnd(tt, 1), "sortie_id": None, "scout": None, "type": typ})

    # ------------------------------------------------------------ outputs
    def summary(self) -> dict:
        s = self.s
        sr = self.sorties
        ev = self.events
        drills = [e for e in ev if e["type"] == "drill"]
        ends = [e for e in ev if e["type"] == "search_end"]
        fixes = sum(r["fixes"] for r in sr)
        wrong = sum(r["wrong_matches"] for r in sr)
        # Deposits that a find landed in (by the finder's true position).
        hit = set()
        for fid, (x, y) in self.true_find_xy.items():
            for k, d in enumerate(self.world.ice):
                if math.hypot(x - d["x"], y - d["y"]) <= d["radius"]:
                    hit.add(k)
        covered = self._covered_cells()
        cov_set = set(map(tuple, covered.tolist())) if len(covered) else set()
        cell = self.world.cell
        in_area = [k for k, d in enumerate(self.world.ice)
                   if (int(round((d["x"] - self.world.x0) / cell)), int(round((d["y"] - self.world.y0) / cell))) in cov_set]
        row = {"run_id": self.run_id, "seed": self.seed}
        for k in SWEPT:
            row[k] = getattr(s, k)
        for k, v in self.world.draws.items():
            if k == "dock_reg_err":
                row["dock_reg_err_m"] = rnd(math.hypot(*v), 3)
            else:
                row[k] = rnd(v, 6) if isinstance(v, float) else v
        row.update({
            "sorties": len(sr),
            "sorties_ok": sum(r["outcome"] == "ok" for r in sr),
            "sorties_completed": sum(bool(r.get("completed")) for r in sr),
            "sorties_below_reserve": sum(r["outcome"] == "below_reserve" for r in sr),
            "sorties_fault_return": sum(r["outcome"] == "fault_return" for r in sr),
            "scouts_lost": sum(r["outcome"] == "lost" for r in sr),
            "drill_err_p95_m": rnd(np.percentile([e["true_err_m"] for e in drills], 95), 3) if drills else None,
            "checks_per_sortie": rnd(np.mean([r["checks"] for r in sr]), 3) if sr else None,
            "wrong_match_rate": rnd(wrong / fixes, 4) if fixes else None,
            "finds": len(self.finds),
            "confirmed": sum(e["result"] == "confirmed" for e in ends),
            "false_confirmed": sum(bool(e.get("false_confirmed")) for e in ends),
            "deposits_missed": sum(bool(e.get("missed")) for e in ends),
            "area_km2": rnd(len(cov_set) * cell * cell / 1e6, 4),
            "h_drive": rnd(sum(r["time_s"]["drive"] for r in sr) / 3600, 2),
            "h_check": rnd(sum(r["time_s"]["check"] for r in sr) / 3600, 2),
            "h_reading": rnd(sum(r["time_s"]["reading"] for r in sr) / 3600, 2),
            "h_drill": rnd(sum(r["time_s"]["drill"] for r in sr) / 3600, 2),
            "h_wait": rnd(sum(r["time_s"]["wait"] or 0 for r in sr) / 3600, 2),
            # Additive (not in the handoffs list yet).
            "fixes": fixes,
            "searches": len(ends),
            "confirm_rate": rnd(sum(e["result"] == "confirmed" for e in ends) / len(ends), 3) if ends else None,
            "search_bound_too_large": sum(e.get("reason") == "bound_too_large" for e in ends),
            "search_no_match": sum(e.get("reason") == "no_match" for e in ends),
            "search_no_readings": sum(e.get("reason") == "no_readings" for e in ends),
            "search_combined_bound_med_m": rnd(np.median([e["combined_bound95"] for e in ends]), 3) if ends else None,
            "sortie_h_mean": rnd(np.mean([r["duration_h"] for r in sr]), 3) if sr else None,
            "drills": len(drills),
            "drill_hit_rate": rnd(np.mean([e["hit"] for e in drills]), 3) if drills else None,
            "survey_err_p95_m": self._survey_err_p95(),
            "deposits_in_area": len(in_area),
            "deposits_unfound": sum(1 for k in in_area if k not in hit),
            "added_misplaced": sum(bool(a.get("misplaced")) for a in self.added),
            "craters_added": len(self.added),
            "campaign_days": rnd(self.t_end / 86400, 2),
        })
        return row

    def by_sortie(self) -> list:
        out = []
        for n in range(1, self.s.sorties_per_scout + 1):
            rs = [r for r in self.sorties if r["n"] == n]
            if not rs:
                continue
            out.append({"run_id": self.run_id, "sortie_n": n,
                        "checks": sum(r["checks"] for r in rs), "wrong_matches": sum(r["wrong_matches"] for r in rs),
                        "map_craters": max(r["map_craters"] for r in rs),
                        "fixes": sum(r["fixes"] for r in rs), "sorties": len(rs)})
        return out

    def _survey_err_p95(self):
        """95th percentile of the true position error over pose rows logged
        while surveying or searching: the error finds and drills inherit."""
        errs = []
        for rows in self.poses.values():
            a = np.asarray(rows, float)
            a = a[np.isin(a[:, 8], (ACT["survey"], ACT["search"]))]
            if len(a):
                errs.append(np.hypot(a[:, 1] - a[:, 4], a[:, 2] - a[:, 5]))
        return rnd(np.percentile(np.concatenate(errs), 95), 3) if errs else None

    def _covered_cells(self):
        """5 m cells within half a lane spacing of the true survey path."""
        pts = []
        for rows in self.poses.values():
            a = np.asarray(rows, float)
            a = a[np.isin(a[:, 8], (ACT["survey"], ACT["search"]))]
            if len(a):
                pts.append(a[:, 1:3])
        if not pts:
            return np.zeros((0, 2), int)
        p = np.concatenate(pts)
        cell = self.world.cell
        half = self.s.survey_line_spacing_m / 2
        r = int(math.ceil(half / cell))
        oi, oj = np.meshgrid(np.arange(-r, r + 1), np.arange(-r, r + 1))
        m = np.hypot(oi, oj) * cell <= half
        oi, oj = oi[m], oj[m]
        cj = np.round((p[:, 0] - self.world.x0) / cell).astype(int)
        ci = np.round((p[:, 1] - self.world.y0) / cell).astype(int)
        cells = np.stack([(cj[:, None] + oj[None, :]).ravel(), (ci[:, None] + oi[None, :]).ravel()], 1)
        return np.unique(cells, axis=0)

    @property
    def run_id(self):
        return f"w{self.seed:05d}-{self.s.key()}"

    def record(self) -> dict:
        s, w = self.s, self.world
        # Crater lists cut to what the Scouts could have seen: within
        # fix_range_m + 10 m of any logged true position.
        allp = np.concatenate([np.asarray(r, float)[:, 1:3] for r in self.poses.values()]) \
            if self.poses else np.zeros((0, 2))
        near = s.fix_range_m + 10.0
        ptree = cKDTree(allp) if len(allp) else None

        def cut(arr):
            if ptree is None or not len(arr):
                return []
            d, _ = ptree.query(arr[:, :2], distance_upper_bound=near)
            return [[rnd(x, 2), rnd(y, 2), rnd(dd, 2)] for x, y, dd in arr[np.isfinite(d)]]

        # truth.craters_missing: cut to the driven neighbourhood, always keeping
        # every crater an added one points at; true_crater is re-indexed into it.
        cm = self.craters_missing
        keep = np.zeros(len(cm), bool)
        if ptree is not None and len(cm):
            d, _ = ptree.query(cm[:, :2], distance_upper_bound=near)
            keep = np.isfinite(d)
        for a in self.added:
            if a["true_crater"] is not None:
                keep[a["true_crater"]] = True
        new_idx = -np.ones(len(cm), int)
        new_idx[keep] = np.arange(int(keep.sum()))
        added = [dict(a, true_crater=(int(new_idx[a["true_crater"]]) if a["true_crater"] is not None else None))
                 for a in self.added]
        missing_rec = [[rnd(x, 2), rnd(y, 2), rnd(dd, 2)] for x, y, dd in cm[keep]]
        standins = [w.standin] if w.standin else []
        finds = []
        for f in self.finds:
            g = {k: v for k, v in f.items() if not k.startswith("_")}
            finds.append(g)
        return {
            "format": FORMAT,
            "run_id": self.run_id, "seed": self.seed,
            "made_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "nav": "sampled",
            "settings": json.loads(json.dumps(asdict(s), default=list)),
            "world": {k: (rnd(v, 6) if isinstance(v, float) else v) for k, v in w.draws.items()},
            "world_ranges": json.loads(json.dumps(asdict(self.ranges), default=list)),
            "terrain": {"site": w.site, "file": w.file, "x0": w.x0, "y0": w.y0, "cell": w.cell,
                        "bounds": [rnd(b, 2) for b in w.bounds]},
            "dock": {"x": rnd(w.dock[0], 2), "y": rnd(w.dock[1], 2)},
            "corridor": [[rnd(x, 2), rnd(y, 2)] for x, y in self.corridor],
            "energy": {k: (rnd(v, 4) if isinstance(v, float) else v) for k, v in self.energy.items()},
            "scouts": self.scouts,
            "standins": standins,
            "crater_map": {"start": cut(w.map_start), "added": added,
                           "scope": f"start and truth.craters_missing list only craters within {near:g} m of a "
                                    f"driven path; the world holds {len(w.craters)} detectable craters"},
            "finds": finds,
            "sorties": sorted(self.sorties, key=lambda r: (r["t_start"], r["scout"])),
            "events": self.events,
            "truth": {"label": "hidden truth",
                      "ice": [{k: rnd(v, 3) for k, v in d.items()} for d in w.ice],
                      "craters_missing": missing_rec,
                      "ice_profile": "sim/ice.py: peak * (threshold/peak)^(r^2/R^2), summed over deposits"},
            "summary": self.summary(),
        }

    def save(self, batch_id: str, out_root: Path | None = None) -> Path:
        root = Path(out_root or CAMPAIGN_DIR) / batch_id / self.run_id
        root.mkdir(parents=True, exist_ok=True)
        rec = self.record()
        (root / "record.json").write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
        arrs = {sid: np.asarray(rows, np.float32).reshape(-1, len(POSE_COLS)) for sid, rows in self.poses.items()}
        np.savez_compressed(root / "poses.npz", **arrs)
        return root


# Design choices swept on purpose (spec sec. 4); every one goes in summary.csv.
SWEPT = ["site", "n_scouts", "check_policy", "map_refinement", "top_speed_mps", "check_stop_s", "drill_limit_m",
         "avg_load_w", "sightings_to_add", "drift_mode", "star_tracker",
         # assumed inputs swept for sensitivity (spec 4a item 3)
         "p_wrong_per_missing", "confirm_bound_max_m", "bound_model"]


def parse_value(field_type, text):
    if field_type in (bool, "bool"):
        return text.lower() in ("1", "true", "yes", "on")
    if field_type in (int, "int"):
        return int(text)
    if field_type in (float, "float"):
        return float(text)
    return text


def settings_from(pairs) -> Settings:
    types = {f.name: f.type for f in fields(Settings)}
    kw = {}
    for p in pairs or []:
        k, v = p.split("=", 1)
        if k not in types:
            raise SystemExit(f"unknown setting {k}")
        kw[k] = parse_value(types[k], v)
    return Settings(**kw)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--set", nargs="*", default=[], help="Settings overrides, name=value")
    ap.add_argument("--batch-id", default="single")
    ap.add_argument("--world-ranges", action="store_true",
                    help="draw drift from WorldRanges as a batch does (default: Settings.drift_frac)")
    a = ap.parse_args()
    s = settings_from(a.set)
    t0 = time.perf_counter()
    c = Campaign(a.seed, s, WorldRanges() if a.world_ranges else None).run()
    t_sim = time.perf_counter() - t0
    root = c.save(a.batch_id)
    t_all = time.perf_counter() - t0
    sm = c.summary()
    print(f"{c.run_id}: {len(c.sorties)} sorties, simulated in {t_sim:.1f} s ({t_all:.1f} s with writing) -> {root}")
    for k, v in sm.items():
        print(f"  {k:24} {v}")


if __name__ == "__main__":
    main()
