# Lantern handoff

17 Nov 2026, 16:40 PST. This is the only current handoff. Read it first,
then `docs/campaign-spec.md`, then `ops/PROTOCOL.md`. No workers are
running: every worker of 17 Nov is retired.

## What Lantern is for

The product owner's words: "upgrade hackathon's lantern to what our paper
proposes, which is a fundamental workflow for these rovers to map,
corroborate, and discover resources." And: "this is not about forcing
rovers to come home but developing a wholistic and realistic simulation
according to our papers proposed workflow, and if workers cant come home,
then fine shadownav isnt accurate enough."

A change is right when it makes the sim more faithful to the paper or the
Moon. It is wrong when its purpose is a better outcome.

## Where it stands

- **Everything is on one branch, `lantern-workflow`**, checked out in
  `C:\Monad-Hackathon-Project`: simulation, Mission Control, terrain
  position and sources. Nothing is on `main`.
- **Spec finish line 1 is met.** Mission Control plays a real camera run:
  the map grows, a Scout logs a find, a second Scout searches, and the
  find is judged. The verdict so far is always "not confirmed".
- **Finish line 2 is not started.** Only test batches exist, one chosen
  seed per site. No paper numbers yet.
- Every Scout navigates by its live camera at about 0.03 s per
  Scout-step. One 3-run batch takes 10-25 min. Memory allows one batch at
  a time.

## Approved-terrain batches (one chosen seed per site; not statistics)

Run with `python -m sim.live_runs` into `data/campaigns/<batch>/`.

| Batch | State | Sorties home / total (s89_134e, s89_014e, haynes) | Finds | Confirmed |
|---|---|---|---|---|
| `workflow-first3d` (old terrain, for comparison) | done | 1/4, 2/5, 5/7 | 1 | 0 |
| `workflow-terrain-main` | done | 6/7, 1/4, 1/4 | 2 | 0 |
| `workflow-terrain-rough021` | done | 7/8, 2/5, 3/5 | 2 | 0 |
| `workflow-terrain-rough048` | done | 3/5, 0/3, 3/5 | 1 | 0 |
| `workflow-terrain-dens106` | cut off at 15:18 by a shutdown, 1 run | | | |
| density 1,540; map cut 18 m; slope at cell centre; scale walk 0.001 | not run | | | |

Not yet reported or played: nobody has read these per sortie, and Mission
Control has not been checked on them. Older batches still need the
"old terrain" superseded reason in `web/js/superseded.js`.

## Decided

- Live camera navigation in every run; paper numbers from many runs.
- Terrain (`docs/terrain/position.md`): roughness 0.035 m rms (test 0.021,
  0.048); crater density 106-1,540 per km² (336 central); map cut 10 m
  (test 18 m); slope test with half-width widening; no rocks, stated.
- Confirm rule stays 6 m, marked assumed: no source for deposit size.
- Descent corridor planned on LOLA slope; Scouts drive the run ground.
- Filter scale walk 0, as in A24 eqs. 3-6 (0.001 is a test case).
- Lost reasons include `stranded` and `stuck`. No free hop home.
- Beacons only as the brief proposes them: comms relays, not homing.
- With truth hidden, views draw the believed pose.
- Mission Control opens the newest batch with no superseded reason; test
  cases never open by default.

## Findings so far

- Five bugs fixed: back-and-forth driving, skipped waypoints counted as
  reached, a free hop home up to 2.4 km, cut-short searches never judged,
  heading drift despite the star tracker.
- The old roughness was about twice real ground. It caused most strandings.
- The filter is ShadowNav as published, about 1.7x cautious along track.
  After about 1 km between crater passes it cannot certify 6 m, even when
  its position is right.
- Searches have found real deposits (13 of 13 readings on one) and still
  been judged "not confirmed" on the 6 m rule.
- s89_014e's way in is too steep on LOLA alone; its Scouts survey little
  under any terrain setting.

## Next, in order (proposed, not started; each gets a new worker)

1. Finish the terrain test cases: resume `workflow-terrain-dens106`, run
   the four not run, report each against `workflow-terrain-main`, and have
   Mission Control mark the older batches superseded.
2. Planned position checks: do camera Scouts make the paper's checks
   (`check_policy` "fixed", draft Table 1 spacing, 5 min stops)? If not,
   build them. Scouts may only fix position when a crater happens into
   view. This must come before any paper numbers.
3. Research: source the ice sensor's false-alarm rate (3 of 5 finds in one
   test were on no deposit) and map crater position error in shadow
   (about 20 m per Mazarico 2025, against the assumed 2 m).
4. Product owner: how many runs the paper needs (1,000 runs is about
   100 h at one batch at a time; estimate).
5. Paper batch on random seeds, then Figure 1 and the fleet and site
   tables. Due 15 Jan 2027. Site and video 10 Feb, proposal 24 Feb.

## Open for the product owner

- Run count (step 4).
- Approve steps 1-3.

## Workers

None running. Retired 17 Nov: `workflow` (simulation), `control`
(Mission Control), `ground` (terrain research). Their briefs are in
`_archive/briefs/`, their mailboxes in `ops/mailboxes/_old/`, and their
branches (`lantern-workflow-sim`, `lantern-workflow-web`, `lantern-ground`)
are merged or copied into `lantern-workflow`.

A worker retires when its brief is done: work merged into
`lantern-workflow`, brief to `_archive/briefs/`, mailbox to
`ops/mailboxes/_old/`, handle struck from `ops/PROTOCOL.md`. A new job
gets a new worker with a fresh brief in `docs/briefs/`, branched from
`lantern-workflow`.

## Running Mission Control

From `C:\Monad-Hackathon-Project`: `python app.py --no-open --port 8050`,
then open http://localhost:8050/. app.py builds each camera run's ground
and re-indexes the runs.
