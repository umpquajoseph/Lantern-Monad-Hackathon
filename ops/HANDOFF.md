# Lantern handoff

20 Nov 2026, evening; "Where it stands", the spinning and slope sections
and "Next steps" rewritten 24 Nov. For the next leader. **Non-authoritative**: it is
one leader's notes, not orders. Use your own judgement, and check every
claim here against the code, the runs and the sources before relying on
it. It replaces the handoff of 20 Nov morning (in git history at
`4b2fe75`), which holds the full ShadowNav comparison record.

Read next: `docs/campaign-spec.md` (it wins over this file), then
`ops/PROTOCOL.md`. Anything in `_archive/` is history.

## What Lantern is for

The product owner, 20 Nov: "this is not to check arbitrary boxes but to
bring the sim up to parity with shadownav and the workflow our paper
proposes." The workflow is the spec's: Scouts map, discover a possible
resource, and a second Scout corroborates it. A change is right when it
makes the sim more faithful to the paper or the Moon. It is wrong when
its purpose is a better result. Scouts that cannot get home under honest
settings are a result for the paper.

## Where it stands

**Resumed 24 Nov** at the product owner's word. The leader does the
survey-length fix and the multi-Scout proof run; `nav` builds the
preplanned position fixes. `sensors` stays paused; `drive` is done.

- `lantern-workflow` (`C:\Monad-Hackathon-Project`) holds all merged
  work, last merge `18ae1da` (the driving work: `lantern-heading` and
  `claude/lantern-drive-8d2ae1`, sim code identical to `9311549`).
  `main` takes nothing without the product owner's word.
- `lantern-nav` (head `3ba89ad`, plus a merge of `lantern-workflow`
  `18ae1da`) holds the scheduled position fixes. **Not merged**: its
  crater choice fails its test (below). nav resumes from it.
- `lantern-sensors` is fully merged.

What is missing for the spec's "done when": working position fixes, one
end-to-end multi-Scout proof run, and the paper batch. Every run since
17 Nov has had one Scout.

## What changed on 20 Nov

| Change | Commit | Source |
|---|---|---|
| Filter matches rims to the near half of each mapped crater, skips updates over 20 m from any mapped rim | `e50fd8f` | A24 sec. III-E2 |
| calibrate.py runs on the approved run ground, with body tilt and A24's 2% odometry truth | `f816766`, `e50fd8f` | A24 eqs. 4-6 |
| Lidar false craters on plain slopes fixed; C23's far-side test for rim points; stereo holes and RMS by range | merged `a6a61a2` | C23; A24 Table 1, 1.5 m row |
| Ice sensor noise from VIPER's NSS (0.5 wt% at 3 sigma), with the polar background hydrogen (0.13 wt%) | merged `a6a61a2` | `docs/sources/` VIPER, LEND papers |
| Orbital map: ShadowCam's ~20 m shared geolocation offset per image | merged `a6a61a2` | Mazarico 2025; ShadowCam factsheet |
| Hazard map kept as GESTALT does: cells merge every scan and are re-judged, so they can reopen | merged `d8888ce` | Goldberg 2002 sec. 4 |
| Scouts plan on the LOLA 5 m slope plus their own lidar map, not the true ground (`plan_slope` "orbital"; "truth" is the test case) | merged `d8888ce` | VIPER strategic planning papers |
| Mission Control replays the map the Scout plans on and rings unflagged steep steps | merged `15145ee` | display only |

Each source has a copy in `docs/sources/` and a line in `docs/SOURCES.md`.

## Findings for the paper so far

- **ShadowNav comparison.** On the approved terrain, Lantern ends its
  three traverses 3.9-6.3 m off against C23's 1.7-3.8 m. The product
  owner ruled out building traverses to match C23 (ShadowNav's map is not
  public), so this is reported as a finding, not a gate. Details in the
  previous handoff at `4b2fe75`.
- **The haynes return ramp is LOLA's own steepness**, not an artefact.
  Before the GESTALT hazard map, Scouts stranded there; now they cross.
- **Orbital planning misses some steep ground.** In one sortie a Scout
  stepped once onto 19.4 deg ground that LOLA read as 4.0 deg, hidden from
  the lidar by shadow. The ground is built from LOLA, so the real Moon
  would likely show more of these.
- **Fixes cost time.** In nav's runs that came home, each fix took about
  0.35 h: about 6 min stopped plus a 90-110 m detour. At the draft's
  80 m spacing that roughly triples drive time. The draft's "88 x 5 min"
  leaves out the detour.

## Open problem: position fixes (nav)

The team's area 3 draft (sec. 4.2, lines 43-47 of its text copy) has
Scouts "schedule fixes by distance driven rather than by the filter's own
estimate", every 80 m at a 10 m limit. nav built this in camera runs: a
half survey of one landmark crater (7.5-20 m) at rim + 10 m, a 1 min stop
per long-exposure image. The survey itself works. Choosing the crater
does not: picking the nearest crater ahead when a fix falls due either
loops back to one crater (58 surveys in one run) or finds none (195
"fix_unavailable" in another). nav's report of 20 Nov 15:18 in
`ops/mailboxes/leader/archive/` has the run table.

