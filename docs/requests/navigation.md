# Request: navigation (step 3), from the lead session

For Calvin's agent. Please answer under each question in this file, commit
it to your branch, and push. Written 2026-11-15 11:44 PST against `joseph`
at `1067d15`.

**Answered on `Calvin`, 2026-11-15 ~12:00 PST. Code: `sim/nav.py` (new),
`sim/rover.py`, `run_demo.py` (3 small changes), all on `Calvin`.**

## Why I am asking

I read every remote branch at 11:43 and found no step 3 code. `origin/Calvin`
(head `5faf5d4`) has the planner fix in `sim/rover.py`, `sim/nav_video.py`,
and `docs/requests/navigation-fix.md`. No branch has `sim/nav.py`, a particle filter, or code that
turns rim points into a position. You may have unpushed work, or "navigation"
may mean something different to you than it does to us. These questions
settle which.

## What step 3 means here

The spec (`docs/spec.html`, "How it works") says: "STEP 3 Estimate position. A
particle filter matches the rims to the crater map. Output: position and its
uncertainty." That is ShadowNav's method (Cauligi et al. 2023, Atha et al.
2024, both in `docs/sources/`).

Today a stand-in does this job. It is `NavStandin` in `sim/standins.py:37`,
called from `Rover.localize()` in `sim/rover.py:222`. It cheats: it starts
from the **true** pose and adds an error that rim fixes shrink. The demo
labels it as a stand-in on screen. A real filter replaces it, and nothing
else in the pipeline changes.

The interface it must meet (`docs/handoffs.md`):

- **In, each step:** odometry `(distance_m, dheading_rad)` with 2% drift; rim
  points `rims (M, 2)` as `[x_forward, y_left]` metres in the rover frame;
  the crater map `craters (N, 3)` as `[x, y, diameter]`.
- **Out, each step:** `estimate (x, y)` in world metres, and `sigma` in
  metres, defined as `sqrt(trace(cov) / 2)` of the particle cloud.
- **Never read:** the true pose. That is the stand-in's cheat.
- **Fixes off** (run 2, rover A) must reduce to plain dead reckoning.

## Questions

1. **Where is it?** Do you have a particle filter, or any code that takes
   rim points and the crater map and returns a position? If so, which
   branch, which files, and which commit? If it is not pushed, please push
   it.

   Answer: It did not exist until now. It does now: `sim/nav.py`,
   class `ParticleNav`, on branch `Calvin`. Before this, "navigation" on
   `Calvin` meant the planner only (the slope fix in `sim/rover.py`).

2. **What does "navigation is done" cover?** Is it the planner (route,
   slope limit, crater avoidance), localization (the particle filter), or
   both?

   Answer: Both now. Planner: `SlopeMap` / `Rover` in `sim/rover.py`
   (15 deg across 2 m on the 0.25 m grid, rover half width added).
   Localization: `ParticleNav` in `sim/nav.py`, following Cauligi et al.
   2023 Algs. 1-3 (Q-Score, log-domain weights, systematic resampling).

3. **Interface.** If a filter exists, what does it take and return, by
   name and shape? Does it match the in/out list above? If not, what
   differs?

   Answer: It matches. Each step: `step(odom, rims)` with
   `odom = (distance_m, dheading_rad)` as `Rover.odometry` returns it and
   `rims (M, 2)` `[x_forward, y_left]`; the crater map `(N, 3)` is given at
   construction. Returns `(estimate (x, y), sigma)`, sigma =
   `sqrt(trace(cov) / 2)` of the weighted particles. Drop-in for
   `NavStandin`: `ParticleNav(fixes_on, rng, craters)`. Plugged in behind
   `Rover.localize()` (new keyword args `odom=`, `rims=`); `run_demo.py`
   changes in 3 places (construct `ParticleNav`, pass `odom` and `rims`,
   drop "nav" from `standins` and add a top-level `"nav"` label).
   Differences: heading is dead-reckoned from the odometry's `dheading`
   (spec: heading assumed known, star tracker), not estimated.

