"""First-person frames for a stored mission, with Joseph's scene (sim/scene.py).

The dashboard and the web viewer show `fp_frame` per step. This renders a
frame for each rover at each run-log step where it moved or worked, plus one
at every science stop, and writes them next to the run log:

    data/missions/<id>/fp/<agency>_<step>.jpg     and run_log.json's fp_frame

Uses the display layer from LANTERN_DATA (lantern_data/terrain/display), so
the ground has its regolith texture and the lamp's rim shadows. Needs VTK.

    python sim/mission_render.py [--mission ID]
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


def render(store, display="full"):
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
            if want:
                t = time.perf_counter()
                img, _ = scene.first_person(ag)
                times.append(time.perf_counter() - t)
                name = f"fp/{ag}_{k:04d}.jpg"
                cv2.imwrite(str(store.dir / name), img[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                last_frame[ag] = name
            last_pose[ag] = r["true_pose"]
            r["fp_frame"] = last_frame[ag]
    log["display"] = scene.display
    log_path.write_text(json.dumps(log, separators=(",", ":")))
    print(f"  rendered {len(times)} first-person frames in {time.perf_counter() - t0:.0f} s "
          f"(median {1e3 * sorted(times)[len(times) // 2]:.0f} ms), display {scene.display}")
    return len(times)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mission")
    ap.add_argument("--display", choices=("full", "detail", "off"), default="full")
    a = ap.parse_args()
    store = MS.MissionStore.open(a.mission) if a.mission else MS.MissionStore.latest()
    render(store, a.display)


if __name__ == "__main__":
    main()
