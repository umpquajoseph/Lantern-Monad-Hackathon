# Rim answer key for step 6 vision

For the vision step (find crater rims in first-person frames). This says,
for any frame, where the mapped crater rims really are, and scores a
detector's rim points against that by number. Code: `sim/rim_truth.py`
(branch `step-6-rim-truth`). It does not touch vision code.

## What counts as a rim (ShadowNav, Cauligi et al. 2023)

`docs/sources/ShadowNav-Cauligi-2023.pdf`:

- **The observation.** With the lamp below the camera, a crater shows
  "a shadow at the leading edge of a negative obstacle". Vision finds
  "the stark contrasting dark line along the rim" (sec. 3A, p. 3).
- **Ground truth.** It is the crater's rim circle from the orbital map.
  The front arc is "the half arc of the ground truth circle that was
  nearest the simulated camera" (sec. 5A, p. 7).
- **Scores.** The paper uses two.
  - Distance from each detection to the nearest ground-truth crater edge
    (Alg. 1, p. 4), through a Gaussian with sigma 0.25 m, "the resolution
    of the DEM" (sec. 5A, p. 7).
  - "Percent of front arc detected" (sec. 5A, p. 7).

## Calls

```python
from sim.rim_truth import rim_truth, score, overlay

img, depth = scene.first_person(agency)      # scene has both rovers placed for the frame
truth = rim_truth(scene, agency, depth)      # crater map defaults to scene.craters
s = score(pred_rims, truth)                  # pred_rims: (M, 2) [x_forward, y_left], metres
vis = overlay(img, truth, pred_rims, scene, agency)   # optional picture
```

`truth` is a dict of per-point arrays. Points are taken every 5 cm along
the rim of every mapped crater near the rover.

| key | shape | meaning |
|---|---|---|
| `xy` | (N, 2) | rover frame `[x_forward, y_left]`, metres, as in `docs/handoffs.md` "Steps 3 and 6 rim points" |
| `z` | (N,) | height above the camera, metres |
| `uv` | (N, 2) | pixel, u right and v down, pixel centres at +0.5 (`scene.camera_model()`) |
| `crater_id` | (N,) | row of the crater in the terrain's `craters` array |
| `near` | (N,) bool | on the front half arc (nearest the camera) |
| `in_view` | (N,) bool | 3-15 m from the rover and within the 60 deg view: the same limits as `rims_standin` |
| `visible` | (N,) bool | in view and not hidden by nearer ground, by the frame's depth buffer |
| `shadow` | (N,) bool | visible, and the scene's own lamp-shadow test finds ground 5-50 cm beyond the crest in shadow: the dark line itself |
| `rims` | (M, 2) | visible front-arc points: a perfect detector's output, in the handoff format |
| `rims_crater_id` | (M,) | crater per row of `rims` |

`score(pred_rims, truth, tol_m=0.5, sigma_m=0.25)` returns a dict.

| key | meaning |
|---|---|
| `precision` | share of detections within `tol_m` of any visible rim point. Any arc counts, as in Alg. 1. |
| `recall` | share of visible front-arc points with a detection within `tol_m`. This is the paper's "percent of front arc". |
| `recall_shadow` | the same, over the shadow-casting part only: what a shadow-edge detector can see at all. This one is not in the paper. |
| `mean_dist_m` | mean distance from each detection to the nearest visible rim point |
| `gauss` | mean of exp(-d^2 / 2 sigma^2) over detections. This is the paper's image score, in metres. |
| `n_pred`, `n_front`, `n_shadow` | counts. A value is NaN when it is undefined. |

- **Tolerance.** `tol_m = 0.5` m is assumed, at 2 sigma. The paper's
  Q-score treats anything within 1 m as exact (p. 4).
- **The vertical is dropped.** All distances are horizontal, in the rover
  frame, because the handoff's `rims` are 2-D.

## Command line

```
python -m sim.rim_truth --run <data/runs/run dir> --picks 9A 39A 134A 262B --display full
```

- For each pick it writes `<frame>.png`, `<frame>_overlay.png` and
  `<frame>.npz` (the truth dict), plus `summary_<display>.json`.
- The output goes under `<run>/rim_truth/` unless you pass `--out`.
- The summary scores `rims_standin` as a baseline.
- Overlay colours:
  - blue: the visible far half of each rim;
  - yellow: the front arc;
  - red: its shadow-casting part;
  - green circles: detections.

## Limits

- **Mapped craters only, D >= 2.3 m.** Rocks and craterlets in the
  display layer also cast dark crescents, and they are not in the map. A
  detector that finds them loses precision. That is intended, because the
  filter can only match mapped craters.
- **Rim heights come from the surface the camera sees** in the chosen
  display mode. In `full` that surface includes the invented lips.
- **The truth is geometric.** It does not say whether a detector could
  see a rim at a given range or contrast. `shadow` is the closest thing.