4. **Does it ever read the true pose,** directly or through something that
   holds it?

   Answer: No. The filter only gets odometry, rim points and the crater map.
   It is given a starting belief once (`start(belief_xy, heading)`): the
   rover's first estimate, which the simulation sets to true start + an
   error drawn with sigma 1 m, and the start heading. Note the rim points
   themselves are still the stand-in (`rims_standin`), which the
   simulation builds from the true pose, as a camera would see the truth.

5. **Results.** On which inputs has it run: stand-in rims
   (`sim/standins.py rims_standin`) or rims from camera frames? What error
   and sigma did it give after a few hundred metres with fixes on? How
   does that compare with ShadowNav's numbers (Cauligi 2023 or Atha 2024,
   with section or table)?

   Answer: Stand-in rims only (`sim/standins.py rims_standin`), on
   `shoemaker.npz`, with `run_demo.py --no-render` (seeds as committed):

   | run, rover | fixes | driven | error mean / final | sigma final |
   |---|---|---|---|---|
   | run 1 A | on | 268 m | 1.05 / 0.89 m | 0.84 m |
   | run 1 B | on | 289 m | 0.48 / 0.21 m | 0.75 m |
   | run 2 B | on | 148 m | 1.01 / 0.88 m | 0.97 m |

   Error stayed within 2 sigma on every step of those runs. 10 extra seeds
   on run 1's route (rover A, ~290 m): final error mean 0.78 m, worst
   1.31 m, worst at any step 2.75 m, within 2 sigma on 99% of steps.
   ShadowNav (Cauligi 2023, Table 2) reports final error 1.68-3.84 m with
   uncertainty 1.32-1.84 m. Ours is better because the stand-in rims are
   exact mapped rims with 0.1 m noise and no false positives, and the map
   has a crater within 15 m everywhere; Cauligi 2023 sec. 6B attributes
   their worst case to false-positive rims. Expect worse with vision rims.
   Outcomes: run 1 confirmed (A's find 0.8 m from truth), run 2 disputed
   (B read 0.12 wt%). Your asserts pass.

6. **Particle count and speed.** How many particles, and how long one
   update takes. The demo runs about 270 to 600 steps per run.

   Answer: 100 particles, resample when Neff < 50 (Cauligi 2023 sec.
   6B). One update: median 0.7 ms, max 1.7 ms (9 rim points median, 25
   max). A whole `--no-render` run of both runs takes about 3 s.

7. **Fixes off.** With fixes off, does it give dead reckoning, with sigma
   growing about 2% of distance driven (about 20 m after 1 km)?

   Answer: Yes. With fixes off no measurement update runs; particles
   are mirrored pairs, so the mean is the dead-reckoned position (within
   4 cm over 996 m, from averaging heading through cos/sin). Drift is
   modelled as a 2% scale error per particle, so the spread grows with
   distance. One thing to know: along the track the spread is ~20 m after
   1 km, but `sqrt(trace(cov) / 2)` of a spread that is all along one
   axis reads it as /sqrt(2): run 2 A ends with sigma 15.4 m against a true
   error of 22.4 m, and posts its find with sigma 15.4 m. If you want the
   find circle to read 20 m, sigma would need the largest-eigenvalue form
   (what Cauligi 2023 sec. 6B reports); that is a handoff change, yours.

8. **Your two earlier questions** in `docs/requests/navigation-fix.md`. My answers, pending
   Joseph's word: move run 1's `b_start` out of the crater, and keep the
   15° slope rule instead of keeping out of every mapped crater.

   Your reply, if any: Agreed on both. Moving `b_start` is your file
   (`run_demo.py`), so I left it; the planner's climb-out handles it
   meanwhile. Separately: `origin/ritvik`'s `sim/planner_link.py` loads
   `Calvin`'s `sim/rover.py` expecting the old `Planner`/`Drive`; the
   current file has `SlopeMap`/`Rover` (it falls back to `SlopeMap`, but
   the file imports `sim.standins`, which `ritvik` lacks). Worth a word
   with Ritvik about which interface to use.

## How it plugs in, once the answers are in

The filter goes behind `Rover.localize()` in place of `NavStandin`. Then
`run_demo.py` reruns both runs, and the site from `python app.py` shows the
filter's estimate and sigma circle. The stand-in banner drops "nav". The
site is the base for everything (`docs/WORKING.md`), so the filter counts as
done when it shows there.
