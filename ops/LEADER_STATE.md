# Leader state (lead session, handle `leader`)

Rewritten 2026-11-15 12:31 PST before autocompact. The full earlier log is in
git at 73e77ab. Read this first after a compaction, then `ops/PROTOCOL.md`,
`docs/WORKING.md` and `docs/handoffs.md`. Append below "Log"; do not rewrite.

## How Joseph wants to work

- **Short answers.** He asks "tldr" often. Answer first, plain words, then
  a table if useful. No option lists unless asked. He gets frustrated by
  complexity and by me managing piecewise.
- **No choice buttons and no AskUserQuestion.** Ask plain-text questions at
  the end of a reply. No memories saved without asking.
- **Workers have their own agency.** Briefs give the goal, the why (quote
  Joseph), constraints and how it will be checked. They do not give the
  method (no worker counts, no tile choices). He reminded me at 12:19.
- **No faking to look right.** Realism must be real. Honest labels for
  everything made up. He caught me proposing a live browser first-person
  view (cancelled) and the "simulate first, render later" mistake: sensor
  frames must stay live in the sim loop.
- I am the leader: decide small things myself and tell him. Ask only real
  decisions. Check facts before saying them; measure before claiming.
- Times come from `date`, never guessed. Commits never name a person.
- Shell gotcha: an unquoted heredoc with backticks breaks mail files. Use
  `<<'EOF'` with a `ts: TS` placeholder and `sed`, or the Write tool. Check
  a mail file is not empty after writing it.

## Scope and decisions (current)

