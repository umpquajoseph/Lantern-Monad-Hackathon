"""The showcase run (docs/campaign-spec.md 6.4): camera navigation and first-person frames, for the video.

    python -m sim.showcase                 # render, navigate on the camera, write the record
    python -m sim.showcase --no-render     # quick check: stand-in rims, no frames

One short campaign on the 1 km drive area of the Shoemaker tile (0.25 m
grid with made-up small craters, sim/terrain.py). S1 leaves the dock and
surveys. Its camera sees crater rims under its own lamp (sim/scene.py,
sim/vision.py), and a particle filter matches them to the crater map
(sim/nav.py, after ShadowNav). It crosses buried ice and logs a find with its
position bound. S2 leaves the dock, drives to the find on its own camera
navigation, reads the ice there and confirms it.

The loop is run_demo.py's, unchanged: the same rovers, filter, vision,
lidar and ice sensor. This file only places the dock, names the Scouts
S1 and S2, and writes the result in the campaign record format
(docs/handoffs.md, "Campaign simulator") plus the first-person frames:

    data/campaigns/showcase/<run_id>/record.json, poses.npz, fp/<A|B>_<step>.jpg

The record says plainly, in `showcase`, that its navigation is Lantern's own
and not the source of the paper's numbers, which come from sim/campaign.py.

Why the drive area and not the campaign window: a camera needs ground finer
than the 5 m LOLA window the campaign uses. The drive area is the only
0.25 m ground with craters a lamp can light. It is Shoemaker terrain, not
shadowed crater floor.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import run_demo as rd
from sim.ice import FIND_THRESHOLD_WT
from sim.nav import ParticleNav
from sim.rover import SPEED_M_S, SlopeMap
from sim.settings import Settings
from sim.terrain import DATA, TERRAIN_DIR, default_path, load_terrain
from sim.vision import Vision

OUT = DATA / "campaigns" / "showcase"
K95 = math.sqrt(-2.0 * math.log(0.05))   # 2.448: 95% radius of a circular Gaussian, x sigma (sim/nav.py's sigma is per axis)
ACT = {"survey": 1, "goto": 6, "wait": 8, "done": 1}   # web/js/campaign.js ACTIVITY codes (docs/handoffs.md poses)

# Metres from the drive area's (x0, y0) corner. S1's start and goal are run_demo.py run 1's
# (ground checked under the slope limit); the dock and S2 sit just west of S1.
DOCK = (294.0, 300.0)                # assumed
SCENARIO = dict(key="showcase", label="Showcase: S1 finds ice by camera navigation, S2 confirms it",
                seed=101, a_fixes=True,
                a_start=(300.0, 300.0, 0.6), a_goal=(620.0, 520.0), find_at_m=260.0,
                b_start=(296.0, 300.0, 0.6))

SHOWCASE_NOTE = ("Showcase run (sim/showcase.py): the Scouts navigate on their own lamp camera, with Lantern's "
                 "rim finder and particle filter. This is not how the paper's numbers are made, and none of them "
                 "come from it. Ground: Shoemaker LOLA with made-up small craters, not shadowed crater floor.")


def bound95(sigma):
    return round(float(sigma) * K95, 3)


def to_record(run_id, steps, field, finds, ft, terrain, sensing, display):
    """run_demo's log -> campaign-record/1 (docs/handoffs.md), with S1 = rover A, S2 = rover B."""
    height, craters, x0, y0, cell = terrain
    s = Settings()
    name = {"A": "S1", "B": "S2"}
    sid = {"A": "S1-01", "B": "S2-01"}
    kw_h = s.avg_load_w / 1000.0 / 3600.0      # energy per second at the draft's average load
    t_end = steps[-1]["t"]
    # S2 launches when it first moves toward the find.
    t_launch = {"A": 0.0, "B": next((st["t"] for st in steps if st["rovers"]["B"]["mode"] == "goto"), t_end)}
    poses, events, frames = {}, [], {}
    for ag in "AB":
        rows = []
        for st in steps:
            r = st["rovers"][ag]
            if st["t"] < t_launch[ag]:
                continue   # S2 is at the dock until then (the site parks it there)
            tx, ty, th = r["true_pose"]
            ex, ey = r["estimate"]
            e = s.battery_kwh - kw_h * max(0.0, st["t"] - t_launch[ag])
            rows.append([st["t"], tx, ty, th, ex, ey, bound95(r["sigma"]), e, ACT.get(r["mode"], 1)])
            fl = frames.setdefault(sid[ag], [])
            if r.get("fp_frame") and (not fl or fl[-1][1] != r["fp_frame"]):   # a Scout standing still keeps its last frame
                fl.append([st["t"], r["fp_frame"]])
        poses[sid[ag]] = np.asarray(rows, np.float64)
        p0 = rows[0]
        events.append({"t": t_launch[ag], "sortie_id": sid[ag], "scout": name[ag], "type": "launch",
                       "x": p0[1], "y": p0[2], "est": [p0[4], p0[5]], "energy_kwh": s.battery_kwh})

    find_ev = check_ev = None
    for st in steps:
        for e in st["events"]:
            if e["type"] == "find":
                find_ev = (st, e)
            elif e["type"] == "check":
                check_ev = (st, e)
    if find_ev is None:
        raise RuntimeError("S1 never logged a find")
    st, e = find_ev
    tx, ty = (float(v) for v in ft["true_xy"])
    find = {"t": st["t"], "sortie_id": "S1-01", "scout": "S1", "type": "find", "x": round(tx, 3), "y": round(ty, 3),
            "find_id": 1, "est": [e["x"], e["y"]], "bound95": bound95(e["sigma"]), "reading_wt": e["reading"],
            "anchor": "camera"}
    events.append(find)
    status = "find"
    if check_ev is not None:
        st2, c = check_ev
        cx, cy = (float(v) for v in ft["check_true_xy"])
        radius = round(math.hypot(find["bound95"], bound95(c["sigma"])), 2)
        arrive = next((q for q in steps if q["t"] <= st2["t"] and
                       math.hypot(q["rovers"]["B"]["estimate"][0] - e["x"], q["rovers"]["B"]["estimate"][1] - e["y"]) <= radius),
                      st2)
        status = "confirmed" if c["agrees"] else "unconfirmed"
        events.append({"t": arrive["t"], "sortie_id": "S2-01", "scout": "S2", "type": "search_start",
                       "x": arrive["rovers"]["B"]["true_pose"][0], "y": arrive["rovers"]["B"]["true_pose"][1],
                       "find_id": 1, "radius_m": radius})
        events.append({"t": st2["t"], "sortie_id": "S2-01", "scout": "S2", "type": "search_end",
                       "x": round(cx, 3), "y": round(cy, 3), "find_id": 1, "radius_m": radius, "result": status,
                       "reason": "match" if c["agrees"] else "no_match", "reading_wt": c["reading"],
                       "truth_has_deposit": bool(field.concentration(cx, cy) >= FIND_THRESHOLD_WT)})
    events.sort(key=lambda v: v["t"])

    sorties = []
    for ag in "AB":
        p = poses[sid[ag]]
        dist = float(np.sum(np.hypot(np.diff(p[:, 1]), np.diff(p[:, 2]))))
        dur = float(p[-1, 0] - p[0, 0])
        sorties.append({"sortie_id": sid[ag], "scout": name[ag], "n": 1, "t_start": float(p[0, 0]), "t_end": float(p[-1, 0]),
                        "outcome": "showcase", "faults": [],
                        "time_s": {"drive": dur, "check": 0.0, "reading": 0.0, "drill": 0.0, "wait": 0.0},
                        "energy_end_kwh": round(float(p[-1, 7]), 3), "distance_m": round(dist, 1),
                        "checks": 0, "wrong_matches": 0})
    n = height.shape[0]
    xmin, ymin = x0 - cell / 2, y0 - cell / 2
    summary = {"run_id": run_id, "seed": SCENARIO["seed"], "n_scouts": 2, "check_policy": "every step",
               "map_refinement": False, "top_speed_mps": SPEED_M_S, "sorties": 2, "sorties_ok": 2,
               "finds": 1, "confirmed": int(status == "confirmed"), "false_confirmed": 0,
               "n_deposits": len(field.deposits)}
    return {
        "format": "campaign-record/1",
        "run_id": run_id,
        "seed": SCENARIO["seed"],
        "made_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "nav": f"camera: {ParticleNav.NAME}" + (f"; rims from {Vision.NAME}" if sensing else "; stand-in rims (no render)"),
        "showcase": SHOWCASE_NOTE,
        "label": SCENARIO["label"],
        "settings": {"battery_kwh": s.battery_kwh, "avg_load_w": s.avg_load_w, "speed_mps": SPEED_M_S,
                     "step_m": rd.STEP_M, "note": "the loop is run_demo.py's; energy is the draft's average load over time"},
        "terrain": {"site": "shoemaker drive area", "file": Path(default_path()).name, "x0": x0, "y0": y0, "cell": cell,
                    "bounds": [xmin, ymin, xmin + n * cell, ymin + n * cell]},
        "dock": {"x": x0 + DOCK[0], "y": y0 + DOCK[1]},
        "scouts": ["S1", "S2"],
        "standins": [] if sensing else ["rims: stand-in rims from the crater map, no camera (--no-render)"],
        "crater_map": {"start": [[round(float(c[0]), 2), round(float(c[1]), 2), round(float(c[2]), 2)] for c in craters],
                       "added": [], "scope": "the drive area's whole crater list, which the filter matches against"},
        "finds": [{"find_id": 1, "scout": "S1", "sortie_id": "S1-01", "est": find["est"], "bound95": find["bound95"],
                   "reading_wt": find["reading_wt"], "status": status}],
        "sorties": sorties,
        "events": events,
        "frames": {"note": rd.FRAMES_NOTE["fp_frame"], "display": display, "by_sortie": frames},
        "truth": {"label": "hidden truth", "ice": field.to_log(), "craters_missing": []},
        "summary": summary,
    }, poses


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-render", action="store_true", help="stand-in rims, no camera frames (a quick check)")
    ap.add_argument("--display", choices=("full", "detail", "off"), default="full")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    terrain = load_terrain(default_path())
    height, craters, x0, y0, cell = terrain
    slope = SlopeMap(height, x0, y0, cell)
    scene = display = None
    if not a.no_render:
        from sim.scene import Scene
        t = time.perf_counter()
        scene = Scene(height, craters, x0, y0, cell, display=a.display, display_dir=TERRAIN_DIR / "display")
        display = scene.display
        print(f"scene built in {time.perf_counter() - t:.0f} s", flush=True)
    run_id = "showcase-" + datetime.now().strftime("%Y%m%d-%H%M")
    run_dir = Path(a.out) / run_id
    t0 = time.perf_counter()
    # Pass 1 learns S1's path, so the deposit can sit on it (run_demo.py's docstring says why this is fair).
    cam1 = rd.LiveCamera(scene, None, 1, sensors=True) if scene is not None else None
    a_steps, _, _ = rd.simulate(SCENARIO, terrain, slope, field=None, cam=cam1)
    field = rd.place_ice(SCENARIO, a_steps, x0, y0, np.random.default_rng([SCENARIO["seed"], 99]))
    print(f"pass 1: {len(a_steps)} steps in {time.perf_counter() - t0:.0f} s", flush=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    cam = rd.LiveCamera(scene, run_dir, 1, sensors=True, replay=cam1) if scene is not None else None
    steps, chain, ft = rd.simulate(SCENARIO, terrain, slope, field=field, cam=cam)
    print(f"pass 2: {len(steps)} steps, {steps[-1]['t'] / 3600:.2f} h simulated, in {time.perf_counter() - t0:.0f} s", flush=True)
    rec, poses = to_record(run_id, steps, field, chain.get_finds(), ft, terrain, scene is not None, display)
    (run_dir / "record.json").write_text(json.dumps(rec, separators=(",", ":")), encoding="utf8")
    np.savez_compressed(run_dir / "poses.npz", **poses)
    lidar = run_dir / "lidar"
    if lidar.exists():
        shutil.rmtree(lidar)   # the lidar's own view; the site does not show it
    f = rec["finds"][0]
    err = math.hypot(f["est"][0] - ft["true_xy"][0], f["est"][1] - ft["true_xy"][1])
    print(f"find: S1 logged it {err:.1f} m from where it really was (95% bound {f['bound95']:.1f} m); status {f['status']}")
    print(f"frames: {sum(len(v) for v in rec['frames']['by_sortie'].values())}; wrote {run_dir}")


if __name__ == "__main__":
    main()
