# Lantern handoff

17 Nov 2026, 14:50 PST. This is the only current handoff. Read it first,
then `docs/campaign-spec.md` on `lantern-workflow`, then `ops/PROTOCOL.md`.

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

- **Spec finish line 1 is met.** Mission Control plays a real camera run:
  the map grows, a Scout logs a find, a second Scout searches, and the
  find is judged. The verdict so far is always "not confirmed".
- **Finish line 2 is not started.** Only test batches exist, one chosen
  seed per site. No paper numbers yet.
- Every Scout navigates by its live camera at about 0.03 s per
  Scout-step. One 3-run batch takes 10-25 min. Memory allows one batch at
  a time.

## Branches (pushed, nothing on `main`)

| Branch | Head | Holds |
|---|---|---|
| `lantern-workflow` | de465bc | Base, checked out in `C:\Monad-Hackathon-Project`: spec, briefs, this handoff; stale docs in `_archive/` |
| `lantern-workflow-sim` | 8dcf209 | Simulation fixes and approved terrain defaults; has `lantern-ground` merged |
| `lantern-workflow-web` | dc5a81f | Mission Control: real runs by default, workflow checkpoints, superseded list |
| `lantern-ground` | 38e94a6 | `docs/terrain/position.md`, `docs/SOURCES.md`, 15 new source PDFs |

## Workers (mailboxes in `ops/mailboxes/`)

| Handle | Folder | Doing |
|---|---|---|
| `workflow` | `C:\Monad-Hackathon-workflow-sim` | Running the approved-terrain main case and 7 test cases, one batch at a time, done about 16:05 (`data/campaigns/workflow-terrain-*`) |
| `control` | `C:\Monad-Hackathon-workflow-web` | Waiting for those batches: mark older batches superseded, play the main case |
| `ground` | `C:\Monad-Hackathon-ground` | Done; idle |

Mission Control runs at http://localhost:8050/ from `control`'s folder.

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

## Next, in order (proposed, not started)

1. `workflow`: check whether camera Scouts make the paper's planned
   position checks (`check_policy` "fixed", draft Table 1 spacing, 5 min
   stops). If not, build them. Scouts may only be fixing position when a
   crater happens into view. This must come before any paper numbers.
2. `ground`: source the ice sensor's false-alarm rate (3 of 5 recent finds
   were on no deposit) and map crater position error in shadow (about
   20 m per Mazarico 2025, against the assumed 2 m).
3. Leader: merge the four branches into one base.
4. Product owner: how many runs the paper needs (1,000 runs is about
   100 h at one batch at a time; estimate).
5. `workflow`: paper batch on random seeds, then Figure 1 and the fleet
   and site tables. Due 15 Jan 2027. Site and video 10 Feb, proposal
   24 Feb.

## Open for the product owner

- Run count (step 4).
- Approve steps 1-3.

## How to resume a worker

Mail it in `ops/mailboxes/<handle>/inbox/` per `ops/PROTOCOL.md`, then
tell it in its window to read its inbox. Watchers get killed when memory
runs short; restart them by hand.