- **Scope: terrain, scene rendering, navigation, and the site.** Chain and
  hosting are OFF (Joseph about 12:00: "the chain shit doesn't need to come
  in, and we dont need to deploy to a host yet").
- **The localhost site is the base.** `python app.py` from
  `C:\Monad-Hackathon-integration` (branch `integration`) serves it. A part
  is done when it shows there. `integration` is the single merge point. The
  integrator merges worker branches into it, and I merge `integration` into
  `joseph` after review. PENDING: `joseph` is behind `integration`; merge
  after run 2 lands and the second judge pass.
- Two renderers are OK (Joseph about 12:00) as long as both use the same
  terrain and the same timing. First person: `sim/scene.py`, recorded
  frames. Third person: three.js live. Sensor frames (every 2 m step) are
  rendered live in the loop; display frames (`fp_sub`, every 0.5 m) are
  rendered after, in parallel, labelled display-only.
- `standins` lists only parts still to be built. Real parts get a top-level
  key: `nav`, `planner` (`docs/handoffs.md`, dc713e7).
- Real craters (Hemmi CR1) dropped. Shoemaker LOLA + synthetic craters stay.

## Waiting on Joseph

1. **Sigma formula.** ParticleNav reports `sqrt(trace(cov)/2)`. With fixes
   off, run 2 A shows about 15 m against a 22 m true error. The alternative
   is the largest-eigenvalue form (Cauligi 2023 sec. 6B). The viewer shows
   it unadjusted until he decides.
2. **Vision input frames.** Terrain found about a third of the rim shadow
   comes from invented lips. It recommends vision read `display="detail"`.
   I agree. Asked at 12:26.

## Workers (all share the name "Monad Hackathon protocol setup"; use addresses)

| handle | doorbell | session | worktree, branch | job now |
|---|---|---|---|---|
| scene | `uds:\\.\pipe\LOCAL\cc-msg-003a54f5030ab1d0041668abdbf2f6dc` | local_e67898fc-0303-4de5-ba17-b09860f190e7 | `C:\Monad-Hackathon-scene`, `step-6-scene` | rerun 1108 running (run 2 about 12:50); then a fast rerun with new code: parallel display frames, B start fix, particles logged (bc9f6ea), planner key; camera frame looks flat grey at panel size (judge item 3) |
| viewer | `uds:\\.\pipe\LOCAL\cc-msg-a233f82a3c78cbd382c1279676ac4ac8` | local_ea255feb-3f18-40f4-80e5-767adfa4e3e7 | `C:\Monad-Hackathon-viewer`, `step-5-viewer` | owns `web/js`. First fp_sub playback (smooth camera), then the particle cloud display |
| terrain | `uds:\\.\pipe\LOCAL\cc-msg-3956fbcec9b14cf96512bbeaf1a4ac16` | local_f1ad01df-54d1-41c3-a8ba-611e840a57d3 | `C:\Monad-Hackathon-terrain`, `step-6-rim-truth` | answer key done (644cf16); revalidates on the new runs. Realism checker for scene and viewer |
| integrator | `uds:\\.\pipe\LOCAL\cc-msg-e4aa78077ba4f6511766cd4cc20730b8` | local_e783098d-945c-48ad-9f91-90350cb94056 | `C:\Monad-Hackathon-integration`, `integration` | owns `app.py`. Judge pass 1 done (7 items). Second pass after run 2 lands |
| designer | `uds:\\.\pipe\LOCAL\cc-msg-42257a837952d8f3cb6dda87cb29e0fb` | local_ba34eb33-eb0d-45e1-8f58-6d1df15566b9 | own worktree, `step-5-design` | owns `web/app.css` and `index.html` layout. Judge items 1, 4, 5, 6, 7. Brief `docs/briefs/design.md` |
| perf | `uds:\\.\pipe\LOCAL\cc-msg-b5fa754137f62a9b8f31896194e2668b` | local_e264d133-dfe3-4b08-a7be-8c66b5b4e005 | `C:\Monad-Hackathon-perf`, `step-6-perf` | rerun and asset build speed, identical output. Brief `docs/briefs/performance.md`. Profiles after 1250 lands |
| merger | `Inbox triage and monitoring [37f8c1]` | opened by Joseph 13:07 | `C:\Monad-Hackathon-checkpoint`, `checkpoint` | checkpoint merge + clean-up, brief `docs/briefs/merge.md` |

My watcher (run_in_background, re-arm after every mail):

    timeout 43200 bash -c 'd="C:/Monad-Hackathon-Project/ops/mailboxes/leader/inbox"; seen=$(ls "$d"); while true; do new=$(comm -13 <(echo "$seen") <(ls "$d") | grep -v "^$"); if [ -n "$new" ]; then echo "mail: $new"; exit 0; fi; sleep 5; done'

Read mail, then `mv` it to `leader/archive/`.

## Teammates (not my workers)

- **Calvin** (`origin/Calvin`): ShadowNav particle filter `sim/nav.py`
  `ParticleNav` and the planner slope fix `sim/rover.py`, both in
  `integration` (abb65cc). His answers are in `docs/requests/navigation.md`
  on his branch. Constants checked against the Cauligi PDF: Ns 100, Neff
  50 (sec. 6B); 2% drift, 3 m 3-sigma (sec. 1). **Now building vision** (step
  6: rims from first-person frames). The answer key for him is
  `sim/rim_truth.py` plus `docs/requests/rim-answer-key.md` on
  `step-6-rim-truth`. His branch also edits `web/build_assets.py`
  (LANTERN_DATA) and `run_demo.py`. `run_demo.py` has many editors; scene
  owns it.
- **Kellen** (`origin/step-1-chain`): contract deployed and verified on Monad
  testnet `0x67f9Bedb42C845307cB1Fd6551D07Df64bd3CE98`, client
  `sim/chain.py` with `run_scope()`, a patch for `run_demo.py`. No keys
  committed (checked). My review is `docs/requests/chain-review.md` on
  `joseph` (736a3c1). Not merging: chain is off.
- **Ritvik** (`origin/ritvik`): an operations layer (battery, comms,
  science stops), a second sim `sim/mission.py`. Parked.

## State of the product (12:31)

- Site shows run 1 NEW: ParticleNav + Calvin's planner. A err 0.89 m, sigma
  0.84 m; B err 0.21 m, sigma 0.75 m; confirmed; tracks off No-go. Run 2 is
  still the old log until the rerun lands.
- Measured: the old planner drove 74-145 m into no-go ground; Calvin's
  planner drives 0 m (A) and 2 m (B, start inside a crater; fixed in the
  next rerun).
- Realism accepted by terrain: scene 61af034 (4 poses, within 3 levels);
  viewer rim arc abc04d3; near-pool brightness settled with no change.
  Viewer: Hapke 3D, near backdrop, rocks, 1x/10x, synced camera panel,
  No-go layer from SlopeMap (65c3dab), sharp map at zoom (f0b94e2), 200x
  patch prefetch (8b61bbf).
- Rendering cost: about 0.7 s per first-person frame, CPU numpy. RAM is the
  limit (15.7 GB total, about 4.5 GB free); about 0.61 GB per pool worker.

## Verified facts (sources checked)

- ShadowNav: Atha 2024 sec. IV-V: camera 2.5 m, lamp 0.2 m below, 60 deg
  HFOV. Cauligi 2023 Table 1: craters 2.3-11.9 m, depth 0.25-1.0 m. Table 2:
  final error 1.68-3.84 m. Rim = "a shadow at the leading edge of a negative
  obstacle" (sec. 3A).
- Hapke: Xu et al. 2020 Table 1 p. 3 old-highland w 0.48, b 0.232, Bs0
  1.5867, hs 0.0626. Eqs. 5-12 pp. 6-7 match `hapke_radf`. The PDF is in
  `docs/sources/` on `step-2-terrain-realism`.
- Terrain: Shoemaker LOLA window, 1 km at 0.25 m, georef 0.17 m RMS. 20.6%
  of the site is no-go (over 15 deg across 2 m, plus half width).
- Rover speed 7.4 cm/s (PROSPECT brief p. 3).

## Log (append below)

- 12:31 Rewritten. Next: re-arm watcher; review run 2 when the integrator
  reports; get the designer's address from its first mail; merge
  `integration` into `joseph` after the second judge pass.
- 12:36 viewer report accepted: fp_sub playback 8c7096c, particles map+3D
  154b96d, planner HUD row, lamp 15/32 and exposure 0.55 match scene.py:76-81
  at 2c8af7e (checked). Run 2 A on current sim clears the deep crater (9.3 m,
  0 no-go steps); unconfirmed until the official rerun lands. Its shots show
  "TEST run*-ptest" entries from its scratchpad; those must not reach
  integration. Viewer mailed designer the dots legend and #opt-particles hooks.
- 12:37 terrain fyi: answer key follows scene's 15/32 cone (7763856, merged
  up to integration f13b9ef). Lips give 29-37% of rim shadow; recommends
  detail frames for vision still. Handoffs numbers updated.
