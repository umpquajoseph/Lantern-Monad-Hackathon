# Brief: Ritvik's layout, playing Kellen's mission (handle `viewer`)

Read `CLAUDE.md`, `ops/PROTOCOL.md`, `docs/handoffs.md` ("Mission log") and
this file first.

## What it is for

Joseph, 14:44: "in general the ui layout from ritviks is better", and "kellens
mission is the one that will be rendered". The judges' page becomes Ritvik's
Mission Control layout (`web/mission.html`, `web/js/mission.js` on
`origin/ritvik`). It plays Kellen's mission log (`mission-demo`, from
`run_mission.py`), with everything that works on today's page kept.

Today Mission Control plays only Ritvik's registry log (`sim/roles.py`, 8
sites). Kellen's mission plays only on `index.html` through `main.js`,
`mission_player.js` and `live_chain.js`. This job joins the two.

## Joseph's decisions (14:44)

Take from Ritvik:
- Layout: full-screen 3D, bottom play bar, camera and map boxes over the
  scene, folding left panel, legend behind (i).
- NASA rover models with suspension, name tags, keyboard shortcuts, slow
  playback during science.
- Science arm and drill animation, where it fits Kellen's science stops
  (Surveyor's survey at S1 and S2, Prospector's instrument sequence).

Change:
- Crater rings: replace them with red shading of steep ground (over 15
  deg) in the 3D view, the same no-go layer as the overhead map's
  (`nogo.png`), with an on/off toggle.
- Lidar: drop Ritvik's Camera | Lidar switch. The camera box gets three
  views side by side as tabs: First person, Satellite (overhead map), Lidar.
  The Lidar view keeps today's Top-down | First person toggle
  (`lidar_frame`, `lidar_fp_frame`) and its captions.
- Drop the snap to first person, with no drawn overlays, at Surveyor stops.
  The view stays where the user put it.

Keep from today's page (main at 4f51c54):
- Kellen's checkpoints and live chain: `mission_player.js`,
  `live_chain.js`, `?key=` posting, the chain feed with Monadscan links that
  open in a new window, the finds list.
- The navigation table (thinks within / actually off / odometry), particles,
  rim points, true and estimated tracks, the frost ice patch, the planned
  route in amber dashes, A/B following in the 3D view, the stand-ins banner.

Leave out: Ritvik's copies of `sim/lidar.py` and `sim/scene.py` (older than
main's: they would drop nav's first-person lidar and perf's work), his
registry contract, `sim/roles.py` and `sim/mission_render.py` from the
judges' page. They stay in the repo, unused by the demo.

## Shape

- Branch `step-5-ui` from `origin/main` (4f51c54), worktree of your choice.
  Merge `origin/ritvik`; in conflicts keep main's `sim/lidar.py` and
  `sim/scene.py`, and Ritvik's layout in `web/`.
- The new layout becomes `web/index.html`. Keep today's page reachable as
  `web/classic.html` as a fallback for the demo.
- Paths stay relative, so Kellen's Vercel publish keeps working.
- Stage 1 (push as soon as it plays): Kellen's mission plays end to end in
  the new layout, with checkpoints, chain feed, camera, satellite and lidar
  views. Stage 2: the rest of the lists above.

## How it is checked

Mail the leader, with screenshots read back:
- the whole mission plays at 200x in the new layout, checkpoints pause, no
  console errors;
- each camera-box view (first person, satellite, lidar top-down and first
  person) at the divert-B checkpoint;
- the steep-ground shading on and off;
- the chain feed after the find and the check, with `?key=` absent (recorded
  hashes);
- `classic.html` still plays;
- what you left out or could not do.

Push to `step-5-ui`, then ask the integrator to merge. No full renders: use
`data/runs/mission-demo` as it is.
