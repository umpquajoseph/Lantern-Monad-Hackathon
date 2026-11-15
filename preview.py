"""Render a 10-second clip of whatever is merged right now, and open it.

    python preview.py

Writes data/previews/latest.mp4: chase view on the left, the rover's
lamp-only camera on the right. It uses sim/scene.py on the default terrain,
so it improves on its own as those files improve. Takes about a minute.
"""

import math
import os
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sim.scene import Scene          # noqa: E402
from sim.terrain import load_terrain  # noqa: E402

SECONDS = 10
FPS = 6                 # 60 frames: about a minute to render
STEP_M = 0.5            # metres driven per frame
OUT = os.path.join(ROOT, "data", "previews", "latest.mp4")


def main():
    t_start = time.time()
    height, craters, x0, y0, cell = load_terrain()
    scene = Scene(height, craters, x0, y0, cell)
    rows, cols = height.shape

    # Rover A starts near the centre of the map; rover B drives alongside.
    heading = 0.6
    ax = x0 + cols * cell / 2 - 15 * math.cos(heading)
    ay = y0 + rows * cell / 2 - 15 * math.sin(heading)
    side = (-math.sin(heading) * 8, math.cos(heading) * 8)

    n = SECONDS * FPS
    track = []
    frames = []
    for k in range(n):
        x = ax + k * STEP_M * math.cos(heading)
        y = ay + k * STEP_M * math.sin(heading)
        track.append((x, y))
        scene.set_rover("A", (x, y, heading))
        scene.set_rover("B", (x + side[0] + 6 * math.cos(heading),
                              y + side[1] + 6 * math.sin(heading), heading))
        fp, _depth = scene.first_person("A")
        tp = scene.third_person("A", {"true_track": np.array(track)})
        right = np.zeros((tp.shape[0], fp.shape[1], 3), np.uint8)
        top = (tp.shape[0] - fp.shape[0]) // 2
        right[top:top + fp.shape[0]] = fp[: tp.shape[0]]
        frame = np.hstack([tp, right])
        frames.append(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        print(f"\rframe {k + 1}/{n}", end="", flush=True)
    print()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    size = (frames[0].shape[1], frames[0].shape[0])
    for codec in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*codec), FPS, size)
        if writer.isOpened():
            for f in frames:
                writer.write(f)
            writer.release()
            break
    print(f"wrote {OUT} in {time.time() - t_start:.0f} s")
    if os.name == "nt" and "--no-open" not in sys.argv:
        os.startfile(OUT)


if __name__ == "__main__":
    main()
