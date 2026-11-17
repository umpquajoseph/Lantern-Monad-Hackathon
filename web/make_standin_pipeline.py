"""Stand-in per-step pipeline data for Mission Control, until the camera-vision engine logs its own.

Mission Control draws the first-person box, the rim points, the particle
cloud, the planned route and the lidar views from what each Scout's
pipeline produced at each step. Campaign records do not carry that yet
(worker live-nav adds it to docs/handoffs.md "Run record"). Until then this
script writes a labelled stand-in beside a record, one file per sortie:

    <run>/pipeline/<sortie_id>.json

in the shape web/js/campaign.js `loadPipeline` reads (docs/handoffs.md,
"Part 4 reads", "Per-step pipeline, stand-in"). Nothing here comes from a
camera, a rim detector or a particle filter:

- rims: points on the near arc of every true crater rim 3-15 m ahead in a
  60 deg view of the true pose (sim/scene.py's camera), matched to the
  record's starting map when the crater is on it; marked wrong when the
  record's own check event at that step says wrong_match.
- particles: STANDIN_PARTICLES points drawn round the logged estimate with
  the logged 95% bound, Gaussian-weighted.
- plan: the next STANDIN_PLAN_M of the true track.
- lidar: hazard cells of sim/lidar.py's grid (0.5 m cells, 2-25 m ahead,
  15 m each side, 120 deg) judged from the heights directly, with no ray
  casting and so no occlusion: steeper than 15 deg over 2 m, or more than
  0.35 m below the 3 m ring round the cell (sim/lidar.py's rules).

    python web/make_standin_pipeline.py                  # copy the standin batch to mission-standin, add pipeline/
    python web/make_standin_pipeline.py --run w00011-3aa58bbf

The source batch (web/make_standin_campaign.py's `standin`) is left as it is:
its records and summary are copied to <DATA>/campaigns/mission-standin/ and
the pipeline files are written there.
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

STANDIN = ("Stand-in pipeline data from web/make_standin_pipeline.py: rim points are placed from the true craters, "
           "particles drawn round the logged estimate, lidar cells judged from the heights without ray casting. "
           "No camera, rim detector or particle filter made them.")

# sim/scene.py camera (60 deg wide) and sim/lidar.py's grid; copied, not re-derived.
CAM_HALF_FOV = math.radians(30.0)        # sim/scene.py: 60 deg horizontal field of view
RIM_RANGE_M = (3.0, 15.0)                # docs/handoffs.md "Steps 3 and 6 rim points": visible 3-15 m
LIDAR_CELL_M = 0.5                       # sim/lidar.py HAZ_CELL_M
LIDAR_FWD_M = (2.0, 25.0)                # sim/lidar.py HAZ_RANGE_M
LIDAR_HALF_W_M = 15.0                    # sim/lidar.py HAZ_HALF_WIDTH_M
LIDAR_HALF_FOV = math.radians(60.0)      # sim/lidar.py H_FOV_DEG / 2
SLOPE_LIMIT_DEG = 15.0                   # sim/lidar.py SLOPE_LIMIT_DEG
RING_M = 3.0                             # sim/lidar.py RING_CELLS * HAZ_CELL_M
DEPRESSION_M = 0.35                      # sim/lidar.py DEPRESSION_M

# STAND-IN constants: this script's choices, used nowhere else.
STANDIN_PARTICLES = 60
STANDIN_PLAN_M = 20.0
STANDIN_RIM_NOISE_M = 0.10
STANDIN_RIM_STEP_DEG = 12.0              # one rim point per this much arc
K95 = math.sqrt(-2.0 * math.log(0.05))   # 2.448: 95% radius of a circular Gaussian over its per-axis sigma


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


# The lidar grid in the rover frame: cell (i, j) covers fwd LIDAR_FWD_M[0] + i*c, left -LIDAR_HALF_W_M + j*c.
NX = int(round((LIDAR_FWD_M[1] - LIDAR_FWD_M[0]) / LIDAR_CELL_M))
NY = int(round(2 * LIDAR_HALF_W_M / LIDAR_CELL_M))
_fi = LIDAR_FWD_M[0] + (np.arange(NX) + 0.5) * LIDAR_CELL_M
_lj = -LIDAR_HALF_W_M + (np.arange(NY) + 0.5) * LIDAR_CELL_M
FWD, LEFT = np.meshgrid(_fi, _lj, indexing="ij")
IN_WEDGE = (np.hypot(FWD, LEFT) <= LIDAR_FWD_M[1]) & (np.abs(np.arctan2(LEFT, FWD)) <= LIDAR_HALF_FOV)
RING = [(RING_M * math.cos(a), RING_M * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]


def lidar_cells(H, x, y, h):
    """One char per cell, row-major (i forward, j left): 0 not judged, 1 safe, 2 crater floor, 3 steeper than 15 deg."""
    c, s = math.cos(h), math.sin(h)
    wx, wy = x + FWD * c - LEFT * s, y + FWD * s + LEFT * c
    z = H.at(wx, wy)
    d = 1.0   # 2 m baseline, as sim/rover.py SlopeMap
    gx = (H.at(wx + d, wy) - H.at(wx - d, wy)) / (2 * d)
    gy = (H.at(wx, wy + d) - H.at(wx, wy - d)) / (2 * d)
    steep = np.degrees(np.arctan(np.hypot(gx, gy))) > SLOPE_LIMIT_DEG
    ring = np.mean([H.at(wx + dx, wy + dy) for dx, dy in RING], axis=0)
    dip = z < ring - DEPRESSION_M
    k = np.where(steep, 3, np.where(dip, 2, 1))
    k = np.where(IN_WEDGE, k, 0)
    return "".join(map(str, k.ravel().tolist()))


def rim_points(craters, ids, x, y, h, rng, wrong):
    """Near-arc rim points of craters 3-15 m ahead in the camera's view, rover frame [fwd, left, match, wrong]."""
    c, s = math.cos(h), math.sin(h)
    out = []
    dx, dy = craters[:, 0] - x, craters[:, 1] - y
    near = np.nonzero(np.hypot(dx, dy) < RIM_RANGE_M[1] + craters[:, 2] / 2)[0]
    for n in near:
        cx, cy, dd = craters[n]
        r = dd / 2
        toward = math.atan2(y - cy, x - cx)          # the half arc facing the Scout (Cauligi 2023 sec. 5A: near rim)
        for a in np.radians(np.arange(-90, 90.01, STANDIN_RIM_STEP_DEG)):
            px, py = cx + r * math.cos(toward + a), cy + r * math.sin(toward + a)
            ex, ey = px - x, py - y
            f, l = ex * c + ey * s, -ex * s + ey * c
            rr = math.hypot(f, l)
            if not (RIM_RANGE_M[0] <= rr <= RIM_RANGE_M[1]) or abs(math.atan2(l, f)) > CAM_HALF_FOV:
                continue
            f += rng.normal(0, STANDIN_RIM_NOISE_M)
            l += rng.normal(0, STANDIN_RIM_NOISE_M)
            out.append([round(f, 2), round(l, 2), ids[n], bool(wrong and ids[n] is not None)])
    return out


