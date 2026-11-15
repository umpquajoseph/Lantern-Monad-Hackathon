# Request: step 4 path finding (`sim/rover.py`)

For Joseph, from Calvin's session. This is what was built against the
`joseph` branch, how it was checked, and what is needed from you to finish
wiring it in.

Built against `joseph` at commit `37d08f2e7ec01b4b37112b41cd8c7ea1ee38525f`.
`sim/terrain.py` and `sim/scene.py` were copied from that commit and checked
byte for byte (SHA-256 matches); neither was changed.

## What was built

`sim/rover.py`: route search and the drive that follows it (spec step 4,
"Route search on a slope map with a 15 deg limit"). Nothing else in step 4:
no second rover, ice sensor, odometry noise or chain calls.

```python
from sim.rover import Planner, Drive, NoRoute

planner = Planner(height, x0, y0, cell)        # step 2 format, docs/handoffs.md
route = planner.plan((x, y), (gx, gy))         # (K, 2) world waypoints; raises NoRoute
drive = Drive(route, (x, y, heading))
while not drive.done:
    true_pose = drive.step(nav_xy=None)        # (x, y, heading), one 0.5 m step
    scene.set_rover("A", true_pose)            # your Scene, unchanged
```

- Obstacles are your planner rule: slope over 15 deg at the 5 m baseline,
  computed with `terrain.slope_deg` on `height[::20, ::20]`, exactly as
  `terrain.report()` does. A 20 m band at the map edge is also blocked so the
  rover and cameras stay over terrain.
- A* on that 5 m grid (8-connected, no corner cutting), then straightened
  by line of sight with the rover's half width (0.83 m, from the rover model
  in `scene.py`) kept clear of blocked cells.
- `Drive` is pure pursuit: 0.5 m per step (as in `scene.py`'s scripted
  drive), 3 m lookahead, turn rate capped so the cameras turn smoothly.
  `step(nav_xy=...)` steers from step 3's estimate once it exists; the
  returned pose is always the true pose.
- Every assumed number is a named parameter marked ASSUMED; sourced ones
  name their source.
- Demo: `python -m sim.rover --terrain PATH.npz --start X Y --goal X Y`
  writes `frames/rover_drive.mp4` (chase view, first-person view, and a map
  inset with blocked cells, route and track) and `frames/rover_drive_map.png`.
  `--no-render` plans and drives without VTK.

## How it was checked

On `synthetic.npz` built by your `terrain.py build --synthetic`
(seed 7: 3205 of 40000 planner cells blocked, 82 by slope, the rest by the
edge band):

| Start -> goal (m) | Route | Straight line | Closest approach to a blocked cell |
|---|---|---|---|
| (290, 500) -> (380, 502), through the largest steep patch | 92.7 m | 90.0 m | 1.03 m |
| (300, 400) -> (600, 750) | 461.6 m | 461.0 m | 1.18 m |
| (480, 640) -> (520, 610) | 52.7 m | 50.0 m | 2.66 m |
| (100, 100) -> (900, 900) | 1136.4 m | 1131.4 m | 2.19 m |

All reached the goal; closest approach is the rover centre to the edge of a
blocked 5 m cell, always above the 0.83 m half width. On a made-up grid
with a steep wall and one 15 m gap, the route went through the gap with
2.02 m clearance; with the gap closed, `plan` raised `NoRoute`. The detour
render was watched frame by frame: both views follow the rover through the
turn. Not tested: the Shoemaker build, and any Mac or Windows machine.

## What is needed from you

1. **`data/terrain/shoemaker.npz`, or the Shoemaker LOLA tiles.** Calvin's
   checkout has only `data/lola/Site04_*`, so the real terrain cannot be
   built here. Everything so far ran on `synthetic.npz`.
2. **Confirm the planner scale.** At 5 m, your own report says the
   Shoemaker window is 99.9% under 15 deg, and crater walls (about 27 deg at
   0.25 m) are not obstacles, so most real routes will be almost straight.
   If you want visible detours around craters, the scale is one parameter,
   `PLAN_CELL_M`; say which you want.
3. **Known quirk of the 5 m rule.** `slope_deg` is a central difference,
   so the crest cell of a narrow ridge (or the floor of a narrow pit) can
   read as flat while both neighbours are blocked. The planner cannot reach
   such a cell across blocked neighbours, but it could drive along a long
   crest. Fine to keep; flagging it.
4. **File ownership.** The layout puts the planner and the ice sensor in
   `sim/rover.py`. Should the sensor and the two-rover loop go in this same
   file (then whoever writes them builds on it), or should the planner move
   to its own file?
5. **Route on the chase view.** `Scene.third_person` overlays take
   `true_track`, `odom_track`, `estimate`, `sigma` and `finds`, with no
   planned route. The demo draws the route only in a 2D inset. If you want
   the route draped on the ground in 3D, that is a new overlay key in
   `scene.py` and a change to `docs/handoffs.md`, which is yours.
6. **Two setup notes for other machines.** `terrain.py` defaults `DATA` to
   `C:\Monad-Hackathon-Project\data`, so on a Mac it needs
   `LANTERN_DATA=$PWD/data`. `scene.py` needs `vtk`, which is not in the
   README's pip line.
7. **Merging.** Per `CLAUDE.md`, this should sit on its own branch cut from
   `origin/joseph` (for example `step-4-rover`) and you merge it. Calvin will
   add the `docs/WORKING.md` row when he pushes.

## Next, once the above is settled

- Odometry `(distance_m, dheading_rad)` with 2% distance noise per
  `docs/handoffs.md`, so step 3 can run on the drive's output.
- Goal choice: "new ground" and "another agency's find", from the finds
  read off the chain.