- 12:41 Branch check. Calvin: nothing since abb65cc (vision not pushed).
  Ritvik efbb4b4 (12:35): second contract LunarDiscoveryRegistry (registry/,
  20 forge tests), Surveyor/Prospector roles, sim/mission.py uses ParticleNav,
  separate web/mission.html "Mission Control". No keys; touches no shared
  file (app.js, index.html, rover, nav, scene, run_demo, app.py). Parked:
  chain is off, and it is a second contract and a second dashboard.
- 12:46 designer report (535a317 on step-5-design, with viewer 1360ed3,
  dff8895 nogo.png build fix): pitch line, legend folded into toggles, HUD
  table "thinks it is within / actually off by", run status dots, phone
  layout. Shots data/previews/design/{before,after}. Looked: clear gain.
  Sent back: No-go must default ON (Joseph liked the no-go map), quieter
  tint instead of off. Open: phone scroll unproven on a real phone; faint
  dashed rectangle near A's find (viewer to trace). Run 1's banner still
  lists "route planner" as stand-in: old 1203 log, fixed at next rerun.
- 12:49 Joseph: map header jitters during playback. Reproduced in browser:
  camera panel 311/294 px because #fp-cap (main.js:281) adds the rim-count
  sentence only on full frames with rims. Task to viewer (124835): per-step
  text must never move layout; check by sampling header top each frame.
- 12:50 Run 1203 was stale (stopped partway). Scene started the fast rerun
  run1/run2-20261115-1250 on 21cca99 (ParticleNav, Calvin's planner, B start
  fix, 15/32 lamp, particles, labels), ETA 13:10. 1108 rerun (old code)
  must not be published. Joseph is frustrated that run 2 still shows the
  old 11:08 log; told him vision means a rerun, not a rebuild.
- 12:51 Joseph: "its just taking froever". Started worker `perf` by start_session (wrong: should be inbox). 12:52 stopped it on Joseph's word, nothing left behind; task now in ops/mailboxes/perf/inbox/20261115-125200_leader_handoff.md for a session Joseph opens. Was
  (local_a6c203cf-f014-4fa6-913e-5f7101e31550), brief docs/briefs/performance.md
  (7fb785d): faster rerun and asset build, identical output, measure first,
  own worktree C:\Monad-Hackathon-perf on step-6-perf, coordinates with scene,
  no heavy benchmarks before 13:10. Doorbell address comes in its first mail.
  Scene lets the 1108 rerun finish (12:55) so run 2 stays consistent until
  1250 lands about 13:10; run_demo.py writes index.json itself.
