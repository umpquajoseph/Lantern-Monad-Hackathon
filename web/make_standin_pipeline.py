"""Stand-in steps.npz for Mission Control, until the camera-vision engine writes real ones.

A camera run's record carries steps.npz beside poses.npz: what each Scout's
pipeline produced at every step (docs/handoffs.md, "Camera navigation",
worker live-nav). Mission Control draws the first-person box, rim points,
particle cloud, planned route and lidar views from it. No camera run exists
yet, so this script writes a labelled stand-in in exactly that format beside
copies of the stand-in campaign records, and the viewer reads it through the
same path as a real one. Nothing here comes from a camera, a rim detector or
a particle filter:

- step: one per pose row the Scout drove (not every 2 m step), from poses.npz.
- rims: points on the near half arc of every true crater rim 3-15 m ahead in
  the camera's 60 deg view of the true pose; rim_map is the starting-map row
  when the crater is on that map, else -1; rim_wrong is set at the record's
  own check events that say wrong_match.
- particles: 100 drawn round the logged estimate with the logged 95% bound,
  Gaussian-weighted.
- lidar: the range image ray-marched over the heights at sim/lidar.py's
  angles (every 2nd beam, as live-nav stores it), with no noise.
- haz: sim/lidar.py's 0.5 m cells judged from the heights directly (no
  occlusion): steeper than 15 deg over 2 m, or more than 0.35 m below the
  3 m ring round the cell.
- plan: the next 20 m of the true track.

    python web/make_standin_pipeline.py                        # w00011-3aa58bbf and w00012-4b73cd25
    python web/make_standin_pipeline.py --run w00011-8e26fe71  # one more

The source batch (web/make_standin_campaign.py's `standin`) is left as it
is: records and summaries are copied to <DATA>/campaigns/mission-standin/,
and steps.npz is written there. The file holds a `standin` string saying all
this; the viewer shows it.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.terrain import DATA, TERRAIN_DIR, load_terrain  # noqa: E402
from sim.settings import Settings  # noqa: E402

STANDIN = ("Stand-in steps.npz from web/make_standin_pipeline.py, not from the camera pipeline: rim points are "
           "placed from the true craters, particles drawn round the logged estimate, the lidar ray-marched over the "
           "heights and its hazards judged from them directly. One step per 10 m pose row.")

# Copied from their sources, not re-derived.
CAM_HALF_FOV = math.radians(30.0)        # sim/scene.py HFOV_DEG = 60 (Atha et al. 2024 sec. IV)
RIM_RANGE_M = (3.0, 15.0)                # docs/handoffs.md "Steps 3 and 6 rim points": visible 3-15 m
CAMERA_H_M = Settings().camera_height_m  # sim/settings.py (brief p.6, 1.6 m mast); sim/lidar.py LIDAR_HEIGHT_M
LIDAR_RIGHT_M = 0.25                     # sim/lidar.py LIDAR_RIGHT_OF_CAMERA_M
LIDAR_AZ_DEG = np.arange(-60.0, 60.0 + 1e-9, 0.5)[::2]       # sim/lidar.py H_FOV_DEG 120, H_STEP_DEG 0.5; every 2nd (live-nav LIDAR_KEEP)
LIDAR_EL_DEG = np.linspace(-35.0, -2.0, 40)[::2]             # sim/lidar.py V_ANGLES_DEG; every 2nd
LIDAR_RANGE_M = (1.0, 30.0)              # sim/lidar.py MIN_RANGE_M, MAX_RANGE_M
HAZ_CELL_M = 0.5                         # sim/lidar.py HAZ_CELL_M
HAZ_FWD_M = (2.0, 25.0)                  # sim/lidar.py HAZ_RANGE_M
HAZ_HALF_W_M = 15.0                      # sim/lidar.py HAZ_HALF_WIDTH_M
SLOPE_LIMIT_DEG = 15.0                   # sim/lidar.py SLOPE_LIMIT_DEG
RING_M = 3.0                             # sim/lidar.py RING_CELLS * HAZ_CELL_M
DEPRESSION_M = 0.35                      # sim/lidar.py DEPRESSION_M
N_PARTICLES = 100                        # docs/handoffs.md steps.npz particles (K, 100, 3)
K95 = math.sqrt(-2.0 * math.log(0.05))   # 2.448: 95% radius of a circular Gaussian over its per-axis sigma

# STAND-IN constants: this script's choices, used nowhere else.
STANDIN_PLAN_M = 20.0
STANDIN_RIM_NOISE_M = 0.10
STANDIN_RIM_STEP_DEG = 12.0              # one rim point per this much arc
STANDIN_MARCH_M = 0.5                    # ray-march step for the stand-in range image
DEFAULT_RUNS = ["w00011-3aa58bbf", "w00012-4b73cd25"]


class Heights:
    """The fine 0.25 m grid where it exists, else the viewer's 5 m LOLA near backdrop."""

    def __init__(self, rec):
        t = rec["terrain"]
        site = t.get("site") or "shoemaker"
        z, _, x0, y0, cell = load_terrain(TERRAIN_DIR / t["file"])
        self.fine = (z.astype(np.float32), x0, y0, cell)
        base = ROOT / "web" / "assets" / "terrain"
        if site != "shoemaker" and (base / site / "meta.json").exists():
            base = base / site
        m = json.loads((base / "meta.json").read_text(encoding="utf8"))
        b = m.get("backdrop_near")
        self.near = None
        if b:
            q = np.frombuffer(gzip.decompress((base / b["file"]).read_bytes()), "<u2").reshape(b["rows"], b["cols"])
            self.near = (b["hmin"] + q.astype(np.float32) * b["hscale"], b["x0"], b["y0"], b["cell"])

    @staticmethod
    def _bilinear(g, x, y):
        z, x0, y0, cell = g
        fj = np.clip((x - x0) / cell, 0, z.shape[1] - 1.001)
        fi = np.clip((y - y0) / cell, 0, z.shape[0] - 1.001)
        j, i = np.floor(fj).astype(int), np.floor(fi).astype(int)
        u, v = fj - j, fi - i
        return (z[i, j] * (1 - u) + z[i, j + 1] * u) * (1 - v) + (z[i + 1, j] * (1 - u) + z[i + 1, j + 1] * u) * v

    def at(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        z, x0, y0, cell = self.fine
        inside = (x >= x0) & (y >= y0) & (x <= x0 + (z.shape[1] - 1) * cell) & (y <= y0 + (z.shape[0] - 1) * cell)
        out = self._bilinear(self.fine, x, y)
        if self.near is not None and not inside.all():
            out = np.where(inside, out, self._bilinear(self.near, x, y))
        return out


# sim/lidar.py's hazard grid, rover frame: cell (i, j) centred at fwd, left below.
_f = HAZ_FWD_M[0] + (np.arange(int(round((HAZ_FWD_M[1] - HAZ_FWD_M[0]) / HAZ_CELL_M))) + 0.5) * HAZ_CELL_M
_l = -HAZ_HALF_W_M + (np.arange(int(round(2 * HAZ_HALF_W_M / HAZ_CELL_M))) + 0.5) * HAZ_CELL_M
FWD, LEFT = (a.ravel() for a in np.meshgrid(_f, _l, indexing="ij"))
IN_WEDGE = (np.hypot(FWD, LEFT) <= HAZ_FWD_M[1]) & (np.abs(np.arctan2(LEFT, FWD)) <= math.radians(60.0))
FWD, LEFT = FWD[IN_WEDGE], LEFT[IN_WEDGE]
RING = [(RING_M * math.cos(a), RING_M * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
EL, AZ = np.meshgrid(np.radians(LIDAR_EL_DEG), np.radians(LIDAR_AZ_DEG), indexing="ij")
MARCH = np.arange(LIDAR_RANGE_M[0] + STANDIN_MARCH_M, LIDAR_RANGE_M[1] + 1e-9, STANDIN_MARCH_M)


def hazards(H, x, y, h):
    """Hazard cell centres [fwd, left] and kinds (0 crater floor, 1 slope), as docs/handoffs.md steps.npz haz."""
    c, s = math.cos(h), math.sin(h)
    wx, wy = x + FWD * c - LEFT * s, y + FWD * s + LEFT * c
    z = H.at(wx, wy)
    d = 1.0   # 2 m baseline, as sim/rover.py SlopeMap
    gx = (H.at(wx + d, wy) - H.at(wx - d, wy)) / (2 * d)
    gy = (H.at(wx, wy + d) - H.at(wx, wy - d)) / (2 * d)
    steep = np.degrees(np.arctan(np.hypot(gx, gy))) > SLOPE_LIMIT_DEG
    ring = np.mean([H.at(wx + dx, wy + dy) for dx, dy in RING], axis=0)
    dip = (z < ring - DEPRESSION_M) & ~steep
    keep = steep | dip
    return np.column_stack([FWD[keep], LEFT[keep]]), np.where(steep[keep], 1, 0).astype(np.uint8)


def range_image(H, x, y, h):
    """(20, 121) uint16 centimetres, 0 = no return, as live-nav stores sim/lidar.py's scan."""
    ox, oy = x + LIDAR_RIGHT_M * math.sin(h), y - LIDAR_RIGHT_M * math.cos(h)
    oz = float(H.at(x, y)) + CAMERA_H_M
    dx, dy, dz = (np.cos(EL) * np.cos(AZ + h)).ravel(), (np.cos(EL) * np.sin(AZ + h)).ravel(), np.sin(EL).ravel()
    px, py = ox + dx[:, None] * MARCH, oy + dy[:, None] * MARCH
    below = oz + dz[:, None] * MARCH <= H.at(px, py)
    got = below.any(axis=1)
    r = np.where(got, MARCH[np.argmax(below, axis=1)], 0.0)
    return np.where(got, np.round(r * 100), 0).astype(np.uint16).reshape(EL.shape)


def rim_points(craters, x, y, h, rng):
    """Near-arc rim points of craters 3-15 m ahead in the camera's view: (rims [fwd, left], crater row)."""
    c, s = math.cos(h), math.sin(h)
    pts, rows = [], []
    near = np.nonzero(np.hypot(craters[:, 0] - x, craters[:, 1] - y) < RIM_RANGE_M[1] + craters[:, 2] / 2)[0]
    for n in near:
        cx, cy, dd = craters[n]
        r = dd / 2
        toward = math.atan2(y - cy, x - cx)          # the half arc facing the Scout (Cauligi 2023 sec. 5A: near rim)
        for a in np.radians(np.arange(-90, 90.01, STANDIN_RIM_STEP_DEG)):
            ex, ey = cx + r * math.cos(toward + a) - x, cy + r * math.sin(toward + a) - y
            f, l = ex * c + ey * s, -ex * s + ey * c
            if not (RIM_RANGE_M[0] <= math.hypot(f, l) <= RIM_RANGE_M[1]) or abs(math.atan2(l, f)) > CAM_HALF_FOV:
                continue
            pts.append([f + rng.normal(0, STANDIN_RIM_NOISE_M), l + rng.normal(0, STANDIN_RIM_NOISE_M)])
            rows.append(n)
    return np.asarray(pts, float).reshape(-1, 2), np.asarray(rows, int)


def build(run_dir: Path, say=print):
    rec = json.loads((run_dir / "record.json").read_text(encoding="utf8"))
    poses = np.load(run_dir / "poses.npz")
    H = Heights(rec)
    start = np.asarray(rec["crater_map"]["start"], float).reshape(-1, 3)
    missing = np.asarray(rec["truth"]["craters_missing"], float).reshape(-1, 3)
    true = np.vstack([start, missing])      # every true crater: the map's plus the ones it misses
    wrong_at = {}
    for e in rec["events"]:
        if e["type"] == "check" and e.get("wrong_match"):
            wrong_at.setdefault(e["sortie_id"], []).append(e["t"])
    used_map, used_true, out = {}, {}, {}
    for so in rec["sorties"]:
        sid = so["sortie_id"]
        rows = poses[sid]
        rng = np.random.default_rng([rec["seed"], sum(map(ord, sid))])
        drive = [i for i, r in enumerate(rows) if int(r[8]) not in (7, 8)]   # not docked or waiting
        st, parts, rims, rmap, rtrue, rwrong, lid, haz, hk, plan = [], [], [], [], [], [], [], [], [], []
        o = {"rim": [0], "haz": [0], "plan": [0]}
        for i in drive:
            t, x, y, h, ex, ey, b95, en, act = (float(v) for v in rows[i])
            nxt = rows[min(i + 1, len(rows) - 1)]
            st.append([t, x, y, h, ex, ey, h, b95, en, act, nxt[4], nxt[5]])
            sig = max(b95, 0.05) / K95
            d = rng.normal(0, sig, size=(N_PARTICLES, 2))
            w = np.exp(-0.5 * np.sum(d ** 2, axis=1) / sig ** 2)
            parts.append(np.column_stack([d, w / w.sum()]))
            p, n = rim_points(true, x, y, h, rng)
            wrong = any(abs(t - tw) < 1.0 for tw in wrong_at.get(sid, []))
            rims.append(p)
            rmap.append([used_map.setdefault(int(k), len(used_map)) if k < len(start) else -1 for k in n])
            rtrue.append([used_true.setdefault(int(k), len(used_true)) for k in n])
            rwrong.append([1 if wrong and k < len(start) else 0 for k in n])
            o["rim"].append(o["rim"][-1] + len(p))
            lid.append(range_image(H, x, y, h))
            hz, kinds = hazards(H, x, y, h)
            haz.append(hz); hk.append(kinds)
            o["haz"].append(o["haz"][-1] + len(hz))
            pl, dist = [[x, y]], 0.0
            for j in range(i + 1, len(rows)):
                dist += math.hypot(rows[j, 1] - rows[j - 1, 1], rows[j, 2] - rows[j - 1, 2])
                pl.append([rows[j, 1], rows[j, 2]])
                if dist >= STANDIN_PLAN_M:
                    break
            plan.append(np.asarray(pl, float))
            o["plan"].append(o["plan"][-1] + len(pl))
        cat = lambda L, dt, cols: np.concatenate([np.asarray(a, dt).reshape((-1,) + cols) for a in L]) if L else np.zeros((0,) + cols, dt)
        out.update({
            f"{sid}.step": np.asarray(st, np.float32).reshape(-1, 12),
            f"{sid}.particles": np.stack(parts).astype(np.float16) if parts else np.zeros((0, N_PARTICLES, 3), np.float16),
            f"{sid}.rim_o": np.asarray(o["rim"], np.int32),
            f"{sid}.rims": cat(rims, np.float16, (2,)),
            f"{sid}.rim_map": cat(rmap, np.int32, ()),
            f"{sid}.rim_true": cat(rtrue, np.int32, ()),
            f"{sid}.rim_wrong": cat(rwrong, np.uint8, ()),
            f"{sid}.lidar": np.stack(lid) if lid else np.zeros((0,) + EL.shape, np.uint16),
            f"{sid}.haz_o": np.asarray(o["haz"], np.int32),
            f"{sid}.haz": cat(haz, np.float16, (2,)),
            f"{sid}.haz_kind": cat(hk, np.uint8, ()),
            f"{sid}.plan_o": np.asarray(o["plan"], np.int32),
            f"{sid}.plan": cat(plan, np.float32, (2,)),
        })
        say(f"{rec['run_id']} {sid}: {len(st)} steps, {o['rim'][-1]} rim points, {o['haz'][-1]} hazard cells")
    mrows = sorted(used_map, key=used_map.get)
    trows = sorted(used_true, key=used_true.get)
    np.savez_compressed(run_dir / "steps.npz", **out,
                        map_craters=start[mrows].astype(np.float32).reshape(-1, 3),
                        true_craters=true[trows].astype(np.float32).reshape(-1, 3),
                        lidar_el_deg=LIDAR_EL_DEG.astype(np.float32), lidar_az_deg=LIDAR_AZ_DEG.astype(np.float32),
                        standin=np.array(STANDIN))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", default="standin", help="batch to copy from")
    ap.add_argument("--dst", default="mission-standin", help="batch to write")
    ap.add_argument("--run", action="append", help="run id (repeatable); default: two runs")
    a = ap.parse_args()
    src, dst = DATA / "campaigns" / a.src, DATA / "campaigns" / a.dst
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("summary.csv", "by_sortie.csv"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
    for d in sorted(p for p in src.iterdir() if (p / "record.json").exists()):   # every record, so the batch lists them all
        (dst / d.name).mkdir(exist_ok=True)
        for f in ("record.json", "poses.npz"):
            shutil.copy2(d / f, dst / d.name / f)
    for rid in a.run or DEFAULT_RUNS:
        build(dst / rid)


if __name__ == "__main__":
    main()
