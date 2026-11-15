"""Render a run log from run_demo.py to a video, to watch the navigation.

    python -m sim.nav_video data/runs/run1-XXXX/log.json --out frames/run1_nav.mp4

Left: the chase view of the rover being followed. Right, top: its
first-person lamp camera. Right, bottom: a top-down map around it, with
ground over the slope limit in red, both rovers' tracks, and finds.

Poses come only from the log. Between two logged poses the rover is drawn
on the straight line joining them, which is the line sim/rover.py checked
for slope at every 0.25 m. Display only: nothing here changes the run.
"""
from __future__ import annotations

import argparse
import json
import math
import os

import cv2
import numpy as np

from sim.rover import SLOPE_LIMIT_DEG, SlopeMap
from sim.scene import FP_SIZE, TP_SIZE, Scene  # noqa: F401
from sim.terrain import default_path, load_terrain

SUBSTEPS = 2          # assumed: frames drawn per logged step (2 m steps -> 1 m per frame)
MAP_VIEW_M = 120.0    # assumed: width of ground shown in the map panel
FPS = 12              # assumed
AGENCY_BGR = {"A": (255, 190, 64), "B": (51, 153, 255)}


def map_panel(slope, x0, y0, cell, center, tracks, finds, size):
    """Top-down panel (size x size, BGR): steep ground red, tracks, finds."""
    w, h = size
    half = MAP_VIEW_M / 2
    cx, cy = center
    s = w / MAP_VIEW_M
    j0 = int(round((cx - half - x0) / cell)); j1 = j0 + int(MAP_VIEW_M / cell)
    i0 = int(round((cy - half * h / w - y0) / cell)); i1 = i0 + int(MAP_VIEW_M * h / w / cell)
    H, W = slope.fine_deg.shape
    sub = np.zeros((i1 - i0, j1 - j0), np.float32)
    ii0, ii1, jj0, jj1 = max(i0, 0), min(i1, H), max(j0, 0), min(j1, W)
    if ii1 > ii0 and jj1 > jj0:
        sub[ii0 - i0:ii1 - i0, jj0 - j0:jj1 - j0] = slope.fine_deg[ii0:ii1, jj0:jj1]
    steep = cv2.resize((sub > SLOPE_LIMIT_DEG).astype(np.uint8)[::-1], (w, h), interpolation=cv2.INTER_NEAREST)
    img = np.full((h, w, 3), 24, np.uint8)
    img[steep > 0] = (40, 40, 170)

    def px(x, y):
        return (int(round((x - (cx - half)) * s)), int(round(h / 2 - (y - cy) * s)))

    for ag, tr in tracks.items():
        if len(tr) >= 2:
            cv2.polylines(img, [np.array([px(*p) for p in tr], np.int32)], False, AGENCY_BGR[ag], 2, cv2.LINE_AA)
        p = px(*tr[-1])
        cv2.circle(img, p, 5, AGENCY_BGR[ag], -1, cv2.LINE_AA)
        cv2.putText(img, ag, (p[0] + 7, p[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, AGENCY_BGR[ag], 1, cv2.LINE_AA)
    for f in finds:
        p = px(f["x"], f["y"])
        col = {"reported": (40, 215, 255), "confirmed": (100, 240, 80), "disputed": (80, 80, 255)}.get(f["status"], (230, 230, 230))
        cv2.circle(img, p, max(3, int(f["sigma"] * s)), col, 2, cv2.LINE_AA)
        cv2.drawMarker(img, p, col, cv2.MARKER_DIAMOND, 12, 2)
    cv2.putText(img, f"red: over {SLOPE_LIMIT_DEG:.0f} deg across 2 m", (8, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 220, 220), 1, cv2.LINE_AA)
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log")
    ap.add_argument("--terrain", default=None, help="terrain .npz (default: sim.terrain.default_path())")
    ap.add_argument("--out", default="frames/nav.mp4")
    ap.add_argument("--follow", default="auto", help="A, B, or auto (whichever rover is moving)")
    a = ap.parse_args()

    log = json.load(open(a.log, encoding="utf8"))
    path = a.terrain or default_path()
    height, craters, x0, y0, cell = load_terrain(path)
    slope = SlopeMap(height, x0, y0, cell)
    scene = Scene(height, craters, x0, y0, cell)
    steps = log["steps"]
    tw, th = TP_SIZE
    rw = 400                                  # right column: camera on top, map below
    fh = int(round(rw * FP_SIZE[1] / FP_SIZE[0]))
    mw, mh = rw, th - fh
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    vw = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (tw + rw, th))
    tracks = {ag: [tuple(steps[0]["rovers"][ag]["true_pose"][:2])] for ag in steps[0]["rovers"]}
    finds = {}
    prev = {ag: steps[0]["rovers"][ag]["true_pose"] for ag in steps[0]["rovers"]}
    follow = "A" if a.follow == "auto" else a.follow
    n = 0
    for k, st in enumerate(steps[1:], start=1):
        moving = [ag for ag, r in st["rovers"].items() if r["true_pose"] != prev[ag]]
        for ev in st["events"]:
            if ev["type"] == "find":
                finds[ev["find_id"]] = dict(x=ev["x"], y=ev["y"], sigma=ev["sigma"], status="reported")
            elif ev["type"] == "check" and ev["find_id"] in finds:
                finds[ev["find_id"]]["status"] = "confirmed" if ev["agrees"] else "disputed"
        if not moving and not st["events"]:
            continue
        if a.follow == "auto" and moving and follow not in moving:
            follow = moving[0]
        for sub in range(1, SUBSTEPS + 1):
            t = sub / SUBSTEPS
            for ag, r in st["rovers"].items():
                p0, p1 = prev[ag], r["true_pose"]
                dh = (p1[2] - p0[2] + math.pi) % (2 * math.pi) - math.pi
                pose = (p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]), p0[2] + t * dh)
                scene.set_rover(ag, pose)
                if ag in moving:
                    tracks[ag].append(pose[:2])
            fl = [dict(v) for v in finds.values()]
            tp = scene.third_person(follow, {"true_track": np.array(tracks[follow]), "finds": fl})
            fp, _ = scene.first_person(follow)
            x, y, _ = scene.poses[follow]
            mp = map_panel(slope, x0, y0, cell, (x, y), tracks, fl, (mw, mh))
            frame = np.zeros((th, tw + rw, 3), np.uint8)
            frame[:, :tw] = tp[..., ::-1]
            frame[:fh, tw:] = cv2.resize(fp[..., ::-1], (rw, fh), interpolation=cv2.INTER_AREA)
            frame[fh:, tw:] = mp
            cv2.putText(frame, f"{log['label']}   t = {st['t'] / 60:.0f} min   following rover {follow}",
                        (14, th - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
            cv2.putText(frame, f"Rover {follow} camera (lamp only)", (tw + 8, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 210, 210), 1, cv2.LINE_AA)
            vw.write(frame)
            n += 1
        prev = {ag: r["true_pose"] for ag, r in st["rovers"].items()}
    vw.release()
    print(f"{n} frames -> {a.out}")


if __name__ == "__main__":
    main()