**Decided by the product owner, 20 Nov:** preplanned is the default,
since ShadowNav is the precedent. Landmark craters are chosen when a leg
is planned, along the route at Table 1's spacing, and the route drives
toward them (A24 sec. III-E2). The comparison case for the paper is
straight through: fix on landmark-sized craters as they come into view,
facing them, as ShadowNav's field tests did (A24, text lines 500-509).
Compare fix error and drive time. The product owner was not aware the
draft had scheduled fixes, so expect the draft to be revisited with
these numbers.

Also unexplained in nav's runs: with repeated fixes on one crater the
filter settled about 4.8 m off with a 1.6 m bound (Mahalanobis median
7.3). C23 Table 2 shows ShadowNav's own filter understating its error,
so this may be faithful. Check before changing anything.

## Scouts stuck or spinning (drive, 23 Nov)

`drive` (`docs/briefs/drive.md`) found the class: a Scout could drive
into a small crater its lidar misread from the rim, then find every wall
over the limit from inside. Fixed by judging slope on crater cells
(`f2b6c82`) and, at the product owner's choice, retracing the track when
the map closes every way out (`9311549`). Full one-Scout sorties on
haynes seeds 1-3 (`drive-full-s1/s2/s3`) came home with no stuck
episode; turns in place 13, 18 and 33. Turns in place are not faults by
themselves: a real rover turns in place too. Only ones a replay shows
to be thrashing are faults. The three Scouts the product owner saw
spinning on 20 Nov were a multi-Scout run, never reproduced; the proof
run is the first multi-Scout run on today's code.

## Slope limits by heading (done, `a0a9d2e`)

The planner, the lidar and the drive step judge pitch along the heading
against `pitch_limit_deg` and roll across it against `roll_limit_deg`,
both 15 deg (`sim/settings.py`). Pitch: Bluethmann, LSIC 2024, p.4
(copy in `docs/sources/`). Roll: Rezich et al. 2025 sec. 3.4, read
online, no copy in `docs/sources/` yet.

## Proof run, 24 Nov (`proof-2x2/w00003-d06bbd2b`, code `d835d3c`)

2 Scouts x 2 sorties, haynes seed 3, watched in Mission Control. Before
`cam_fix_mode`: the filter corrected on every frame with rims (now the
"opportunistic" test case), not on the draft's scheduled fixes. All four sorties home and completed, no Scout lost.
S1-01 logged find 1 (1.7 wt%, bound95 26.3 m, true error 18.9 m) at
day 1 02:01. S2-02 searched it (day 2 03:37-05:41, radius 32.8 m):
`unconfirmed`, `bound_too_large`, combined bound 31.8 m against the 6 m
confirm rule, with a real deposit there. The shared map gained one
crater (C1) from two sightings, at S2-02's docking. This is the spec's
first "done when", on camera Scouts.

