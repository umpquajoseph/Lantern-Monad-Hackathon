# Brief: drive (Scouts that get stuck)

Handle `drive`. Branch: the one your session starts on, cut from
`lantern-heading`. Report to `leader` by mail (`ops/PROTOCOL.md`). Opus.

## What this is for

The product owner watches sorties in Mission Control and sees Scouts stuck:
spinning in place, rocking back and forth, trapped. Hours of fixes have been
reported as working and were not. Done means a one-Scout sortie drives its
whole route and comes home without a stuck episode, on seed 3 and on two
other haynes seeds, and you watched where it went, not only its outcome.

## What is known (23 Nov, leader)

| Run (`data/campaigns/...`) | Code | Outcome |
|---|---|---|
| `spin-check-1/w00003-86f177f0` | `09df192` (before the driving changes) | ok, home; still 119 turns in place, 33 back-and-forth blocks, 4 skips |
| `spin-check-2/w00003-77e2cab8` | reversing without a limit | lost, trapped, 1,586 reversals |
| `spin-check-4/w00003-77e2cab8` | `a0a9d2e` + `c372d3d` (this branch) | lost, `stranded`, trapped from step ~2716 to the end, 78 skips |

spin-check-4's trap, step 2716, at true (16238, -30197):

1. A survey waypoint (16242.5, -30197.5) lies inside a crater about 10 m
   across, too small for the 5 m orbital map.
2. Approaching from the east, the lidar could not see the near wall (it
   faces away; `steep_why` "shadow" at step 2713). It marked the floor as
   crater, which the planner only costs (`CRATER_COST`, sim/rover.py), not
   refuses. The Scout drove down a 24 deg wall to the waypoint.
3. Inside, the lidar sees every wall and marks it too steep. Every way out
   is closed. It steps 2 m, turns 180 deg, steps back, turns 180 deg until
   the time runs out. All 78 skips are the no-progress timer; the
   `hazard` skip never fired (the next goal was far away), and backing out
   is allowed once per dead end.

spin-check-2 trapped at the same crater. So the class is: **the Scout can
drive into ground it then cannot drive out of**, because its lidar judges
ground differently from outside and from inside. The back-and-forth guard,
the reversal rule and the skips act on the rocking, not the entry. Find
every site of the class before changing one: the planner's crater cost, the
step checks (`_feasible`, `_feasible_many`), the lidar's judgement
(sim/lidar.py), the skip and arrival rules (sim/campaign_camera.py
`drive_to`), the guard (`_note_move`) and the reversal (`_follow_sensing`).

`a0a9d2e` may have made this seed worse. One run each is not proof either
way. Settle it with replays before keeping or reverting any of its parts.

## How to test: small, and able to fail

`python -m sim.drive_check <run> --step N --steps M` replays a stretch of a
finished run without the camera in seconds (0.04 s a step). It holds the
estimate's error fixed and starts with an empty lidar map, so it can miss a
failure: from spin-check-4 step 2650 it drives past the crater; from step
2730 it reproduces the trap (4 blocked skips, 31 reversals). Improving its
faithfulness (the run's estimate per step, a lidar map seeded from the
run's `haz` records) is in scope.

**A replay is evidence only after it has shown the failure on the code that
failed.** Write `Reproduced:` with the numbers before any fix, then the
same replay after. A pass from a replay that never failed means nothing:
that is how 23 Nov's report went wrong.

Order: replays while building; `--set sortie_h=4` test sorties next; full
sorties (about 50 min each) only at the end. One sim at a time on this
machine (`ops/HANDOFF.md`, "How to run things"). Look at what each run
did: the path picture, the record's turns in place, reversals and skips.

## Rules

- Realism, not outcomes (`CLAUDE.md`). A fix is right because a real rover
  would do it, with a source (VIPER, GESTALT, the team's draft in
  `docs/sources/`) or marked `assumed`. Never tune to make seed 3 pass.
- Commit and push your branch early. Never merge to `main`. No `Agent` tool.
- Questions go to `leader` as one `question` mail. Then arm the watcher.
- Report with the protocol's headings. Never write "fixed" or "works"
  without the replay that failed before and passes now, and a full sortie
  you looked at.
