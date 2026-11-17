"""Per-step census of a camera run: mapped craters in view vs rims detected vs rims kept.

    python -m sim.rim_census RUN_DIR [--sortie S1-01] [--stride 10] [--max-render N]

For one sortie of a camera campaign run (record.json, steps.npz, the run
ground it was rendered on), at each step's TRUE pose:

- in view: a mappable crater's near (front half) rim arc has points inside
  the camera's rim range and field of view (sim/rim_truth.py `in_view`),
  visible in the depth image (`visible`), and casting a lamp shadow
  (`shadow`, lamp-lit crest). Mappable: a crater on the run ground at least
  WorldRanges.orbital_map_min_d_m across; "in the map": one of those with a
  crater_map.start row within MAP_MATCH_M.
- detected: sim/vision.py detect_contours() on the frame (one noisy depth
  per frame, as the run), before the contour filter; a pixel is a mapped
  crater's when its noiseless placement lies within SOURCE_TOL_M of that
  crater's rim circle.
- kept: the same pixels after contour_ok (the measured 5.53 m threshold).

Steps whose pose cannot see any mappable crater by geometry alone (centre
beyond rim range + radius, or outside the field of view + the crater's
angular radius) are counted as not in view without rendering; every
`stride`-th of them is rendered anyway, for the detected/kept counts on
crater-free ground. Output: <run>/rim_census.json and a table on stdout.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from sim.rim_truth import RIM_HALF_FOV_DEG, RIM_RANGE_M, rim_truth
from sim.settings import WorldRanges
from sim.terrain import DATA, load_terrain
from sim.vision import Vision, contour_ok, rover_to_world

MAP_MATCH_M = 6.0      # assumed: a map row this close to a true crater is that crater (map error 2 m/axis)
SOURCE_TOL_M = 1.0     # as sim/calibrate.py: a rim pixel within this of a crater's rim circle is that crater's


def census(run_dir, sortie=None, stride=10, max_render=None, display=None):
    from sim.scene import Scene
    run_dir = Path(run_dir)
    rec = json.loads((run_dir / "record.json").read_text(encoding="utf8"))
    z = np.load(run_dir / "steps.npz")
    sid = sortie or sorted(k.split(".")[0] for k in z.files if k.endswith(".step"))[0]
    st = z[f"{sid}.step"]
    rim_o = z[f"{sid}.rim_o"]
    run_rims = np.diff(rim_o)                       # rim points the run itself logged per step
    g = rec["ground"]
    h, cr, x0, y0, cell = load_terrain(DATA / "terrain" / g["file"])
    scene = Scene(h, cr, x0, y0, cell, display=display or g.get("display", "off"))
    cut = WorldRanges().orbital_map_min_d_m
    big = cr[cr[:, 2] >= cut]
    start = np.asarray(rec["crater_map"]["start"], float).reshape(-1, 3)
    in_map = np.zeros(len(big), bool)
    if len(start) and len(big):
        dist, _ = cKDTree(start[:, :2]).query(big[:, :2])
        in_map = dist <= MAP_MATCH_M
    vis = Vision(scene, np.random.default_rng([int(rec["seed"]), 99]))
    btree = cKDTree(big[:, :2]) if len(big) else None
    r_max = RIM_RANGE_M[1]
    rows = []
    t = time.perf_counter()
    rendered = 0
    for k in range(len(st)):
        x, y, hd = (float(v) for v in st[k, 1:4])
        cand = []
        if btree is not None:
            for j in btree.query_ball_point((x, y), r_max + 12.0):
                cx, cy, d = big[j]
                dc = math.hypot(cx - x, cy - y)
                if dc - d / 2 > r_max:
                    continue
                ang = abs((math.atan2(cy - y, cx - x) - hd + math.pi) % (2 * math.pi) - math.pi)
                half = math.asin(min(1.0, (d / 2) / max(dc, 1e-6)))
                if ang <= math.radians(RIM_HALF_FOV_DEG) + half:
                    cand.append(j)
        row = dict(step=k, run_rim_points=int(run_rims[k]), geom=len(cand) > 0)
        if not cand and k % stride:
            rows.append(row)
            continue
        if max_render is not None and rendered >= max_render:
            rows.append(row)
            continue
        scene.set_rover("A", (x, y, hd))
        img, depth = scene.first_person("A")
        rendered += 1
        if cand:
            tr = rim_truth(scene, "A", depth, craters=big[cand])
            near = tr["near"]
            mapped_pt = in_map[np.asarray(cand)[tr["crater_id"]]] if len(tr["crater_id"]) else np.zeros(0, bool)
            for key, m in (("in_view", tr["in_view"]), ("visible", tr["visible"]), ("lit", tr["shadow"])):
                row[f"mappable_{key}"] = bool(np.any(near & m))
                row[f"mapped_{key}"] = bool(np.any(near & m & mapped_pt))
        nd = vis.noisy_depth(depth)
        v, u, wid, npx = vis.detect_contours(img, depth, nd=nd)
        kept = contour_ok(wid, npx) if len(v) else np.zeros(0, bool)
        src = np.full(len(v), -1)
        if len(v) and len(big):
            tw = rover_to_world(vis._rover_points(depth[v, u].astype(np.float64), v, u), (x, y, hd))
            dd, jj = btree.query(tw, k=min(4, len(big)))
            dd, jj = np.atleast_2d(dd), np.atleast_2d(jj)
            de = np.abs(np.hypot(big[jj, 0] - tw[:, :1], big[jj, 1] - tw[:, 1:2]) - big[jj, 2] / 2)
            a = de.argmin(axis=1)
            ok = de[np.arange(len(v)), a] <= SOURCE_TOL_M
            src[ok] = jj[np.arange(len(v)), a][ok]
        on_map = (src >= 0) & np.where(src >= 0, in_map[np.maximum(src, 0)], False)
        row.update(rendered=True, det_px=int(len(v)), det_mappable_px=int(np.sum(src >= 0)),
                   det_mapped_px=int(on_map.sum()), kept_px=int(kept.sum()),
                   kept_mappable_px=int(np.sum(kept & (src >= 0))), kept_mapped_px=int(np.sum(kept & on_map)),
                   mapped_width_max=round(float(wid[on_map].max()), 2) if on_map.any() else None,
                   mapped_range=round(float(np.median(depth[v, u][on_map])), 1) if on_map.any() else None)
        rows.append(row)
        if rendered % 100 == 0:
            print(f"  {rendered} frames rendered, step {k}/{len(st)}, {time.perf_counter() - t:.0f} s", flush=True)
    return dict(run=str(run_dir), sortie=sid, site=rec["world"].get("site", rec["terrain"]["site"]),
                density=rec["world"]["crater_n_ge10_per_km2"], ground=g, display=scene.display,
                steps=len(st), rendered=rendered, mappable=int(len(big)), in_map=int(in_map.sum()),
                stride=stride, rows=rows)


def table(c):
    R = c["rows"]
    n = len(R)
    g = [r for r in R if r.get("rendered") and r["geom"]]
    o = [r for r in R if r.get("rendered") and not r["geom"]]
    cnt = lambda rows, key: sum(1 for r in rows if r.get(key))
    pos = lambda rows, key: sum(1 for r in rows if r.get(key, 0) > 0)
    L = [f"{c['run']} {c['sortie']}: {c['site']}, {c['density']:g}/km2, ground {c['ground']['file']} "
         f"(cell {c['ground']['cell']} m, display {c['display']}); {c['mappable']} craters >= 10 m on the ground, "
         f"{c['in_map']} of them in the start map.", "",
         "| Steps (of %d) | mapped crater (in the start map) | any crater >= 10 m |" % n, "|---|---|---|",
         f"| geometry: near rim within range and field of view | {sum(1 for r in R if r['geom'])} (any >= 10 m) | |",
         f"| near rim in view (rim_truth in_view) | {cnt(g, 'mapped_in_view')} | {cnt(g, 'mappable_in_view')} |",
         f"| ... and visible in depth | {cnt(g, 'mapped_visible')} | {cnt(g, 'mappable_visible')} |",
         f"| ... and lamp-lit, casting a shadow | {cnt(g, 'mapped_lit')} | {cnt(g, 'mappable_lit')} |",
         f"| rim pixels detected on it, before the filter | {pos(g, 'det_mapped_px')} | {pos(g, 'det_mappable_px')} |",
         f"| rim pixels kept after the 5.53 m filter | {pos(g, 'kept_mapped_px')} | {pos(g, 'kept_mappable_px')} |",
         f"| any rim pixels detected / kept (rendered in-view steps) | {pos(g, 'det_px')} / {pos(g, 'kept_px')} of {len(g)} | |",
         f"| any rim pixels detected / kept (sampled other steps) | {pos(o, 'det_px')} / {pos(o, 'kept_px')} of {len(o)} | |",
         f"| the run's own logged rims | {sum(1 for r in R if r['run_rim_points'] > 0)} steps | |"]
    w = np.array([r["mapped_width_max"] for r in g if r.get("mapped_width_max") is not None])
    rr = np.array([r["mapped_range"] for r in g if r.get("mapped_width_max") is not None])
    if len(w):
        L.append(f"\nWidest mapped-crater contour per step ({len(w)} steps): median {np.median(w):.2f} m, "
                 f"quartiles {np.percentile(w, 25):.2f}-{np.percentile(w, 75):.2f} m, "
                 f"{np.mean(w >= 4.0):.0%} at 4 m or more, {np.mean(w >= 5.53):.0%} at the 5.53 m threshold "
                 f"or more; median range of those pixels {np.median(rr):.1f} m.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--sortie", default=None)
    ap.add_argument("--stride", type=int, default=10)
    ap.add_argument("--max-render", type=int, default=None)
    ap.add_argument("--display", default=None, help="override the run's display mode (default: as the run)")
    a = ap.parse_args()
    c = census(a.run_dir, a.sortie, a.stride, a.max_render, a.display)
    out = Path(a.run_dir) / ("rim_census.json" if a.display is None else f"rim_census_{a.display}.json")
    out.write_text(json.dumps(c), encoding="utf8")
    print(table(c))


if __name__ == "__main__":
    main()
