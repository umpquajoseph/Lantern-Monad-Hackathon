"""Rim answer key for step 6 vision: where the mapped crater rims really are
in a first-person frame, and a score for detected rim points against it.

Definitions, from ShadowNav (docs/sources/ShadowNav-Cauligi-2023.pdf):
- What a rim observation is: with the lamp below the camera, "a shadow at
  the leading edge of a negative obstacle" (sec. 3A, p. 3); vision finds
  "the stark contrasting dark line along the rim" (sec. 3A.2, p. 3).
- Ground truth is the crater's rim circle from the orbital map. The "front
  arc" is "the half arc of the ground truth circle that was nearest the
  simulated camera" (sec. 5A, p. 7). That is NEAR_ARC_DEG = 90 here.
- Score: detected edges are scored by distance to the nearest ground-truth
  crater edge (Alg. 1, p. 4), through a Gaussian of sigma 0.25 m, "the
  resolution of the DEM" (sec. 5A, p. 7), averaged over detections; and by
  the "percent of front arc detected" (sec. 5A, p. 7).

Output format: docs/requests/rim-answer-key.md. Rover frame as in
docs/handoffs.md "Steps 3 and 6 rim points": [x_forward, y_left] metres
from the rover (the camera sits above the rover origin).

Run: python -m sim.rim_truth --run <run dir> --picks 9A 39A 134A 262B
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from sim.standins import RIM_HALF_FOV_DEG, RIM_RANGE_M

NEAR_ARC_DEG = 90.0          # Cauligi 2023 sec. 5A p. 7: the half arc nearest the camera
GAUSS_SIGMA_M = 0.25         # Cauligi 2023 sec. 5A p. 7: Gaussian sigma = DEM resolution
RIM_STEP_M = 0.05            # assumed: spacing of truth points along a rim (finer than any detector)
TOL_M = 0.5                  # assumed: match tolerance for precision / recall, 2 x GAUSS_SIGMA_M
VIS_TOL_M = 0.15             # assumed: a rim point is visible if the depth buffer is no nearer than
VIS_TOL_FRAC = 0.02          #   its range minus (VIS_TOL_M + VIS_TOL_FRAC * range)
VIS_WIN_PX = 1               # assumed: visibility uses the farthest depth in a (2k+1)^2 window, so a
                             #   crest on a silhouette edge is not lost to the pixel just in front of it
MIN_CONE = 0.25             # assumed: lamp cone factor at the crest below which no shadow line shows
SHADOW_PROBE_M = (0.05, 0.10, 0.20, 0.35, 0.50)  # assumed: ground this far beyond the crest is
                             #   tested for lamp shadow; a hit marks the rim point shadow-casting


def rim_truth(scene, agency, depth, craters=None):
    """Truth for one first-person frame.

    scene: sim.scene.Scene with the rover placed (set_rover) as for the frame.
    depth: (h, w) metres along each pixel ray, NaN for sky, from scene.first_person(agency).
    Returns a dict of per-point arrays, all rim points of craters near enough:
      xy (N, 2) rover frame [x_forward, y_left]; z (N,) up from the camera;
      uv (N, 2) pixel coordinates (u right, v down, pixel centres at +0.5);
      crater_id (N,) row in the crater map; near (N,) on the front half arc;
      in_view (N,) within RIM_RANGE_M and the 60 deg view; visible (N,) in view and not occluded;
      shadow (N,) visible, lit by the lamp cone, and casting lamp shadow just beyond the crest
      (the dark line vision sees).
    Convenience keys: rims (M, 2) and rims_crater_id (M,) = visible near-arc points, in the
    handoffs.md rims format.
    """
    craters = np.asarray(scene.craters if craters is None else craters, dtype=np.float64)
    C, fwd, right, up = scene._camera_frame(agency)
    x, y, hd = scene.poses[agency]
    cam = scene.camera_model()
    fx, fy, cx, cy = cam["fx"], cam["fy"], cam["cx"], cam["cy"]
    W, H = cam["width"], cam["height"]
    r_max = RIM_RANGE_M[1]

    R = craters[:, 2] / 2.0
    dc = np.hypot(craters[:, 0] - x, craters[:, 1] - y)
    cand = np.flatnonzero((dc - R < r_max + 1.0) & (dc + R > RIM_RANGE_M[0] - 1.0))

    pts, ids, near = [], [], []
    for i in cand:
        ccx, ccy, r = craters[i, 0], craters[i, 1], R[i]
        n = max(24, int(math.ceil(2 * math.pi * r / RIM_STEP_M)))
        a = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
        px, py = ccx + r * np.cos(a), ccy + r * np.sin(a)
        to_rover = math.atan2(y - ccy, x - ccx)
        dang = np.abs((a - to_rover + math.pi) % (2 * math.pi) - math.pi)
        pts.append(np.stack([px, py], 1)); ids.append(np.full(n, i)); near.append(dang <= math.radians(NEAR_ARC_DEG))
    if not pts:
        return _empty()
    P = np.concatenate(pts); ids = np.concatenate(ids); near = np.concatenate(near)

    # Height of the surface the camera sees, in the scene's local frame.
    lx, ly = P[:, 0] - scene.origin[0], P[:, 1] - scene.origin[1]
    lz = np.asarray(scene.surface.height(lx, ly), dtype=np.float64)
    Pl = np.stack([lx, ly, lz], 1)
    d = Pl - C
    rng3 = np.linalg.norm(d, axis=1)
    zc = d @ fwd
    xr, yu = d @ right, d @ up

    # Rover frame (handoffs.md): forward along the heading, left, up.
    dx, dy = P[:, 0] - x, P[:, 1] - y
    xf = dx * math.cos(hd) + dy * math.sin(hd)
    yl = -dx * math.sin(hd) + dy * math.cos(hd)
    rh = np.hypot(xf, yl)
    in_view = ((rh >= RIM_RANGE_M[0]) & (rh <= r_max)
               & (np.abs(np.degrees(np.arctan2(yl, xf))) <= RIM_HALF_FOV_DEG) & (zc > 0.1))

    u = cx + fx * xr / np.maximum(zc, 1e-6)
    v = cy - fy * yu / np.maximum(zc, 1e-6)
    ui, vi = np.floor(u).astype(int), np.floor(v).astype(int)
    on_img = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    visible = np.zeros(len(P), bool)
    dep = np.where(np.isnan(depth), np.inf, depth)
    k = VIS_WIN_PX
    for j in np.flatnonzero(in_view & on_img):
        win = dep[max(vi[j] - k, 0):vi[j] + k + 1, max(ui[j] - k, 0):ui[j] + k + 1]
        visible[j] = win.max() >= rng3[j] - (VIS_TOL_M + VIS_TOL_FRAC * rng3[j])
    # Shadow-casting: the scene's own lamp shadow test (the horizon map it shades with) finds
    # ground just beyond the crest, along the lamp's ray, in shadow.
    shadow = np.zeros(len(P), bool)
    j = np.flatnonzero(visible)
    if len(j):
        lamp = scene._lamp(agency)
        away = Pl[j, :2] - lamp.pos[:2]
        away /= np.maximum(np.linalg.norm(away, axis=1, keepdims=True), 1e-9)
        for s in SHADOW_PROBE_M:
            q = Pl[j, :2] + s * away
            Q = np.stack([q[:, 0], q[:, 1], np.asarray(scene.surface.height(q[:, 0], q[:, 1]), np.float64)], 1)
            shadow[j] |= lamp.visibility(Q) < 0.5
        # Only where the lamp reaches the crest: outside the cone there is no lit ground to
        # contrast with, so no dark line (scene's lamp cone, smoothstep inner -> outer).
        from sim.scene import LAMP_INNER_DEG, LAMP_OUTER_DEG
        ray = Pl[j] - lamp.pos
        ray /= np.linalg.norm(ray, axis=1, keepdims=True)
        off = np.degrees(np.arccos(np.clip(ray @ lamp.axis, -1.0, 1.0)))
        tt = np.clip((LAMP_OUTER_DEG - off) / (LAMP_OUTER_DEG - LAMP_INNER_DEG), 0.0, 1.0)
        shadow[j] &= tt * tt * (3 - 2 * tt) >= MIN_CONE
    vn = visible & near
    return dict(xy=np.stack([xf, yl], 1), z=Pl[:, 2] - C[2], uv=np.stack([u, v], 1), crater_id=ids,
                near=near, in_view=in_view, visible=visible, shadow=shadow,
                rims=np.stack([xf[vn], yl[vn]], 1), rims_crater_id=ids[vn])


def _empty():
    z2, z1 = np.zeros((0, 2)), np.zeros(0)
    b = z1.astype(bool)
    return dict(xy=z2, z=z1, uv=z2, crater_id=z1.astype(int), near=b, in_view=b, visible=b, shadow=b,
                rims=z2, rims_crater_id=z1.astype(int))


def score(pred_rims, truth, tol_m=TOL_M, sigma_m=GAUSS_SIGMA_M):
    """Score detected rim points against one frame's truth.

    pred_rims: (M, 2) rover frame [x_forward, y_left], as vision hands to the filter.
    precision: share of detections within tol_m of any visible rim point (any arc: Alg. 1
      scores against any mapped crater edge).
    recall: share of visible front-arc truth points with a detection within tol_m
      ("percent of front arc detected", sec. 5A).
    recall_shadow: the same over the shadow-casting part of the front arc only, the part a
      shadow-edge detector can see at all (assumed extra metric, not in the paper).
    mean_dist_m: mean distance from each detection to the nearest visible rim point.
    gauss: mean of exp(-d^2 / 2 sigma^2) over detections (sec. 5A's image score, in metres).
    Returns a dict; values are NaN when undefined (no detections, or no visible front arc).
    """
    pred = np.asarray(pred_rims, dtype=np.float64).reshape(-1, 2)
    vis = truth["xy"][truth["visible"]]
    front = truth["xy"][truth["visible"] & truth["near"]]
    shad = truth["xy"][truth["visible"] & truth["near"] & truth["shadow"]]
    out = dict(n_pred=len(pred), n_front=len(front), n_shadow=len(shad), tol_m=tol_m, sigma_m=sigma_m,
               precision=math.nan, recall=math.nan, recall_shadow=math.nan, mean_dist_m=math.nan,
               gauss=math.nan)
    if len(pred) and len(vis):
        d = _nearest(pred, vis)
        out.update(precision=float(np.mean(d <= tol_m)), mean_dist_m=float(d.mean()),
                   gauss=float(np.mean(np.exp(-d * d / (2 * sigma_m ** 2)))))
    elif len(pred):
        out.update(precision=0.0, gauss=0.0)
    if len(front):
        out["recall"] = float(np.mean(_nearest(front, pred) <= tol_m)) if len(pred) else 0.0
    if len(shad):
        out["recall_shadow"] = float(np.mean(_nearest(shad, pred) <= tol_m)) if len(pred) else 0.0
    return out


def _nearest(a, b):
    """Distance from each row of a to the nearest row of b."""
    if len(b) == 0:
        return np.full(len(a), np.inf)
    out = np.empty(len(a))
    for s in range(0, len(a), 2048):
        dd = a[s:s + 2048, None, :] - b[None, :, :]
        out[s:s + 2048] = np.sqrt((dd * dd).sum(-1)).min(1)
    return out


def truth_mask(truth, shape, near_only=True, width=2, shadow_only=False):
    """(h, w) uint8 mask of visible rim polylines (255), for an image overlay or a pixel score."""
    import cv2
    m = np.zeros(shape[:2], np.uint8)
    sel = truth["visible"] & (truth["near"] if near_only else True) & (truth["shadow"] if shadow_only else True)
    for cid in np.unique(truth["crater_id"][sel]):
        idx = np.flatnonzero(sel & (truth["crater_id"] == cid))
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):  # consecutive rim samples only
            if len(run) >= 2:
                cv2.polylines(m, [np.round(truth["uv"][run] - 0.5).astype(np.int32)], False, 255, width)
    return m


def overlay(img, truth, pred_rims=None, scene=None, agency=None):
    """Copy of img with the visible far half of each rim in blue, the front arc in yellow, its
    shadow-casting part in red and, if given, predicted rims projected onto the ground (needs
    scene and agency) as green circles."""
    import cv2
    out = np.array(img, dtype=np.uint8, copy=True)
    if out.ndim == 2:
        out = np.repeat(out[..., None], 3, 2)
    out[truth_mask(truth, out.shape, near_only=False, width=1) > 0] = (60, 120, 255)
    out[truth_mask(truth, out.shape, near_only=True, width=1) > 0] = (255, 220, 0)
    out[truth_mask(truth, out.shape, near_only=True, width=1, shadow_only=True) > 0] = (255, 40, 40)
    if pred_rims is not None and len(pred_rims) and scene is not None:
        for u, v in _project_rover_xy(scene, agency, np.asarray(pred_rims)):
            if 0 <= u < out.shape[1] and 0 <= v < out.shape[0]:
                cv2.circle(out, (int(u), int(v)), 3, (40, 230, 60), 1)
    return out


def _project_rover_xy(scene, agency, xy):
    """Pixel (u, v) of rover-frame points placed on the visible surface."""
    C, fwd, right, up = scene._camera_frame(agency)
    x, y, hd = scene.poses[agency]
    wx = x + xy[:, 0] * math.cos(hd) - xy[:, 1] * math.sin(hd)
    wy = y + xy[:, 0] * math.sin(hd) + xy[:, 1] * math.cos(hd)
    lx, ly = wx - scene.origin[0], wy - scene.origin[1]
    d = np.stack([lx, ly, np.asarray(scene.surface.height(lx, ly), dtype=np.float64)], 1) - C
    cam = scene.camera_model()
    zc = np.maximum(d @ fwd, 1e-6)
    return np.stack([cam["cx"] + cam["fx"] * (d @ right) / zc, cam["cy"] - cam["fy"] * (d @ up) / zc], 1)


def rim_shadow_px(img, truth, band_px=4, dark_frac=0.5):
    """Dark pixels along the visible front arcs: grey below dark_frac x the local median
    (21 px), within band_px of the arc. Counts what a shadow-edge detector has to work with."""
    import cv2
    g = img if img.ndim == 2 else img[..., 0]
    g = g.astype(np.float32)
    med = cv2.medianBlur(g.astype(np.uint8), 21).astype(np.float32)
    band = cv2.dilate(truth_mask(truth, g.shape, near_only=True, width=1),
                      np.ones((2 * band_px + 1, 2 * band_px + 1), np.uint8)) > 0
    dark = (g < dark_frac * med) & band
    return int(dark.sum()), int(band.sum())


def _picks(log, picks):
    for p in picks:
        step, ag = int(p[:-1]), p[-1]
        yield step, ag, log["steps"][step]


def main():
    import cv2
    from sim.scene import Scene, load_any_terrain
    from sim.standins import rims_standin
    from sim.terrain import default_path

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="run directory with log.json")
    ap.add_argument("--picks", nargs="+", default=["9A", "39A", "134A", "262B"], help="step + agency, e.g. 134A")
    ap.add_argument("--display", default="full", choices=["full", "detail", "off"])
    ap.add_argument("--out", default=None, help="output dir (default <run>/rim_truth)")
    a = ap.parse_args()
    run = Path(a.run)
    log = json.loads((run / "log.json").read_text())
    tfile = log.get("terrain", {}).get("file")
    tpath = default_path().parent / tfile if tfile else None
    height, craters, x0, y0, cell = load_any_terrain(tpath)
    scene = Scene(height, craters, x0, y0, cell, display=a.display)
    out = Path(a.out) if a.out else run / "rim_truth"
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    rows = []
    for step, ag, s in _picks(log, a.picks):
        for g, r in s["rovers"].items():
            scene.set_rover(g, r["true_pose"])
        img, depth = scene.first_person(ag)
        t = rim_truth(scene, ag, depth, craters)
        base = rims_standin(s["rovers"][ag]["true_pose"], craters, rng)
        sc = score(base, t)
        tag = f"{ag}_{step:04d}_{scene.display}"
        np.savez_compressed(out / f"{tag}.npz", **{k: v for k, v in t.items()})
        cv2.imwrite(str(out / f"{tag}.png"), img[..., ::-1])
        cv2.imwrite(str(out / f"{tag}_overlay.png"), overlay(img, t, base, scene, ag)[..., ::-1])
        dark, band = rim_shadow_px(img, t)
        rows.append(dict(frame=tag, true_pose=s["rovers"][ag]["true_pose"], n_front_visible=int((t["visible"] & t["near"]).sum()),
                         n_front_shadow=int((t["visible"] & t["near"] & t["shadow"]).sum()),
                         craters_seen=int(len(np.unique(t["rims_crater_id"]))),
                         rim_shadow_px=dark, rim_band_px=band, standin=sc))
        print(tag, {k: v for k, v in rows[-1].items() if k not in ("frame", "standin", "true_pose")},
              "standin", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in sc.items()})
    (out / f"summary_{scene.display}.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
