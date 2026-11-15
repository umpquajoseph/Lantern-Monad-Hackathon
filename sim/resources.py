"""Resources on the terrain, and the science targets the rovers drive to.

Two layers, kept apart on purpose:

  deposits  HIDDEN TRUTH. What is really under the ground: water ice buried
            under dry regolith, and patches of ilmenite-rich regolith. No rover
            may read these. Only science.py's drill sample touches them, at
            the rover's TRUE position, and the viewer may show them only as a
            labelled "hidden truth" reveal.
  targets   WHAT THE ROVERS KNOW. Candidate sites as an orbital survey would
            flag them (PROSPECT layer 0: "use existing LRO data and imagery to
            pick targets", brief p. 2). Each target sits near a deposit but
            not on it, and some targets are decoys with nothing there, so a
            drill can come back dry. The planner paths to targets, never to
            deposits.

Planner interface (for step 4, the rover loop):

    from sim.resources import load_site
    deposits, board = load_site()
    t = board.nearest(x, y)                 # dict, or None when none are open
    board.claim(t["id"], "A")               # so the other rover skips it
    ...drive to (t["x"], t["y"])...
    if board.arrived(t["id"], x, y): ...    # within t["radius"] of the target
    ...science.run_science(...)...          # then board.complete(t["id"], record_id)

    board.add_check_target(find)            # another agency's find becomes a
                                            # target, so a rover can go check it

Usage:
    python sim/resources.py build   # -> data/resources/site.json
    python sim/resources.py look    # -> data/resources/site.png

Sources:
  [PB] PROSPECT Team Brief, RASC-AL 2027 Theme 3, 13 Oct 2026
       (docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf)
Every other number is a named parameter marked ASSUMED.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import terrain  # noqa: E402  (Joseph's step 2; read only)

# Inputs come from LANTERN_DATA (lantern_data/, committed and shared).
# Everything this code writes goes to LANTERN_OUT, default the git-ignored
# data/ folder, so a run never writes into the shared lantern_data/.
import os  # noqa: E402
OUT = Path(os.environ.get("LANTERN_OUT", Path(__file__).resolve().parent.parent / "data"))
RESOURCE_DIR = OUT / "resources"
SITE_PATH = RESOURCE_DIR / "site.json"

KINDS = ("water_ice", "ilmenite")

# [PB] p. 5: the neutron spectrometer senses hydrogen "down to ~1 m", and the
# Scout carries a "1 m drill + mass spectrometer".
DRILL_DEPTH_M = 1.0
# [PB] p. 12: LCROSS measured "~5.6 % water by mass at Cabeus". Used as the
# ceiling for deposit peaks, so no simulated deposit is richer than that.
ICE_PEAK_MAX_WT_PCT = 5.6
# [PB] handoffs.md step 2: the planner's slope limit is 15 deg over a 2 m
# baseline (the 15 deg itself is VIPER's, [PB] p. 7).
SLOPE_LIMIT_DEG = 15.0
SLOPE_BASELINE_M = 2.0


@dataclass
class Params:
    seed: int = 11
    # ASSUMED. How many deposits of each kind sit in the 1 km world.
    n_ice: int = 6
    n_ilmenite: int = 3
    # ASSUMED. Decoy targets: orbital candidates with nothing under them.
    n_decoys: int = 3
    # ASSUMED. Deposit footprint radius (1-sigma of a Gaussian), metres.
    radius_m: tuple = (6.0, 14.0)
    # ASSUMED. Ice peak in wt% water, capped by ICE_PEAK_MAX_WT_PCT.
    ice_peak_wt_pct: tuple = (1.5, ICE_PEAK_MAX_WT_PCT)
    # ASSUMED. Dry regolith over the ice ("dry overburden", [PB] p. 5), metres.
    overburden_m: tuple = (0.1, 0.6)
    # ASSUMED. Ilmenite in ilmenite-rich regolith, wt%, and the background.
    ilmenite_peak_wt_pct: tuple = (8.0, 15.0)
    ilmenite_background_wt_pct: float = 2.0
    # ASSUMED. Orbital candidates are this far (max) from the deposit centre.
    target_offset_m: float = 6.0
    # ASSUMED. A rover has "arrived" when within this distance of a target.
    target_radius_m: float = 3.0
    # ASSUMED. Keep deposits this far from each other and from the map edge.
    min_sep_m: float = 60.0
    edge_margin_m: float = 60.0
    # ASSUMED. A deposit centre is not placed within this distance of a
    # crater rim, so the drill site is on open, drillable ground.
    crater_clear_m: float = 2.0


@dataclass
class Deposit:
    id: str
    kind: str              # "water_ice" | "ilmenite"
    x: float
    y: float
    radius: float          # 1-sigma footprint, metres
    peak: float            # wt% at the centre (water, or ilmenite)
    overburden: float = 0.0  # metres of dry regolith on top (ice only)


# --------------------------------------------------------------------------
# Hidden truth: what a drill sample at (x, y) really contains.
# --------------------------------------------------------------------------

def concentration(deposits, kind, x, y):
    """True wt% of `kind` at world (x, y): the strongest deposit's Gaussian.

    For ice this is the concentration inside the ice layer, below the
    overburden. science.py works out how much of the drill core that layer is.
    """
    best, over = 0.0, 0.0
    for d in deposits:
        if d.kind != kind:
            continue
        r2 = (x - d.x) ** 2 + (y - d.y) ** 2
        c = d.peak * math.exp(-0.5 * r2 / d.radius ** 2)
        if c > best:
            best, over = c, d.overburden
    return best, over


# ASSUMED. Neutron spectrometer survey while driving: footprint radius on the
# ground and 1-sigma noise, wt% water-equivalent hydrogen.
NEUTRON_FOOTPRINT_M = 1.0
NEUTRON_NOISE_WT_PCT = 0.25


def neutron_reading(deposits, x, y, rng):
    """What the driving survey sees at TRUE (x, y): hydrogen averaged over the
    top DRILL_DEPTH_M ([PB] p. 5: "Hydrogen (water) down to ~1 m"), so ice
    under thick overburden reads weaker, plus noise. Weaker and noisier than
    a drill core by design: it finds candidates, the drill confirms them."""
    a = np.linspace(0, 2 * np.pi, 6, endpoint=False)
    xs = np.concatenate([[x], x + NEUTRON_FOOTPRINT_M * np.cos(a)])
    ys = np.concatenate([[y], y + NEUTRON_FOOTPRINT_M * np.sin(a)])
    vals = []
    for px, py in zip(xs, ys):
        c, over = concentration(deposits, "water_ice", px, py)
        vals.append(c * max(0.0, DRILL_DEPTH_M - over) / DRILL_DEPTH_M)
    return max(0.0, float(np.mean(vals) + rng.normal(0, NEUTRON_NOISE_WT_PCT)))


# ASSUMED. Regolith classes by measured ilmenite wt%. [PB] p. 5 names
# ilmenite content as what decides "which regolith is best for oxygen".
REGOLITH_CLASSES = ((8.0, "ilmenite-rich: oxygen feedstock candidate"),
                    (4.0, "moderate ilmenite"),
                    (0.0, "ilmenite-poor: typical highland regolith"))


def classify_regolith(ilmenite_wt_pct):
    for lo, name in REGOLITH_CLASSES:
        if ilmenite_wt_pct >= lo:
            return name
    return REGOLITH_CLASSES[-1][1]


def as_truth(deposits):
    """The run log's `truth` field (docs/handoffs.md, Run log): ice as
    {x, y, radius, peak}, plus the other resources under their own key."""
    ice = [dict(x=d.x, y=d.y, radius=d.radius, peak=d.peak, overburden=d.overburden)
           for d in deposits if d.kind == "water_ice"]
    other = [dict(kind=d.kind, x=d.x, y=d.y, radius=d.radius, peak=d.peak)
             for d in deposits if d.kind != "water_ice"]
    return {"ice": ice, "resources": other}


# --------------------------------------------------------------------------
# Targets: what the rovers are allowed to know.
# --------------------------------------------------------------------------

@dataclass
class TargetBoard:
    targets: list = field(default_factory=list)

    def open(self, kind=None):
        return [t for t in self.targets
                if t["status"] == "open" and (kind is None or t["kind"] == kind)]

    def get(self, target_id):
        for t in self.targets:
            if t["id"] == target_id:
                return t
        raise KeyError(target_id)

    def nearest(self, x, y, kind=None, agency=None):
        """Nearest open target by straight-line distance, or None.

        Straight-line is only for choosing; the planner finds the real route.
        With `agency`, targets that agency may not take are skipped: its own
        finds (a rover cannot check its own agency's find, spec "Contract
        functions").
        """
        cands = [t for t in self.open(kind)
                 if agency is None or t.get("finder_agency") != agency]
        if not cands:
            return None
        return min(cands, key=lambda t: (t["x"] - x) ** 2 + (t["y"] - y) ** 2)

    def claim(self, target_id, agency):
        t = self.get(target_id)
        if t["status"] != "open":
            raise ValueError(f"target {target_id} is {t['status']}, not open")
        t["status"], t["claimed_by"] = "claimed", agency
        return t

    def release(self, target_id):
        t = self.get(target_id)
        t["status"], t["claimed_by"] = "open", None
        return t

    def arrived(self, target_id, x, y):
        t = self.get(target_id)
        return math.hypot(x - t["x"], y - t["y"]) <= t["radius"]

    def complete(self, target_id, record_id):
        t = self.get(target_id)
        t["status"], t["record_id"] = "sampled", record_id
        return t

    def add_check_target(self, find):
        """Turn another agency's find (a get_finds() dict) into a target.

        The target sits where the finder SAYS the ice is. Its radius is the
        chain's check rule distance for a checker with zero uncertainty
        (3 x sigma, docs/handoffs.md step 1), so arriving inside it is
        necessary for the check to be accepted, not sufficient.
        """
        tid = f"check-{find['id']}"
        if any(t["id"] == tid for t in self.targets):
            return self.get(tid)
        t = dict(id=tid, kind="water_ice", purpose="check", x=float(find["x"]),
                 y=float(find["y"]), radius=3.0 * float(find["sigma"]),
                 find_id=find["id"], finder_agency=find["agency"],
                 source="chain find", status="open", claimed_by=None, record_id=None,
                 # Evidence refs (sim/evidence.py): the finder's report, and
                 # its completed route, which the checker may reuse.
                 report_cid=find.get("report_cid"), route_cid=find.get("route_cid"))
        self.targets.append(t)
        return t

    def near(self, x, y, r):
        """Any target, of any status, within r metres of (x, y)."""
        return [t for t in self.targets if math.hypot(t["x"] - x, t["y"] - y) <= r]

    def add_survey_target(self, x, y, reading, agency, t, radius=3.0):
        """A rover's own neutron survey flagged hydrogen here: a new target
        the rover found itself, not one from the orbital list."""
        n = sum(1 for q in self.targets if q["id"].startswith("S")) + 1
        tgt = dict(id=f"S{n:02d}", kind="water_ice", purpose="survey", x=float(x), y=float(y),
                   radius=radius, source=f"onboard neutron survey by {agency}",
                   survey_reading=round(float(reading), 3), found_t=round(float(t), 1),
                   status="open", claimed_by=None, record_id=None)
        self.targets.append(tgt)
        return tgt

    def to_list(self):
        return [dict(t) for t in self.targets]


# --------------------------------------------------------------------------
# Building a site on the terrain.
# --------------------------------------------------------------------------

def _slope_ok_mask(height, cell):
    """True where slope over SLOPE_BASELINE_M is under SLOPE_LIMIT_DEG."""
    k = max(1, int(round(SLOPE_BASELINE_M / cell)))
    h = height[::k, ::k]
    gy, gx = np.gradient(h, cell * k)
    return np.degrees(np.arctan(np.hypot(gx, gy))) < SLOPE_LIMIT_DEG, k


def build_site(height, craters, x0, y0, cell, p: Params | None = None):
    p = p or Params()
    rng = np.random.default_rng(p.seed)
    ok, k = _slope_ok_mask(height, cell)
    size = height.shape[0] * cell
    xmin, ymin = x0 + p.edge_margin_m, y0 + p.edge_margin_m
    xmax, ymax = x0 + size - p.edge_margin_m, y0 + size - p.edge_margin_m

    def good(x, y, placed):
        i, j = int((y - y0) / (cell * k)), int((x - x0) / (cell * k))
        if not ok[i, j]:
            return False
        d_rim = np.hypot(craters[:, 0] - x, craters[:, 1] - y) - craters[:, 2] / 2
        if np.any(d_rim < p.crater_clear_m):
            return False
        return all(math.hypot(x - q[0], y - q[1]) >= p.min_sep_m for q in placed)

    def place(n, placed):
        out = []
        for _ in range(20000):
            if len(out) == n:
                break
            x, y = rng.uniform(xmin, xmax), rng.uniform(ymin, ymax)
            if good(x, y, placed + out):
                out.append((x, y))
        if len(out) < n:
            raise RuntimeError(f"placed only {len(out)} of {n} sites; loosen Params")
        return out

    placed = []
    deposits = []
    for kind, n in (("water_ice", p.n_ice), ("ilmenite", p.n_ilmenite)):
        for (x, y) in place(n, placed):
            placed.append((x, y))
            r = rng.uniform(*p.radius_m)
            if kind == "water_ice":
                peak = rng.uniform(*p.ice_peak_wt_pct)
                over = rng.uniform(*p.overburden_m)
            else:
                peak, over = rng.uniform(*p.ilmenite_peak_wt_pct), 0.0
            deposits.append(Deposit(f"{kind[:3]}-{len(deposits) + 1:02d}", kind,
                                    float(x), float(y), float(r), float(peak), float(over)))

    targets = []
    for d in deposits:
        ang, dist = rng.uniform(0, 2 * math.pi), rng.uniform(0, p.target_offset_m)
        targets.append(dict(
            id=f"T{len(targets) + 1:02d}", kind=d.kind, purpose="survey",
            x=float(d.x + dist * math.cos(ang)), y=float(d.y + dist * math.sin(ang)),
            radius=p.target_radius_m, source="orbital candidate (simulated)",
            status="open", claimed_by=None, record_id=None))
    for (x, y) in place(p.n_decoys, placed):
        placed.append((x, y))
        targets.append(dict(
            id=f"T{len(targets) + 1:02d}", kind=str(rng.choice(KINDS)), purpose="survey",
            x=float(x), y=float(y), radius=p.target_radius_m,
            source="orbital candidate (simulated)", status="open",
            claimed_by=None, record_id=None))
    # Shuffle so decoys are not always the last targets.
    order = rng.permutation(len(targets))
    targets = [targets[i] for i in order]
    for n, t in enumerate(targets):
        t["id"] = f"T{n + 1:02d}"
    return deposits, TargetBoard(targets)


def save_site(deposits, board, terrain_file, p: Params, path=SITE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "terrain_file": Path(terrain_file).name,
        "params": asdict(p),
        "HIDDEN_TRUTH_deposits": [asdict(d) for d in deposits],
        "targets": board.to_list(),
    }
    path.write_text(json.dumps(doc, indent=1))
    return path


def load_site(path=SITE_PATH):
    doc = json.loads(Path(path).read_text())
    deposits = [Deposit(**d) for d in doc["HIDDEN_TRUTH_deposits"]]
    return deposits, TargetBoard(doc["targets"])


# --------------------------------------------------------------------------
# Picture.
# --------------------------------------------------------------------------

def look(path=SITE_PATH, terrain_path=None, out=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    height, craters, x0, y0, cell = terrain.load_terrain(terrain_path)
    deposits, board = load_site(path)
    s = 8
    h = height[::s, ::s]
    gy, gx = np.gradient(h, cell * s)
    shade = np.clip(0.6 - 0.4 * gx - 0.2 * gy, 0, 1)
    ext = (x0, x0 + height.shape[1] * cell, y0, y0 + height.shape[0] * cell)
    fig, ax = plt.subplots(figsize=(9, 9), dpi=110)
    ax.imshow(shade, origin="lower", extent=ext, cmap="gray", vmin=0, vmax=1)
    colors = {"water_ice": "#3aa0ff", "ilmenite": "#e08a2c"}
    for d in deposits:
        ax.add_patch(Circle((d.x, d.y), 2 * d.radius, color=colors[d.kind], alpha=0.35, lw=0))
    for t in board.targets:
        ax.plot(t["x"], t["y"], "+", color=colors[t["kind"]], ms=12, mew=2)
        ax.annotate(t["id"], (t["x"], t["y"]), xytext=(6, 6), textcoords="offset points",
                    color="white", fontsize=8)
    ax.set_title("Hidden truth (filled: ice blue, ilmenite orange, 2-sigma)\n"
                 "and targets (+), which is all a rover knows")
    ax.set_xlabel("x, m (east)")
    ax.set_ylabel("y, m (north)")
    out = Path(out) if out else Path(path).with_suffix(".png")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--terrain")
    b.add_argument("--seed", type=int, default=Params.seed)
    sub.add_parser("look").add_argument("--terrain")
    a = ap.parse_args()
    tpath = a.terrain or terrain.default_path()
    if a.cmd == "build":
        p = Params(seed=a.seed)
        deposits, board = build_site(*terrain.load_terrain(tpath), p=p)
        out = save_site(deposits, board, tpath, p)
        print(f"wrote {out}: {len(deposits)} deposits, {len(board.targets)} targets")
        print(f"wrote {look(out, tpath)}")
    else:
        print(f"wrote {look(SITE_PATH, tpath)}")


if __name__ == "__main__":
    main()
