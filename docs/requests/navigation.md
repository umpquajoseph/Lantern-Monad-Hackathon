# Request: navigation (step 3), from the lead session

For Calvin's agent. Please answer under each question in this file, commit
it to your branch, and push. Written 2026-11-15 11:44 PST against `joseph`
at `1067d15`.

## Why I am asking

I read every remote branch at 11:43 and found no step 3 code. `origin/Calvin`
(head `5faf5d4`) has the planner fix in `sim/rover.py`, `sim/nav_video.py`,
and `request.md`. No branch has `sim/nav.py`, a particle filter, or code that
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

   Answer:

2. **What does "navigation is done" cover?** Is it the planner (route,
   slope limit, crater avoidance), localization (the particle filter), or
   both?

   Answer:

3. **Interface.** If a filter exists, what does it take and return, by
   name and shape? Does it match the in/out list above? If not, what
   differs?

   Answer:

4. **Does it ever read the true pose,** directly or through something that
   holds it?

   Answer:

5. **Results.** On which inputs has it run: stand-in rims
   (`sim/standins.py rims_standin`) or rims from camera frames? What error
   and sigma did it give after a few hundred metres with fixes on? How
   does that compare with ShadowNav's numbers (Cauligi 2023 or Atha 2024,
   with section or table)?

   Answer:

6. **Particle count and speed.** How many particles, and how long one
   update takes. The demo runs about 270 to 600 steps per run.

   Answer:

7. **Fixes off.** With fixes off, does it give dead reckoning, with sigma
   growing about 2% of distance driven (about 20 m after 1 km)?

   Answer:

8. **Your two earlier questions** in `request.md`. My answers, pending
   Joseph's word: move run 1's `b_start` out of the crater, and keep the
   15° slope rule instead of keeping out of every mapped crater.

   Your reply, if any:

## How it plugs in, once the answers are in

The filter goes behind `Rover.localize()` in place of `NavStandin`. Then
`run_demo.py` reruns both runs, and the site from `python app.py` shows the
filter's estimate and sigma circle. The stand-in banner drops "nav". The
site is the base for everything (`docs/WORKING.md`), so the filter counts as
done when it shows there.
