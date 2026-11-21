# Lantern handoff

20 Nov 2026, evening. For the next leader. **Non-authoritative**: it is
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

**Paused** at a checkpoint at the product owner's word. Both workers are
stopped and not watching their inboxes. Resume only when told.

- `lantern-workflow` (`C:\Monad-Hackathon-Project`) holds all merged
  work, last merge `15145ee`. `main` takes nothing without the product
  owner's word.
- `lantern-nav` (`C:\Monad-Hackathon-nav`, head `3ba89ad`) holds the
  scheduled position fixes. **Not merged**: it fails its test (below).
  It already contains lantern-workflow up to `d8888ce`.
- `lantern-sensors` is fully merged.

My rough estimate: about 70% of the way to the spec's "done when". The
workflow's three steps exist in `sim/campaign.py`; what is missing is
working position fixes, one end-to-end proof run, and the paper batch.

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

## Open problem: rovers spinning in circles

On 20 Nov evening the product owner watched three Scouts spin in circles
in Mission Control (port 8070). Not investigated: which run it was is
unknown, and it may be a multi-Scout run older than 20 Nov's changes.
My report that same evening, that most test sorties came home, was
wrong to reassure: those were one-Scout runs on one seed. Reproduce it
first (ask which run), then find the cause. Turning in place appears
in sortie records as `turns in place` and `pingpong` (sensors' item 6
report cut them from 503 to 38 on one run, so they were not zero).

## Open problem: the slope limit ignores direction

The planner, the lidar and the drive step all judge one number, the
overall tilt of the plane under the Scout's 1.55 m footprint
(`sim/footprint.py tilt_deg`), against `slope_limit_deg` = 15. Overall
tilt is the same whatever the heading, so switchbacks cannot help.

A real rover's limits depend on heading: driving up the fall line puts
all the slope into pitch (traction, power); driving across puts it into
roll (side slip, tip-over). At heading angle a off the fall line on a
slope s, pitch = atan(tan s cos a) and roll = atan(tan s sin a). A 20 deg
slope taken at 45 deg is about 14.4 deg pitch and 14.4 deg roll.

Sources so far:
- The team brief (`docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf`,
  p.7): "A 15° slope limit for routes (VIPER's own design limit)". Not
  yet found in the VIPER papers in `docs/sources/`. Unverified.
- Andrews 2020 VIPER overview (`VIPER-Andrews-2020-overview-NTRS-20205000864.txt`,
  slide 18) captions test photos "20º upslope" and "15º upslope with 45º
  cross slope". So VIPER was tested at different limits by direction.
- The brief also notes "Our slope numbers swing a lot with soil values".

The work: find VIPER's published pitch and roll limits (or the
mobility papers behind them), then make the planner, `_feasible` and the
drive step judge pitch and roll against the Scout's heading. A* then
needs heading in its state, or an edge cost by direction. Report how
stranding changes; do not pick limits for a better result. Suggested
owner: sensors, who owns the hazard map and planner.

## Suggested next steps

1. nav: preplanned landmark choice, then the straight-through test
   case. Short test runs while building.
2. Merge lantern-nav. Then one run with 2 or more Scouts, watched in
   Mission Control: the map grows, one Scout discovers a find, another
   checks it. This is the spec's first "done when", and, as far as I
   know, it has not yet played end to end on camera-driven Scouts.
   Unverified.
3. Agree the run count with the product owner.
4. The paper batch, preplanned and straight through side by side, then
   Figure 1 and the fleet and site tables. Due 15 Jan 2027. Site and
   video 10 Feb, proposal 24 Feb.

sensors has no open item. It could check the find-and-confirm logic
before step 2.

## How to run things without hurting the machine

- **One sim at a time on this machine**, across all workers: live_runs,
  calibrate, build_assets, render scripts. Three at once left under
  2 GB free on 20 Nov. Before starting one, check none is running:
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'live_runs|calibrate'`.
  To stop a run, end its whole process tree (`taskkill /PID <pid> /T /F`);
  live_runs restarts a killed child otherwise.
- **Short test runs while building**: `--set sortie_h=4` cuts a 20 min
  test sortie to about 4 min. Full 22 h sorties only for a merge check
  or anything about the return leg.
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

`nav` (branch `lantern-nav`, worktree `C:\Monad-Hackathon-nav`, brief
`docs/briefs/nav.md`) and `sensors` (branch `lantern-sensors`, worktree
`C:\Monad-Hackathon-sensors`, brief `docs/briefs/sensors.md`). Both Opus,
both through the mailboxes (`ops/PROTOCOL.md`); the product owner opens
their sessions. sensors owns the hazard map and planner in `sim/rover.py`
and the Mission Control web files since 20 Nov; nav owns the rest of
rover.py. The briefs predate the pause and the fix decision; update
nav's before resuming it. `ops/_old/` is untracked and not this
project's work; leave it.

## Running Mission Control

From `C:\Monad-Hackathon-Project`: `python app.py --no-open --port 8070`,
then open http://127.0.0.1:8070/ (IPv4 only, so `localhost` may fail).
For a new camera run: `python web/build_assets.py --run-ground
<batch>/<run>`, then `python -c "import app; app.sync_runs(None)"`. The
orbital-planning test sortie with the lidar map recorded is
`sensors-mc-check`; its unflagged steep step is at step 2860.
