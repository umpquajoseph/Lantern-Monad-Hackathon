# Brief: vision, pathfinding and lidar, integrated properly (handle `nav`)

Read `CLAUDE.md`, `ops/PROTOCOL.md`, `docs/TODO.md`, `docs/handoffs.md` and
this file first.

## What it is for

Joseph, 13:37: "have a worker fix calvin's code so the vision,
pathfinding, and lidar are all correctly integrated into my code".

Calvin's vision, lidar and hazard planner are on `main`, behind
`run_demo.py --vision` (his commit 2e28d61). Joseph opened a vision run of
run 1 and found three problems. Make vision, pathfinding and lidar work
the way a real rover's would, on the site, and make them good enough to be
the demo's default.

## What Joseph saw (leader checked each in the log and the code)

The run: `run1-20261115-vision`, served at http://localhost:8030 from
`C:\Monad-Hackathon-vision-data` (made with `--only run1 --vision
--sub-frame-m 0 --display detail`).

1. **A gives up on its goal.** Rover A starts at local (300, 300) and heads
   for its goal at (620, 520). At step 42, near (353, 308), it turns back.
   It wanders near (315, 300), then drives west. The find triggers after
   260 m driven (`find_at_m`), so it lands at (243, 293), west of the
   start. B then drives 574 m to check it. Without `--vision`, A drives
   straight to (505, 445). Likely causes in `sim/rover.py`:
   - `HAZARD_CELL_FRAC = 0.01` (line 38): one hazard point blocks a whole
     2 m planning cell, while the comment at line 267 says "blocked only
     when most of it is hazard";
   - hazard points are widened by the rover's half width;
   - the back-and-forth guard (about line 222) blocks cells for good;
   - hazards never clear.
   Confirm or overturn these; do not take them on trust.
2. **The path is jagged.** Joseph: "the path is verry jagged why".
   - Each 2 m step takes the first feasible heading from
     `TURN_OFFSETS_DEG` (line 31), with no limit on how far it turns per
     step.
   - A* runs on 2 m cells with 8 neighbours, and the route is not smoothed
     (waypoints every 4 m, line 150).
   - The rover re-aims every step from its estimate, so filter corrections
     jerk the heading.
   - With vision on, 60 deg turns in place leave dead-end spurs.
   A real rover drives smooth arcs. The fix goes in the rover's behaviour,
   not in the drawing: the logged poses are what the rover did, and
   smoothing only the line would be faking it.
3. **The lidar panel shows nothing a judge can read.** `Lidar.view`
   (`sim/lidar.py:164`) draws a 240 x 40 range image coloured by distance.
   On flat ground it is a smooth gradient with a black band of no return.
   It does not show what the lidar found. Joseph: "why does lidar view look
   like this?" Make it show what the rover learns from the scan. A top-down
   hazard map is one way; the choice is yours.

## Goal and limits

- Vision, lidar and the hazard planner work together in the sim loop.
  Rovers reach their goals, drive smooth paths, and avoid hazards and no-go
  ground. The find lands where it does without `--vision`.
- Then they become the default: make `--vision` on by default once both
  runs pass, and keep a flag for the stand-in rims.
- Real, not faked. Constants keep a source (Cauligi 2023, Atha 2024, the
  PROSPECT brief) or get marked as assumed. Rover turn rate, smoothing and
  hazard thresholds are named, assumed parameters.
- Score vision against the answer key, `sim/rim_truth.py` (see
  `docs/requests/rim-answer-key.md`). The terrain worker recommends
  `--display detail` for vision, because the invented rim lips make about a
  third of the rim shadow.
- Speed: a vision run of run 1 takes about 8 minutes here, so iterate with
  `--only run1 --sub-frame-m 0`. `perf` is making the render faster.

## Ownership

- You own `sim/rover.py`, `sim/vision.py` and `sim/lidar.py` for this job.
  Calvin wrote them. He is a teammate and may still be editing them, so
  read `docs/requests/vision-sync.md` and check `origin/Calvin` before you
  start and before each push. If he has pushed changes, merge them rather
  than overwrite them.
- `perf` owns `LiveCamera`, the render pool and `sim/scene.py`. `scene`
  owns `run_demo.py`'s `main()` and `--resume`. If you need a change there,
  agree it by mail first.
- `viewer` owns `web/js`. A new lidar panel or path drawing is agreed with
  viewer, and one of you makes it. The image `sim/lidar.py` writes is
  yours.
- Work on branch `step-6-nav` from `origin/integration` (which is `main`
  plus the resume and perf fixes), in your own worktree
  `C:\Monad-Hackathon-nav`. Push often. `integrator` merges. Never touch
  `joseph` or `main`.

## How it is checked

Mail the leader a report (`ops/PROTOCOL.md` format) with, for run 1 and
run 2 with vision on:

- each rover's track: start, goal, find spot, distance driven, metres on
  no-go ground, and the outcome (run 1 confirmed, run 2 disputed);
- smoothness in numbers: largest and 95th-percentile heading change per
  step, before and after, and the count of dead-end spurs;
- the vision score from `rim_truth.score`: precision, recall, mean
  distance;
- the new lidar panel: a screenshot from the site, read back;
- the same numbers without `--vision`, to show the default still works;
- what you are blind to.

## Talking to others

Your handle is `nav`. Your inbox is `ops/mailboxes/nav/inbox/`, in
`C:\Monad-Hackathon-Project`. Mailboxes are git-ignored, so write files
there directly. Mail `ts` comes from `date`. No commit message names a
person. Give the leader your doorbell address in your first mail.

| handle | doorbell address |
|---|---|
| leader | `Hackathon project setup review [5247c4]` |
| perf | `uds:\\.\pipe\LOCAL\cc-msg-b5fa754137f62a9b8f31896194e2668b` |
| scene | `uds:\\.\pipe\LOCAL\cc-msg-003a54f5030ab1d0041668abdbf2f6dc` |
| viewer | `uds:\\.\pipe\LOCAL\cc-msg-a233f82a3c78cbd382c1279676ac4ac8` |
| integrator | `uds:\\.\pipe\LOCAL\cc-msg-e4aa78077ba4f6511766cd4cc20730b8` |
| terrain | `uds:\\.\pipe\LOCAL\cc-msg-3956fbcec9b14cf96512bbeaf1a4ac16` |
