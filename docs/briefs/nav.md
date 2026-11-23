# Brief: `nav` (camera navigation and the paper's position fixes)

Handle `nav`. Branch `lantern-nav`, worktree `.claude\worktrees\nav`,
from `lantern-workflow`. Leader: `leader`. Model: Opus.

Read first, in this order: `ops/HANDOFF.md`, `docs/campaign-spec.md`, this
brief, `ops/PROTOCOL.md`. Then the sources named below, in
`docs/sources/`. Anything in `_archive/` is history and sets no scope.

## Goal

Bring the Scouts' navigation up to parity with ShadowNav and with the
workflow the team's paper proposes. The product owner, 20 Nov: "this is
not to check arbitrary boxes but to bring the sim up to parity with
shadownav and the workflow our paper proposes." A change is right when it
makes a Scout navigate more as ShadowNav (C23, A24) and the area 3 draft
(sec. 4.2-4.3) describe. It is wrong when its purpose is a better result.
If honest settings leave Scouts lost, that is a result for the paper.

## Where it stands (20 Nov)

- The filter matches C23 Algs. 1-3 and A24 eq. 3. Since `e50fd8f` it also
  matches rim points only to the near half of each mapped crater and skips
  the update when its estimate is over 20 m from every mapped rim (A24
  sec. III-E2; `Settings.pf_match`, `pf_gate_m`).
- On the approved terrain Lantern ends traverses 3.9-6.3 m off against
  C23's 1.7-3.8 m. The product owner ruled out building traverses to
  match C23's: ShadowNav's map is not available. That gap is a finding.
- Camera runs fix whenever a crater happens to be in view. The paper does
  not work that way.

## The work, in order

1. **Overconfidence after a wrong match.** Run one test sortie on the
   current code (`python -m sim.live_runs --sites haynes --scouts 1
   --sorties 1 --batch-id <name> --serial --progress 500`, about 10 min;
   single sorties only, no batches). Watch it in Mission Control. The
   handoff's step 3 describes the failure: one wrong match shrinks the
   filter's 95% bound under 1 m while the true error is about 6 m, and
   later correct rims no longer pair. Find out whether the near-half
   matching and 20 m gate changed that. If it persists, find the cause.
   Check against A24 eqs. 3-6 and C23 Algs. 1-3 before changing the
   filter. C23 Table 2 itself shows a filter whose reported uncertainty
   understates its error (Mahalanobis 2.75-8.74); reproducing that is
   faithful, inventing a cure ShadowNav does not have is not.
2. **The paper's position fixes in camera runs.** Draft sec. 4.2: the
   Scouts "schedule fixes by distance driven rather than by the filter's
   own estimate", at Table 1's spacing (80 m at the 10 m limit on the
   false-match row). Draft item 11: whether a fix needs a stop is open;
   `Settings.check_stop_s` = 300 s from "if each fix takes five minutes".
   A24 sec. III-E2: ShadowNav's landmarks are craters the rover's
   "planned trajectory" drives toward, and its best result is a half
   survey (A24 Table 2). Design how a camera Scout takes a scheduled fix:
   which mapped crater it goes to, how it approaches, how long it stops,
   what it costs in time and energy, what happens when no mapped crater is
   in reach. The sampled engine (`sim/campaign.py`, `check_policy`) already
   has the policies; camera runs (`sim/campaign_camera.py`) do not. Every
   choice cites the draft, C23 or A24, or is a named setting marked
   `assumed`. Send the design to `leader` in one `question` mail before
   building it, since it shapes what the paper says.
3. Then a test sortie with the fixes on, watched in Mission Control.

## Your files

`sim/nav.py`, `sim/campaign_camera.py`, `sim/rover.py` (except its hazard
map and planner, which moved to `sensors` on 20 Nov), `sim/campaign.py`,
`sim/live_runs.py`, and the navigation and
`check_*` / `fix_*` / `pf_*` / `cam_*` fields of `sim/settings.py`.
`sensors` owns `sim/lidar.py`, `sim/ice.py`, `sim/orbital_map.py` and
their settings; do not edit those. If you need a change there, mail
`leader`. A format change in `docs/handoffs.md` is told to `leader`
first and made in the same commit.

## Rules

- Check every claim against the source, never memory or a summary,
  this brief included.
- A number from a source names it in a comment; anything else is a named
  setting marked `assumed`.
- Look at what you made: the sortie playing in Mission Control
  (`python app.py --no-open --port <your port>`; see the handoff), the
  camera frames, the error trace. An exit code is not a look.
- Commit and push `lantern-nav` early and often. Never merge into
  `lantern-workflow` or `main`; `leader` merges.
- Decide, build, check, push, then report. Ask `leader` only about scope,
  what the paper will say, and merges.
