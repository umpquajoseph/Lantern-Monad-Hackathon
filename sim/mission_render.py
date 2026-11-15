"""First-person frames for a stored mission, with Joseph's scene (sim/scene.py).

The dashboard and the web viewer show `fp_frame` per step. This renders a
frame for each rover at each run-log step where it moved or worked, plus one
at every science stop, and writes them next to the run log:

    data/missions/<id>/fp/<agency>_<step>.jpg     and run_log.json's fp_frame
    data/missions/<id>/lidar/<agency>_<step>.jpg  and lidar_frame, lidar_hazards

The lidar frame is Calvin's forward lidar (sim/lidar.py) scanned at the same
pose and drawn by its own Lidar.view: the scan from above, forward up, grey
judged safe, amber crater floor, red steeper than 15 deg.

Uses the display layer from LANTERN_DATA (lantern_data/terrain/display), so
the ground has its regolith texture and the lamp's rim shadows. Needs VTK.

    python sim/mission_render.py [--mission ID] [--lidar-only | --no-lidar]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "sim"))
import mission_store as MS  # noqa: E402

JPEG_QUALITY = 85   # as run_demo.py
LIDAR_SEED = 7      # range-noise stream (ASSUMED: any fixed seed, so a re-render matches)


def render(store, display="full", camera=True, lidar=True):
    import cv2
    from sim.scene import Scene
    from sim.terrain import load_terrain, TERRAIN_DIR

    log_path = store.dir / "run_log.json"
    log = json.loads(log_path.read_text())
    height, craters, x0, y0, cell = load_terrain(TERRAIN_DIR / log["terrain"]["file"])
    disp_dir = TERRAIN_DIR / "display"
    scene = Scene(height, craters, x0, y0, cell, display=display,
                  display_dir=str(disp_dir) if disp_dir.exists() else None)
    fp = store.dir / "fp"
    fp.mkdir(exist_ok=True)
    lid = None
    if lidar:
        import numpy as np
        from sim.lidar import Lidar
        (store.dir / "lidar").mkdir(exist_ok=True)
        lid = Lidar(scene, np.random.default_rng(LIDAR_SEED))
    last_lidar, last_haz, ltimes = {}, {}, []
    sci_t = set()
    for s in log["steps"]:
        for e in s.get("science", []):
            sci_t.add(s["t"])
    last_pose, last_frame, times = {}, {}, []
    t0 = time.perf_counter()
    for k, s in enumerate(log["steps"]):
        for ag, r in s["rovers"].items():
            if last_pose.get(ag) != r["true_pose"]:
                scene.set_rover(ag, r["true_pose"])
        for ag, r in s["rovers"].items():
            want = last_pose.get(ag) != r["true_pose"] or s["t"] in sci_t or ag not in last_frame
            if want and camera:
                t = time.perf_counter()
                img, _ = scene.first_person(ag)
                times.append(time.perf_counter() - t)
                name = f"fp/{ag}_{k:04d}.jpg"
                cv2.imwrite(str(store.dir / name), img[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                last_frame[ag] = name
            elif want:
                last_frame[ag] = r.get("fp_frame") or last_frame.get(ag)
            if want and lid is not None:
                t = time.perf_counter()
                pts, rimg = lid.scan(r["true_pose"])
                hz, kinds = lid.hazards(pts)
                name = f"lidar/{ag}_{k:04d}.jpg"
                cv2.imwrite(str(store.dir / name), lid.view(rimg)[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                ltimes.append(time.perf_counter() - t)
                last_lidar[ag] = name
                last_haz[ag] = {kk: kinds.count(kk) for kk in ("crater", "slope")}
            last_pose[ag] = r["true_pose"]
            r["fp_frame"] = last_frame.get(ag)
            if lid is not None:
                r["lidar_frame"], r["lidar_hazards"] = last_lidar.get(ag), last_haz.get(ag, {})
    log["display"] = scene.display
    log_path.write_text(json.dumps(log, separators=(",", ":")))
    if times:
        print(f"  rendered {len(times)} first-person frames (median {1e3 * sorted(times)[len(times) // 2]:.0f} ms), "
              f"display {scene.display}")
    if ltimes:
        print(f"  rendered {len(ltimes)} lidar frames (median {1e3 * sorted(ltimes)[len(ltimes) // 2]:.0f} ms)")
    print(f"  total {time.perf_counter() - t0:.0f} s")
    return len(times) + len(ltimes)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mission")
    ap.add_argument("--display", choices=("full", "detail", "off"), default="full")
    ap.add_argument("--lidar-only", action="store_true", help="keep the camera frames, add lidar frames")
    ap.add_argument("--no-lidar", action="store_true")
    a = ap.parse_args()
    store = MS.MissionStore.open(a.mission) if a.mission else MS.MissionStore.latest()
    render(store, a.display, camera=not a.lidar_only, lidar=not a.no_lidar)


if __name__ == "__main__":
    main()
