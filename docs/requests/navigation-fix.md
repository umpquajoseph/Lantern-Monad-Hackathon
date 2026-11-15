# Request: navigation fix for `sim/rover.py`

For Joseph, from Calvin's session. Replaces the earlier request.

Checked against `joseph` at `a919add`. `sim/rover.py`, `sim/standins.py`,
`sim/ice.py`, `run_demo.py`, `sim/terrain.py` and `sim/scene.py` were taken
from that commit (SHA-256 checked) and run on `lantern_data/terrain/shoemaker.npz`.

## The bug: rovers drove into steep craters

`docs/handoffs.md` says slope for planning is measured across 2 m and must
stay under 15 deg. `SlopeMap` sampled the height only every 2 m
(`height[::8, ::8]`) and took forward differences between samples. Crater
walls lying between samples were missed. Measured on the 0.25 m grid with a
2 m central difference, the tracks from `run_demo.py --no-render` went over:

| Run, rover | Ground over 15 deg driven | Steepest |
|---|---|---|
| run 1, A | 14.2 m | 24.1 deg |
| run 1, B | 11.2 m | 25.1 deg |
| run 2, A | 19.5 m | 20.9 deg |
| run 2, B | 7.5 m | 25.0 deg |

Example: run 2's A drove across a mapped crater 17.8 m wide and 2.0 m deep
(local x 306, y 277). See `docs/nav/crater_fix_before_after.png`.

## The fix (`sim/rover.py` on this branch)

Same public interface, so `run_demo.py` needs no change: `SlopeMap`,
`Rover`, `PLANNER_STANDIN`, `SPEED_M_S`, `STEP_M`. Only these changed:

1. `SlopeMap.__init__`: slope is the 2 m central difference at every 0.25 m
   point, widened by the rover's half width (0.83 m, ASSUMED, from the rover
   model in `sim/scene.py`). The A* grid stays 2 m; each cell takes that
   value at its centre, where `plan()` puts waypoints.
2. `SlopeMap.at()`: reads the 0.25 m map, so `_feasible` checks the real
   ground.
3. `Rover._feasible`: checks every 0.25 m along a step (was 4 points). A
   rover standing on steep ground may move only onto ground no steeper,
   ending less steep, so it climbs out instead of sitting still.
4. `Rover.set_goal` / `follow`: if `plan()` found no route (it returns
   `[goal]`), plan again once the rover is on drivable ground.
5. `PLANNER_STANDIN` text updated to say what the planner now does.

Without (3) and (4), rover B in run 1 never moves: its start, local
(700, 250), is inside a mapped crater on 21.7 deg ground.

## Result, same command and seeds

- Run 1: confirmed (B read 5.36 wt%). Run 2: disputed (A's report 22.3 m off,
  B read 0.07 wt%). Your asserts pass.
- Ground over 15 deg driven: 0 m for every rover except run 1 B, which
  climbs 1.2 m out of the crater it starts in.
- Rovers still cross some shallow mapped craters whose walls are under
  15 deg across 2 m, as your handoff intends ("a 0.25 m slope map would wall
  off every crater interior").

## Questions for you

1. Move run 1's `b_start` (700, 250) out of the crater, or keep the
   climb-out?
2. Should rovers keep out of every mapped crater, whatever its slope? That
   would change the handoff rule, so it is your call.
3. Not checked: rendered frames from `run_demo.py` with this change (only
   `--no-render` and the top-down plot), and the web viewer.