Turns in place: 19, 23, 18 and 55 (S2-02, 37 of them the search
pattern's line ends). Most are re-aims with progress after. The
suspect ones are 2-4 spells per sortie of opposite turns near a crater
rim, the plan flipping sides as the lidar map fills in (S1-01 steps
2685-2690, 3326-3330; S1-02 3503-3512; S2-01 1356-1358, 2187-2189).
`drive_check` from S1-02 step 3490 (`--warm 100`) did NOT reproduce it,
so nothing was changed.

## Next steps (agreed with the product owner, 24 Nov)

1. Merge the driving work into lantern-workflow. Done, `18ae1da`.
2. Survey-length fix. Done, `d835d3c`: `survey-fix-s2` reran
   drive-full-s2 exactly (3,764.9 m) and now reads `survey_end` "lanes",
   survey done, completed.
3. A run with 2 or more Scouts, watched in Mission Control. Done, above.
4. Turn-arounds: fix only the ones the proof run shows are faults, each
   with a replay before and after. Not reproduced yet (above).
5. Position fixes (nav): preplanned landmark choice, straight through as
   the comparison case. Done, merged at `0c6166f` (below).
6. Rerun step 3 with the fixes on.

## Position fixes, 24 Nov (nav, merged `0c6166f`)

`cam_fix_mode` "preplanned" (default): the landmark for each of the
draft's fixes (every 79 m at the 10 m limit, draft lines 45-48) is
chosen when its leg is planned, a 7.5-20 m crater beside the route, and
the Scout drives a half survey of it, 1 min stops every 10 m (A24 line
401, line 794, sec. V-D). "straight": fix on landmark craters as they
come into view within `fix_view_m` 20 m (A24 sec. IV-B). A fix whose
exposures saw no rim does not reset the schedule. nav's report:
`ops/mailboxes/leader/archive/20261124-221500_nav_report.md`.

Haynes seed 3, one full sortie each, numbers checked against the records:

| run | mode | h | survey m | turn back | fixes updated | true error p50 / max m |
|---|---|---|---|---|---|---|
| drive-full-s3 | opportunistic (old) | 14.1 | 3,861 | none | every frame | 5.0 / 32.8 |
| nav-pre-s3a | preplanned | 19.5 | 569 | time, 9.6 h | 31 of 40 | 1.4 / 5.1 |
| nav-str-s3a | straight, 20 m | 17.1 | 3,786 | none | 8 of 51 | 44.2 / 102.7 |
| nav-str12-s3a | straight, 12 m (sensitivity) | 15.9 | 3,800 | none | 7 of 41 | 41.1 / 84.1 |

For the paper: at the draft's spacing a preplanned fix costs a median
0.34 h (6 min stopped, a 99 m survey drive), so fixes fill the sortie
and the Scout surveys a seventh of the baseline's ground, within the
10 m limit. Straight through, on a route not built to pass landmarks at
survey range, saw rims in 4% of exposures and did not hold position.
One seed, one Scout, one sortie per mode: no spread yet. The 20 Nov
"4.8 m off with a 1.6 m bound" was the map's own 3.5 m error on the
landmark; C23 Alg. 1 treats the map as truth, so it stands.

`live_runs --set`: a second `--set` flag replaces the first. Give
several overrides in one flag, `--set a=1 b=2`.

Then: agree the run count with the product owner; the paper batch,
preplanned and straight through side by side; Figure 1 and the fleet
and site tables. Due 15 Jan 2027. Site and video 10 Feb, proposal
24 Feb.

## How to run things without hurting the machine

- **One sim at a time on this machine**, across all workers: live_runs,
  calibrate, build_assets, render scripts. Three at once left under
  2 GB free on 20 Nov. Before starting one, check none is running:
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'live_runs|calibrate'`.
  To stop a run, end its whole process tree (`taskkill /PID <pid> /T /F`);
  live_runs restarts a killed child otherwise.
- **Short test runs while building**: `--set sortie_h=4` cuts a 20 min
  test sortie to about 4 min. With preplanned fixes (since `0c6166f`)
  a 4 h or 10 h sortie turns back before the lanes, so it tests the
  corridor only. Full 22 h sorties only for a merge check or anything
  about the return leg.
- A test sortie: `python -m sim.live_runs --sites haynes --scouts 1
  --sorties 1 --batch-id <name> --serial --progress 500`.

## Decided

- Live camera navigation in every run.
- Terrain as `docs/terrain/position.md`: roughness 0.035 m rms (test
  0.021, 0.048); crater density 106-1,540 per km² (336 central); map cut
  10 m (test 18 m); footprint slope test (test cases "widened", "point");
  ShadowNav camera texture; no rocks, stated.
- Filter: A24 near-half matching and 20 m gate; scale walk 0, as in A24
  eqs. 3-6. No parity test against C23.
- 15 deg is a planning limit, not a physical one. Scouts strand only where
  the true ground stops them; unflagged steep steps are counted.
- When a Scout's own map closes every way out of where it stands, it
  retraces its own recent track (`retrace_track_m`, 50 m, assumed),
  ignoring the map, until its map leaves a way out. Its climbs over the
  limits still count as steps over the limits; the record counts
  `retraces`. Product owner, 23 Nov, over a tilt stop or stranding.
- Position fixes: preplanned by default, straight through as the test
  case (above).
- Confirm rule stays 6 m, assumed. Dock registration stays 6 m, assumed;
  not settled (it waits on draft item 15).
- Lost reasons include `stranded` and `stuck`. No free hop home.
- Beacons only as comms relays, not homing.
- Mission Control never opens a test case by default.
- Hosting: local only until the product owner says it is right; never
  republish lantern-teal.vercel.app.

## Workers

From 24 Nov `nav` (brief `docs/briefs/nav.md`, rewritten 24 Nov; a
session the leader starts, its branch cut from `lantern-nav`, its
worktree under `.claude\worktrees\`, Opus, high effort) builds the
position fixes through the mailboxes (`ops/PROTOCOL.md`). `sensors`
(branch `lantern-sensors`, brief `docs/briefs/sensors.md`) is paused;
it owns the hazard map and planner in `sim/rover.py` and the Mission
Control web files. `drive` (brief `docs/briefs/drive.md`) is done and
merged. The leader owns `sim/campaign.py`'s lane loop and `_completion`
for the survey fix. `ops/_old/` is untracked and not this project's
work; leave it.

## Running Mission Control

From `C:\Monad-Hackathon-Project`: `python app.py --no-open --port 8070`,
then open http://127.0.0.1:8070/ (IPv4 only, so `localhost` may fail).
For a new camera run: `python web/build_assets.py --run-ground
<batch>/<run>`, then `python -c "import app; app.sync_runs(None)"`. The
orbital-planning test sortie with the lidar map recorded is
`sensors-mc-check`; its unflagged steep step is at step 2860.
