# Request: vision (step 6), bring the branch up to date, from the lead session

For Calvin's agent. Written 2026-11-15 12:57 PST against `origin/integration`
at `623fbd1`. Please reply under "Answer" at the end, commit it to your
branch, and push.

## Why

Joseph says your vision work is running slowly. `origin/Calvin` (head
`b84e2e2`, 12:01) is **45 commits behind `integration`**. The site at
`python app.py` runs `integration`. Among the commits you lack:

- `sim/scene.py`, about 300 lines changed. Rim shadows come from a 5 cm near
  surface (47c99c1, 61af034). The lamp pool is 15/32 deg inside the view,
  with a new tone curve (2c8af7e). Frames you render on the old `scene.py`
  will not match what the site shows or what the answer key expects.
- `run_demo.py`: sensor frames render live in the sim loop, every 2 m step.
  Display-only frames (`fp_sub`, every 0.5 m) render after the sim, in a
  process pool (980e248). Particles are logged (bc9f6ea). The planner is
  logged as a real part (2c8af7e).
- The answer key for scoring vision: `sim/rim_truth.py` and
  `docs/requests/rim-answer-key.md` (branch `step-6-rim-truth`, merged into
  `integration` at f13b9ef).

## What to do

1. Merge `origin/integration` into your branch. `run_demo.py` conflicts.
   `integration` already contains your three ParticleNav edits (abb65cc),
   so keep `integration`'s copy and re-add only what vision needs.
2. For vision runs, skip what vision does not read:
   - `--sub-frame-m 0`: no display frames. They are about three in four of
     all frames, and vision never reads them.
   - `--only run1`: one run while you iterate.
   - `--display detail`: the terrain worker measured that the invented rim
     lips cause 29-37% of the rim shadow in `full` frames. Vision should not
     learn from geometry we made up. This is the lead's recommendation;
     Joseph has not decided yet.
3. Push your vision work-in-progress, even unfinished, so the lead can see
   what you have and where the time goes.

A performance worker (`perf`, branch `step-6-perf`) is profiling the
render and the rerun now. Its speed-ups land in `integration`. Merge them
when they arrive rather than tuning `scene.py` on your branch, so we do not
end up with two versions.

## Interface reminder (`docs/handoffs.md`)

Vision gives `rims (M, 2)` as `[x_forward, y_left]` metres in the rover frame,
from `scene.first_person(agency)` -> `(img, depth)`. It replaces
`rims_standin` behind the same call. Score it with `rim_truth.score`.

## Answer

Where the time goes in your vision run (per frame: render vs detection),
and the commit you pushed:
