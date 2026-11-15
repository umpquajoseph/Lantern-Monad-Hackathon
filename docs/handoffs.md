# Hand-offs between steps

Proposed formats. Agree them in the first 20 minutes of the build, then change
one only by telling the others and editing this file in the same commit.

## Frames and units

- World frame: metres, x east, y north. On a real LOLA tile, south polar
  stereographic metres, the frame the LOLA files use.
- Heading: radians, counterclockwise from +x.
- Rover frame: metres, x forward, y left, origin at the camera.
- Uncertainty `sigma`: one number in metres, the filter's 1-sigma radial
  spread, `sqrt(trace(cov) / 2)` of the particle cloud.

## Step 2, terrain, gives

- `height`: `np.ndarray (H, W)`, float32, metres, cell size 0.25 m, cell `[0, 0]`
  at world `(x0, y0)`.
- `craters`: `np.ndarray (N, 3)`, rows `[x, y, diameter]` in metres. This is the
  rover's orbital map.

## Step 4, rover loop, gives each step, per rover

- `true_pose`: `(x, y, heading)`.
- `odom`: `(distance_m, dheading_rad)` since the last step, with distance noise
  of 2% (ShadowNav section 1). Until step 4 exists, step 3 uses a scripted drive
  that produces the same tuple.

## Steps 3 and 6 rim points

- `rims`: `np.ndarray (M, 2)`, rows `[x_forward, y_left]` in metres, rover frame.
- Stand-in until step 6: points on the near rims of mapped craters within 15 m
  of the true pose, with noise added and some missed.

## Step 3, navigation, gives each step

- `estimate`: `(x, y)` in metres, world frame.
- `sigma`: metres, as defined above. Logged next to the true error.

## Step 1, chain client (`sim/chain.py`), gives

```python
post_find(agency, x, y, sigma, reading, data_hash) -> find_id
check_find(agency, find_id, x, y, sigma, reading, agrees) -> tx_hash
get_finds() -> list[dict]   # each: id, agency, x, y, sigma, reading,
                            # agree (agencies), disagree (agencies), status
```

- `agency` is `"A"` or `"B"`; the client maps it to the key in `.env`.
- On chain, positions and sigmas are whole centimetres, `data_hash` is
  `bytes32`, and status is reported, confirmed or disputed.
- Check rule: refused if the checker's agency is the finder's, or if
  `dx² + dy² > (3 × (sigma_find + sigma_check))²`, all in centimetres.

## Step 5, dashboard, takes

- The per-step state above for both rovers, `get_finds()`, and from step 6 the
  camera image `np.ndarray (h, w)` or `(h, w, 3)` with `rims` to overlay.
