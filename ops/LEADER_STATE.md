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
| designer | unknown until it mails | opened about 12:29 | own worktree, `step-5-design` | owns `web/app.css` and `index.html` layout. Judge items 1, 4, 5, 6, 7. Brief `docs/briefs/design.md` |

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
