# Request: camera and lidar fused, with the rover's path drawn ahead of it

From Kellen, for Joseph and the owners of the scene, the spine, the viewer
and vision (Calvin). Written 2026-11-15 13:25 PST against `origin/Calvin`
at `c84f4e1` and `origin/integration` at `623fbd1`. A change to how the
autonomy software works and to what the demo shows.

## What Kellen wants, in his words

> The software is sensor fusion between a LiDAR sensor and the camera
> sensor. When the rover is moving forward, we should see an overview of
> what the camera sees and what the LiDAR is mapping, and with that, we
> should see a line, a dashed line, in front of the rover with exactly
> where it's going. So if there's no obstacles between it or in front of it
> that it can see, it's proceeding forward.
>
> Then in the demo, which I will set up outside of this change, we'll have
> a pre-computed point at which the rover stops right as it gets towards a
> crater. When it sees that crater, that dashed line goes from straight
> forward to navigating around the crater, around one direction of it.
>
> So the change that needs to be made is sensor fusion of the camera and
> LiDAR, and deploying a projected line in front of the rover for where
> it's going next, that can divert when it runs into something.

## Why

Judges score completeness and creativity on what they can see. Today the
viewer shows the rover's track after the fact. This change shows the rover
deciding: the sensors it is deciding from, and the path it has chosen,
bending the moment a crater comes into view. It is the one moment in the
demo where autonomy is visible rather than claimed.

## What must show on the demo page

1. **Camera and lidar side by side** while the rover drives: the lamp-lit
   camera frame, and what the lidar is mapping (its range view, and the
   hazards it has marked).
2. **A dashed line on the camera frame** from the rover forward along the
   path it is about to drive. Clear ground ahead: the line runs straight.
3. **The divert.** At the staged point the crater enters the lidar's view,
   its hazard cells appear, and the dashed line bends around one side of
   the crater. The same bend shows on the map's route.
4. Honest labels: the lidar is simulated from the terrain (no source in
   `docs/sources/` specifies a lidar for this rover), and says so.

## What the autonomy must do

- Each step, fuse the two sensors into one picture of the ground ahead:
  the camera frame (rims, shadows) and the lidar returns (geometry:
  craters as ground below the local surface, rocks above it, slopes over
  the limit). Hazards found by either go into the planner's blocked cells.
- Plan the next stretch from that fused picture and the goal, and publish
  the planned path each step, so the viewer can draw it.
- When a new hazard lands on the planned path, replan around it and keep
  the rest of the route. The divert must come from the sensors seeing the
  crater, not from the orbital map knowing it in advance (see staging).

## Staging the demo moment (Kellen's part, but it constrains the software)

- Kellen picks the run and the crater, and a pre-computed point just before
  it where the demo pauses. The viewer already pauses at a time:
  `?run=<run_id>&t=<seconds>` (`web/README.md`). So the autonomy's job is
  to log, per step, the lidar hazard count and the planned path, and the
  pause time comes from the step where the count first rises.
- For the line to go from straight to bent on screen, the planner must not
  already be avoiding that crater from the map. Either the crater is one
  the a-priori slope map does not block (shallow across 2 m, or unmapped),
  or the demo planner only learns craters from the sensors. Whoever owns
  the planner picks; say which in the commit.

## What exists already (so nobody builds it twice)

Calvin's WIP on `origin/Calvin`, commit `a6ed160` ("WIP vision and lidar:
camera rims for the filter, lidar crater hazards, planned path on the
camera"), held out of `checkpoint` at 13:10 only because the camera-rim
*localization* part does not pass run 1 and `web/js/main.js` conflicts
(`docs/requests/vision-sync.md`, `ops/LEADER_STATE.md`). It already has:

| Piece | Where | State |
|---|---|---|
| Forward lidar beside the camera: 120° x 33°, 30 m, range noise; ray-casts the same surface the camera sees | `sim/lidar.py` `Lidar.scan`, `hazards`, `view` | built |
| Lidar hazards: crater interiors, rocks, over-limit slopes, on a 0.5 m grid | `sim/lidar.py` `hazards()` | built |
| Hazards block the planner's 2 m cells; local replan around them; `goal_blocked` | `sim/rover.py` `plan(blocked=)`, `add_hazards`, `follow` | built |
| Planned path drawn on the camera frame as amber dots, hazards as red marks | `sim/vision.py` `draw_plan` | built (dotted, not dashed) |
| Camera obstacle hazards from depth (steps, slopes) | `sim/vision.py` | built |
| Per-step log: `lidar_frame`, `lidar_hazards` | `run_demo.py` on `Calvin` | built |
| Viewer lidar panel under the camera, caption with hazard counts | `web/index.html`, `web/js/main.js` on `Calvin` | built, conflicts with the designer's `main.js` |
| Camera rims replacing the stand-in for localization | `sim/vision.py` | fails run 1; not needed for this request |

So the fusion and the divert exist in code. What this request adds is
small, and it is mostly a merge decision:

## The ask, in order

1. **Decouple.** Bring in the lidar, the hazards, the planned path on the
   camera, and the lidar panel, with camera rims left as the stand-in
   default, exactly as `vision-sync.md` already says. The localization
   change can follow when it passes run 1; the divert does not depend on it.
2. **Draw the path as a dashed line**, from the rover's feet forward, in
   `draw_plan` (today: dots), and give the viewer the planned path per
   step in the log (world xy, so the map can draw the same line) rather
   than only baked into the frame.
3. **Log, per step, the lidar hazard count and whether the path changed**,
   so the staged pause time can be computed and the viewer can mark the
   step where the divert happened.
4. **Caption** on both panels: "lidar simulated from the terrain" and
   "dashed line: the path the rover is about to drive".
5. Keep the planner's a-priori crater knowledge out of the staged crater
   (see staging), and say in the commit how.

## How it will be checked

- `python app.py` (or the hosted link) on the demo run, paused at the
  staged time: camera and lidar panels both visible; the dashed line
  straight; hazard count in the caption; one step on: crater returns in
  the lidar view, red hazard cells, the dashed line bent around one side,
  the map route bent the same way; `lidar_hazards` in `log.json` rises at
  that step. Kellen looks at it before the pitch.
- Run 1 still ends confirmed and run 2 disputed with the lidar on
  (hazards must not strand rover B; Calvin's `BLOCKED_ARRIVE_M` covers a
  find inside a crater).

## Decisions for Joseph

- Whether the lidar and path pieces come in ahead of the camera-rim
  localization (the request assumes yes).
- Who does items 2 to 5: Calvin owns `sim/lidar.py`, `sim/vision.py` and the
  `rover.py` hazard changes; the viewer owner owns the panel and the map's
  route line; the spine owner owns the log fields.

## Answer

Please reply under here, commit to your branch, and push.