def particles(ex, ey, b95, rng):
    sig = max(b95, 0.05) / K95
    p = rng.normal([ex, ey], sig, size=(STANDIN_PARTICLES, 2))
    w = np.exp(-0.5 * np.sum((p - [ex, ey]) ** 2, axis=1) / sig ** 2)
    w /= w.sum()
    return [[round(float(a), 2), round(float(b), 2), round(float(c), 4)] for (a, b), c in zip(p, w)]


def plan_ahead(rows, i):
    out, dist = [[round(float(rows[i, 1]), 2), round(float(rows[i, 2]), 2)]], 0.0
    for j in range(i + 1, len(rows)):
        dist += math.hypot(rows[j, 1] - rows[j - 1, 1], rows[j, 2] - rows[j - 1, 2])
        out.append([round(float(rows[j, 1]), 2), round(float(rows[j, 2]), 2)])
        if dist >= STANDIN_PLAN_M:
            break
    return out if len(out) > 1 else []


def build(run_dir: Path):
    rec = json.loads((run_dir / "record.json").read_text(encoding="utf8"))
    poses = np.load(run_dir / "poses.npz")
    H = Heights(rec)
    start = np.asarray(rec["crater_map"]["start"], float).reshape(-1, 3)
    missing = np.asarray(rec["truth"]["craters_missing"], float).reshape(-1, 3)
    craters = np.vstack([start, missing]) if len(missing) else start
    ids = [f"m{i}" for i in range(len(start))] + [None] * len(missing)
    out = run_dir / "pipeline"
    out.mkdir(exist_ok=True)
    checks = {}
    for e in rec["events"]:
        if e["type"] == "check":
            checks.setdefault(e["sortie_id"], []).append(e)
    for so in rec["sorties"]:
        sid = so["sortie_id"]
        rows = poses[sid]
        rng = np.random.default_rng([rec["seed"], sum(map(ord, sid))])
        chk = sorted(checks.get(sid, []), key=lambda e: e["t"])
        steps = []
        for i, r in enumerate(rows):
            t, x, y, h, ex, ey, b95, _, act = (float(v) for v in r)
            if act in (7, 8):   # docked or waiting at the dock: no pipeline
                continue
            wrong = any(abs(e["t"] - t) < 1.0 and e.get("wrong_match") for e in chk)
            steps.append({"t": round(t, 1),
                          "rims": rim_points(craters, ids, x, y, h, rng, wrong),
                          "particles": particles(ex, ey, b95, rng),
                          "plan": plan_ahead(rows, i),
                          "lidar": lidar_cells(H, x, y, h)})
        doc = {"format": "pipeline-standin/1", "standin": STANDIN, "run_id": rec["run_id"], "sortie_id": sid,
               "scout": so["scout"],
               "lidar_grid": {"cell_m": LIDAR_CELL_M, "fwd0_m": LIDAR_FWD_M[0], "half_width_m": LIDAR_HALF_W_M,
                              "nx": NX, "ny": NY, "kinds": ["not judged", "safe", "crater floor", "steeper than 15 deg"]},
               "steps": steps}
        (out / f"{sid}.json").write_text(json.dumps(doc, separators=(",", ":")), encoding="utf8")
        nr = sum(len(s["rims"]) for s in steps)
        print(f"{rec['run_id']} {sid}: {len(steps)} steps, {nr} rim points, "
              f"{sum(s['lidar'].count('3') for s in steps)} steep cells", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", default="standin", help="batch to copy from")
    ap.add_argument("--dst", default="mission-standin", help="batch to write")
    ap.add_argument("--run", default=None)
    a = ap.parse_args()
    src, dst = DATA / "campaigns" / a.src, DATA / "campaigns" / a.dst
    runs = [src / a.run] if a.run else sorted(p for p in src.iterdir() if (p / "record.json").exists())
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("summary.csv", "by_sortie.csv"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
    for d in runs:
        (dst / d.name).mkdir(exist_ok=True)
        for f in ("record.json", "poses.npz"):
            shutil.copy2(d / f, dst / d.name / f)
        build(dst / d.name)


if __name__ == "__main__":
    main()
