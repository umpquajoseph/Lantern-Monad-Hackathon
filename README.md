# data-shoemaker: the built Shoemaker terrain

Answers item 1 of `request.md` on `Calvin`: "`data/terrain/shoemaker.npz`,
or the Shoemaker LOLA tiles".

This branch holds only data and shares no history with `joseph`. Do not
merge it. Take the file out of it:

```bash
git fetch origin data-shoemaker
git checkout origin/data-shoemaker -- data/terrain/shoemaker.npz
git restore --staged data/terrain/shoemaker.npz
```

`data/` is git-ignored on the working branches, so the file stays local.

## What it is

- `data/terrain/shoemaker.npz`: the file `sim/terrain.py build` wrote from
  the Shoemaker LOLA tile (seed 7), the one `run_demo.py` and `sim/scene.py`
  use. It is re-saved with `np.savez_compressed` to fit under GitHub's 50 MB
  warning: 43.6 MB here, 64.3 MB uncompressed.
- The arrays are identical to the original: `height`, `craters`, `x0`, `y0`,
  `cell`, `crater_depth` and `meta`. `load_terrain()` reads it unchanged.
- Checks:
  - file sha256 `37532872a05ea6cda3b15a4461ec080d3a3f43b1220d38f41fb635887f8d8157`
  - `height.tobytes()` sha256 `5527763b687f21caac2344d4be88a32910424ec9b8d16667df88a12b4d35e24b`
- Grid: 4000 x 4000 at 0.25 m, x0 73250.125, y0 36000.125, with 11,082
  mapped craters.

## Note for the rover work

`joseph` already has a `sim/rover.py`. The demo spine was merged at 11:23:
an A* planner on a 2 m slope map, odometry with 2% drift, and the two-rover
loop in `run_demo.py`. The `sim/rover.py` on `Calvin` has the same path, so
the two will conflict. Items 2 to 7 of `request.md` are decisions for the
lead, not data.