- Rule (Joseph 12:52): new workers go through the inbox system. Write the
  brief and a handoff mail in ops/mailboxes/<handle>/inbox/; Joseph opens the
  session. Never start_session for a worker.
- Joseph 12:54: the global 4-Opus worker cap does not apply to this project.
  The inbox rule above still does.
- 12:55 perf started from the inbox (Joseph opened it). Stopped session
  local_a6c203cf is the dead duplicate; told perf.
- 12:56 integrator report: both ParticleNav runs on the site (623fbd1). Run 2
  A err 22.38 m, sigma 15.39 m, find disputed. Judge problem: HUD reads
  "thinks it is within ±15.4 m / actually off by 22.4 m". Sigma decision now
  urgent (Joseph). Joseph wants a big merge soon: Ritvik, Kellen, Calvin into
  joseph, then main, as a checkpoint foundation for the final 2 hours. He
  interrupted my dry merge-tree check; waiting on his word.
- 12:57 Joseph: chain BACK IN SCOPE. Keep Kellen's host (lantern-teal.vercel.app).
  Merge plan (proposed, Mission Control placement still open): integrator
  merges Calvin, step-1-chain, step-5-chain-feed, step-5-hosting,
  step-7-results, ritvik and main onto `checkpoint` from integration after
  1250 lands; conflicts only run_demo.py (keep integration's), main.js
  banner (hosting, by hand), WORKING.md. Then joseph and main on his word.
  Skip data-shoemaker. Calvin is 45 commits behind integration (scene.py
  about 300 lines); request docs/requests/vision-sync.md for his agent.
- 13:02 viewer: jitter fixed (84e14bf, main.js only). Live boxes reserve
  their tallest text per run; clock padded. 0 boxes move over ~33k frames,
  both runs, desktop and phone (was 10/23). Dashed rectangle = fine-tile
  edge aliasing; fine tiles hide beyond 150 m (0a75b73). Next: No-go hatch.
  Joseph asked what Ritvik's Mission Control adds; I proposed taking verify
  button, science stop, route on camera into the main site after the
  checkpoint merge; waiting on his answer and on Mission Control placement.
- 13:04 JOSEPH DECISIONS (13:03): demo = Ritvik's Surveyor/Prospector roles
  and 8-site mission, keeping all our improvements (renderer, ParticleNav,
  planner, viewer, design); NASA rover models in; Kellen's Lantern contract
  is the chain (not Ritvik's registry); Kellen's host kept. Checkpoint merge
  task sent to integrator (130335): all branches onto `checkpoint` from
  integration; joseph/main only on Joseph's word. Next: unification brief
  (one sim emitting our log format with roles and 8 sites; viewer plays it
  with NASA models, verify, science stops). Open: dispute story in the
  mission; sigma formula (now posts on a real chain).
- 13:06 Joseph OK: Monadscan in a second window (it sets X-Frame-Options
  SAMEORIGIN, so no embed). Live demo = laptop posts with keys, site's chain
  feed (Kellen's chain.js) shows each tx, links open Monadscan. To-do list
  docs/TODO.md (e60d4fc).
- 13:06 Joseph: "give the merge to a new agent. it needs to clean everything
  up". Withdrew 130335 from integrator (it had read it, no checkpoint branch
  pushed). Brief docs/briefs/merge.md; handoff
  ops/mailboxes/merger/inbox/20261115-130601_leader_handoff.md. Joseph
  opens the `merger` session. Integrator keeps integrating into integration.
- 13:08 Integrator had already merged locally: worktree
  C:\Monad-Hackathon-checkpoint, branch checkpoint e4b2e83 (unpushed;
  main, ritvik, step-1-chain, chain-feed, step-7-results, hosting, Calvin on
  integration b21837c; designer held; forge not installed). Told merger to
  start from it (fyi 130752). Joseph opened merger; address comes in its
  first mail.
- 13:09 merger live (doorbell `Inbox triage and monitoring [37f8c1]`),
  keeping the integrator's local merges, auditing conflict picks. Calvin
  followed vision-sync: merged integration (e6b7428, 1ca566e) and pushed WIP
  sim/vision.py (camera rims for the filter), lidar crater hazards, planned
  path on camera, and a lidar panel touching web/js, app.css, index.html
  (overlaps viewer/designer; merger takes it in).
