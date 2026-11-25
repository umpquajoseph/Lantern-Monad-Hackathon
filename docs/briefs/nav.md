# Brief: `nav` (the paper's position fixes, preplanned)

Handle `nav`. Rewritten 24 Nov 2026; the 20 Nov brief is in git
history. Branch: the one your session starts on, cut from `lantern-nav`
(which holds `lantern-workflow` `18ae1da`). Leader: `leader`. Opus, high
effort.

Read first, in this order: `ops/HANDOFF.md`, `docs/campaign-spec.md`,
this brief, `ops/PROTOCOL.md`. Then nav's 20 Nov reports in
`ops/mailboxes/leader/archive/` (`20261120-151843_nav_report.md`, and the
15:39 addendum) for the run table. Anything in `_archive/` is history.

## What this is for

The product owner, 24 Nov: "i just want the sim to mirror the workflow
the paper proposes so we have something of substance besides just a
theoretical model, and so we have something to build on top of
shadownav." The paper's Scouts take position fixes on landmark craters.
Lantern's camera Scouts must take them the way the paper and ShadowNav
describe, so the paper can quote what fixes cost and what they buy. A
change is right when it makes the fixes more faithful to the draft, C23
or A24. It is wrong when its purpose is a better result. If honest
fixes leave Scouts lost, that is a result for the paper.

## What the sources say (checked 24 Nov against the text copies)

- Area 3 draft, `docs/sources/area3-navigation-draft.txt` lines 45-48:
  the Scouts "schedule fixes by distance driven rather than by the
  filter's own estimate"; with a 10 m limit, the false-match traverse
  needs "a fix every 80 m, about 88 per sortie", the best traverse one
  every 358 m (Table 1, line 57 on).
- A24, `docs/sources/ShadowNav-Atha-2024.txt` line 401: landmark craters
  "require a planned trajectory to drive towards". Line 794: accuracy is
  best "when a half survey around the landmark crater occurs".
- A24 lines 505-509, the field tests: the rig stopped for long exposures
  every 5-10 m and "was directed toward large craters if any were
  present". That is the straight-through case below.

Check each of these yourself before relying on it.

## Where it stands

Your branch has nav's 20 Nov work (`9396c65` to `3ba89ad`):
`cam_fix_mode` "scheduled" (filter updates only at a fix's observation
stops; odometry between), the half survey at rim + `fix_survey_offset_m`,
1 min exposure stops (`fix_exposure_stop_s`, A24 sec. V-D). The survey
works. **Choosing the crater does not**: picking the nearest landmark
ahead when a fix falls due looped on one crater (58 surveys in
`nav-fix-check7`) or found none (195 `fix_unavailable` in another run).
`nav-fix-check7` was lost on battery at 37.7 h.

It now also has the 23 Nov driving work, merged clean by text but never
run together with your fix code: slope limits by heading, slope judged
on crater cells, and the retrace out of a dead end (`sim/rover.py`
`_start_retrace`).

## Decided (product owner)

- **Preplanned is the default.** When a leg is planned, the landmark
  craters for its fixes are chosen along the route at Table 1's spacing,
  and the route is planned to drive toward each one (A24 line 401). The
  Scout chooses from its map (the orbital map plus what it has mapped),
  never from the true ground.
- **Straight through is the comparison case.** No preplanned landmarks.
  The Scout drives its route and fixes on landmark-sized craters as they
  come into view, facing them for the exposures, as A24's field tests
  did. This is not the old `opportunistic` mode (every frame against the
  whole map), which stays a labelled test case.
- Compare the two on fix error, drive time and detour per fix.

## The work, in order

1. **Run what you were given.** One short test sortie on your branch as
   it starts (`python -m sim.live_runs --sites haynes --scouts 1
   --sorties 1 --batch-id <name> --serial --progress 500 --set
   sortie_h=4`). Look at the path and the record: does the retrace or
   the heading limits clash with a fix's survey?
2. **Preplanned landmark choice.** Where in the leg's planning it
   happens; which craters qualify (`fix_crater_d_m`, A24 sec. V-B); what
   the Scout does when no qualifying crater lies near a scheduled
   point; how the route bends toward the landmark and what the detour
   costs. Every choice cites the draft, C23 or A24, or is a named
   setting marked `assumed`. Decide and build. Mail `leader` one
   `question` only if a choice changes what the paper will say.
3. **Straight through**, as a `cam_fix_mode` value.
4. **Test sorties** (`sortie_h=4`) while building, each looked at: the
   path, where each fix happened, the error and bound trace.
5. **Full sorties at the end**: haynes seed 3, preplanned and straight
   through. The no-fix baseline on identical driving code is
   `data/campaigns/drive-full-s3` (`9311549`). Report fix count, fix
   error after each fix, drive time, detour per fix, outcome.

Also unexplained from 20 Nov: with repeated fixes on one crater the
filter settled about 4.8 m off with a 1.6 m bound (Mahalanobis median
7.3). C23 Table 2 shows ShadowNav's own filter understating its error,
so this may be faithful. Check against C23 Algs. 1-3 and A24 eqs. 3-6
before changing the filter.

## Your files

`sim/nav.py`, `sim/campaign_camera.py` (not `drive_to`'s stuck handling
unless a fix needs it; say so in your report), `sim/rover.py` outside the
hazard map, planner and retrace, `sim/live_runs.py`, and the `cam_*`,
`fix_*`, `check_*` and `pf_*` fields of `sim/settings.py`. **The leader
is editing `sim/campaign.py`'s lane loop and `_completion`** for the
survey-length fix; do not touch those. A format change in
`docs/handoffs.md` is told to `leader` first and made in the same commit.

## Running sims

One sim at a time on this machine, across every session. The leader
will be running multi-Scout proof runs. Before any `live_runs`, check
none is running:
`Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'live_runs|calibrate|build_assets'`.
If one is, mail `leader` an `fyi` that you are waiting for the machine,
arm your watcher and end your turn; the leader mails when it is free.
`python -m sim.drive_check` replays and pure planning scripts are cheap
and may run any time.

## Rules

- Check every claim against the source, never memory or a summary, this
  brief included.
- Look at what you made: the path picture, the fixes on it, the error
  trace; for the full sorties, the run in Mission Control. An exit code
  is not a look. Never write "fixed" or "works" without that look.
- Commit and push your branch early and often. Never merge into
  `lantern-workflow` or `main`; `leader` merges.
- No `Agent` tool. Report with the protocol's headings.
