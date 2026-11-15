# Leader state (lead session, handle `leader`)

Written 2026-11-15 11:20 PST, before autocompact. Read this first after a
compaction, then `ops/PROTOCOL.md`, `docs/WORKING.md`, `docs/handoffs.md`.

## How Joseph wants to work

- No choice buttons or AskUserQuestion. Ask questions in plain text at the
  end of a reply. Wrap-up with a recap only.
- Never save memories without asking (he was angry when I did). Do small
  reversible things he points at without asking; ask only real decisions.
  Do not go rogue.
- Keep explanations simple; he got lost when I gave option lists. Answer the
  question asked.
- Workers have their own agency, not box-tickers. Handoffs quote Joseph
  verbatim; method is a suggestion.
- UI: simple, elegant, like MEWracle-Developer
  (`C:\MEWron 0.2\MEWweaver (Experimental Physics Build)\v3 tests`) but not
  1:1, "not obviously AI". UI stays open until near submission.
- He checks with teammates before new work. He is NOT doing the contract.
- Commits: never name a person. `main` takes merges only when he says. His
  branch is `joseph`; I merge reviewed worker branches into it.

## Decisions

- Build our spec (Lantern, `docs/spec.html`), not Calvin's `4 specs.md`
  (Moon Ledger); Calvin's four-part split is good.
- Demo link required (local-only is disqualified): build locally, port at
  the end to a static web viewer replaying run logs.
- Judging: investability, creativity, completeness ("clean ui/ux and working
  features, end to end, no gaps"), presentation.
- Display realism is made up and labelled; the sim runs on the 0.25 m grid
  ("low poly renders kept on the backend").
- Checkpoint tag `checkpoint-1105` = `e6cd771` on `joseph`, stable.

## Mailboxes and workers

Mailboxes: `C:\Monad-Hackathon-Project\ops\mailboxes\<handle>\inbox|archive`
(git-ignored). My watcher (re-arm after each mail, run_in_background):

    timeout 43200 bash -c 'd="C:/Monad-Hackathon-Project/ops/mailboxes/leader/inbox"; seen=$(ls "$d"); while true; do new=$(comm -13 <(echo "$seen") <(ls "$d") | grep -v "^$"); if [ -n "$new" ]; then echo "mail: $new"; exit 0; fi; sleep 5; done'

Doorbells (SendMessage `to` = these addresses; all sessions share the name
"Monad Hackathon protocol setup", so use the address):

| handle | address | worktree | current job |
|---|---|---|---|
| terrain | `uds:\\.\pipe\LOCAL\cc-msg-3956fbcec9b14cf96512bbeaf1a4ac16` | `C:\Monad-Hackathon-terrain` | real 1 m DTM rebuild (Hemmi 2025 CR1), branch `step-2-terrain-real`, timeboxed 1 h from 11:13; write `data/terrain/cr1.npz`, shoemaker stays default |
| scene | `uds:\\.\pipe\LOCAL\cc-msg-003a54f5030ab1d0041668abdbf2f6dc` | `C:\Monad-Hackathon-scene` / `-spine` | demo spine `run_demo.py`, branch `step-4-spine`, brief `docs/briefs/spine.md` |
| viewer | unknown yet (first mail will show it) | `C:\Monad-Hackathon-viewer` | web viewer, branch `step-5-viewer`, brief `docs/briefs/viewer.md` |

## Merged into `joseph` and reviewed

- Terrain (`sim/terrain.py`): `data/terrain/shoemaker.npz`, 1 km at 0.25 m,
  LOLA window x 73250-74250, y 36000-37000, 11,082 synthetic craters
  2.3-20 m. Georef checked by me: 0.17 m RMS vs raw tile, 45 m flipped.
- Scene (`sim/scene.py`): first person 0.29 s, chase 0.17 s per frame;
  depth = range along ray; lamp shadows by horizon map.
- Display layer (`sim/terrain_display.py`, `sim/terrain_look.py`):
  `data/terrain/display/`, format in handoffs. terrain_look.py is a slow
  still-image renderer (15 s/frame, rebuilds every frame); not for video.

## Verified facts (sources checked this session)

- ShadowNav 2024 (Atha), sec IV-V: camera 2.5 m, lamp 0.2 m below, 60° HFOV;
  LROC DEMs 2-5 m scaled to 0.25 m with two-scale fBm texture; crater test
  used 4 real craters 5-20 m "from the orbital data"; crater map hand-labelled,
  2-3 landmarks per 536-1211 m trajectory. 2023 (Cauligi) Table 1: craters
  2.3-11.9 m, depth 0.25-1.0 m. No ShadowNav code released.
- Hemmi 2025 data (`data/hemmi2025/`, CC BY 4.0, Zenodo 17153447): six 1 m
  SfS DTMs; CR1 2.0 x 1.7 km, 90% valid; vs LOLA Site01: 0.33 m offset,
  1.28 m RMS, no shift better, 75 m flipped. Holes are mostly shadowed crater
  interiors. Sunlit ridge, not a PSR floor.
- LOLA "Shoemaker"/"Haworth" tiles are landing-site high ground, not crater
  floors. Repo is private on GitHub.

## Open items

- Display layer into `sim/scene.py` (keeps 0.29 s/frame); spine frames lack
  realism until then. Rim lips: run vision on plain geometry.
- Pitch (investability, slides, script) is unowned.
- Ice numbers must come from the PROSPECT brief with page, or be assumed.
- New teammate branches appeared: `origin/ritvik`, `origin/step-1-chain`.
  Look before assuming who owns what.
- `preview.py` (repo root): `python preview.py` renders a 10 s clip of what
  is merged to `data/previews/latest.mp4` in about a minute and opens it.
- Background task bf7q1xpd8 renders a 30 m display-layer drive to
  `data/previews/display_drive.mp4` (10 fps, 6 s); re-encode at 6 fps for a
  10 s clip and send it to Joseph.

## Update 11:21

- `lantern_data/` pushed on `joseph` (198c1eb): shoemaker.npz, synthetic.npz,
  display/; friend sets LANTERN_DATA to it.
- Terrain worker hit its timebox on CR1 (report archived 11:21): cr1.npz
  works, 53 real craters, 12% filled, but only 8% of points have a mapped
  crater within 15 m (shoemaker 100%), and fill seams draw false edges.
  Options: (a) keep shoemaker for demo, CR1 as real-data validation;
  (b) biharmonic fill (20 min) and sparse-landmark nav; (c) hybrid.
  DECIDED 11:25: real craters tossed, shoemaker stays ("lets toss real craters and keep current terain"). Terrain worker told to hold.
- Teammate branch `origin/step-1-chain` has a contract, Foundry tests and a
  Python client (67cde98). `origin/ritvik` exists, unread.

## Update 11:22

- Integrator briefed (`docs/briefs/integrator.md`, branch `integration`, handle `integrator`, Opus). Owns the single launch app and glue; may merge worker branches into `integration` only. Waiting for Joseph to open its session.

- 11:30 Spine merged into joseph: run1 confirmed, run2 disputed (data/runs/run*-20261115-1108). Scene worker idle; told to expect mail from integrator.

- 11:27 Viewer merged into joseph (1bfd1cf). Screens checked: run1 confirmed, run2 disputed. Flaws: rover camera washed out (scene exposure); hidden-truth labels cover the find label in run 2. Next: hosting.

- 11:29 Integrator interim report: app.py on integration serves the viewer, both runs play. Told it to merge origin/joseph (standins fix is 6baa1ef). Viewer told: label fix, agree hosting with integrator. Addresses: integrator uds:\.\pipe\LOCAL\cc-msg-e4aa78077ba4f6511766cd4cc20730b8, viewer uds:\.\pipe\LOCAL\cc-msg-a233f82a3c78cbd382c1279676ac4ac8. The textured clip has no rover: terrain_look.py draws terrain only; scene.py has the rover mesh.

- 11:31 Integrator: joseph merged into integration (3eecb01), banner and FAKE TX on screen, --site export 46 MB with no keys. Scene texturing frames, ETA about 12:30. Host choice waits on Joseph (repo private, so not GitHub Pages free).

- 11:33 Viewer label fix fa35ebc merged into joseph. Viewer proposed integrator runs the deploy.

- 11:36 Realism push before Calvin nav import (Joseph: "as realistic as possible ... first and third person lighting and terrain"). scene: display layer + Hapke + exposure in sim/scene.py (approved, ~12:40). viewer: Hapke shader, rocks, near backdrop in web 3D. terrain: verify Hapke vs Xu 2020, reference stills, judge both (branch step-2-terrain-realism). Keep Scene interface stable for nav.

- 11:38 Standing rule (Joseph): localhost site from app.py is the base; everything integrates into it; done = visible there. Broadcast to all workers; on the board. Calvin is building nav.

- 11:52 Viewer realism (8f42c21) merged into joseph: Hapke 3D view, near backdrop, rocks, 1x/10x. Checked v1_vs_refs.png: matches terrain refs. App needs asset rebuild (integrator told).

- 11:55 Joseph: 3rd person looks different from 1st, 1st has low fps. Cause: fp panel is a slideshow of scene.py jpgs (one per 2 m step), older shading. Viewer job: live first person in three.js, sensor frames behind a labelled toggle. Terrain checks rim shadows.

- 12:00 Scene 2c0fbba merged into joseph (display layer + Hapke in fp, 0.49 s/frame). Rim shadows fainter than refs; scene told to strengthen (finer mesh approved), fix ripple, wait for terrain verdict.

- 12:02 Integrator: integration 31261eb has viewer 8f42c21 + scene 2c0fbba, assets rebuilt, both runs play. Bug: 3D canvas overflows camera panel at 800 px (mailed to viewer). Waiting on host choice.

- 12:05 RESET (Joseph unhappy: too fake, piecewise). Cancelled live browser first person. Priority now: replace fakes, in order: chain (Kellen, merge), vision (rims from scene frames, unowned, propose terrain), particle filter (Calvin or us), then looks. integration = the single merge point; leader merges integration into joseph. Waiting on Joseph: host choice, go-ahead on chain merge.

- 12:07 Terrain: Hapke values verified against Xu 2020 Table 1 p.3, Eqs 5-12 pp.6-7 (PDF in docs/sources, commits 6e170ef, 59c7161 on step-2-terrain-realism). Scene/viewer told: rim arcs faint/missing. Terrain free -> vision candidate pending Joseph go.

- 12:10 SCOPE (Joseph, angry): terrain + scene rendering ONLY. Chain, hosting, nav, vision OFF. Scene: rim arcs, brightness, rocks, fp frame every 0.5 m. Viewer: rim arc, pool brightness, overflow. Terrain: judge. Integrator: merge rendering fixes only.

- 12:16 Joseph: separate renderers OK if 3rd person has same timing as 1st and same terrain. Viewer asked to sync frame/pose and confirm terrain source.

- 12:18 Brightness dispute: terrain computed from shared constants; viewer near pool too bright at 262B (157 vs ~45 expected, ground 48.6 deg off lamp axis, outside 45 deg cone). Viewer to probe its lamp axis/cone. Terrain render tracks computed values within ~20 levels.

- 12:21 Viewer: near-pool settled (camera aim mismatch, no lighting change; both match computed). Panel synced, worst gap 1.0 m (0.25 m once 0.5 m frames land), 70b4f72. Terrain source confirmed same npz/x0/y0/cell. Rim arc: adding fine patch with lips ahead of rovers.

- 12:25 Calvin pushed sim/nav.py ParticleNav (43abedd) + answers. Verified constants vs Cauligi PDF (Ns 100, Neff 50 sec 6B; 2%, 3 m 3-sigma sec 1). Integrator assigned: checkout nav.py + rover.py from origin/Calvin (no branch merge), 3 run_demo edits, rerun, show on site. Scene cc. Note sigma reads 15 m vs 22 m true error, fixes off (Q7), handoff decision pending.

- 12:28 Joseph likes Calvin slope-exclusion map (crater_fix_before_after.png; red = >15 deg across 2 m + half width). Viewer: add No-go layer to site map, built from SlopeMap in build_assets, after rim-arc fix, after integrator lands rover.py.

- 12:07 CORRECTION: state lines stamped 12:10-12:28 above were guessed; real clock was ~11:58-12:06. Times from date only. Chain review pushed: docs/requests/chain-review.md (736a3c1).

- 12:08 GO combined rerun: scene from step-6-scene 5ef8a24 (61af034 accepted by terrain, 0.5 m frames, ParticleNav via integration abb65cc). Integrator merges + reports from app.py. Viewer: 200x patch gap, then No-go layer; terrain flagged hard shadow streaks (2 cm penumbra).

- 12:11 Joseph: why so slow. Rerun ~1.2 frames/s serial, ~45 min both runs (run1 ~12:25, run2 ~13:00). Scene next: parallel render from log, 8 workers, target <10 min. Integrator: show run 1 early.

- 12:13 CORRECTION (Joseph caught it): fp frames at 2 m nav steps must stay in the sim loop (vision will read them). Only 0.5 m in-between display frames go parallel afterwards, labelled as display. Scene told.

- 12:13 Viewer 8b61bbf: 200x patch gap closed (prefetch along track; 100% covered at 200x, 135 fps). Nav HUD line added. Now No-go layer. Sigma formula still Joseph decision (viewer shows unadjusted).

- 12:16 Viewer 65c3dab No-go layer accepted (matches Calvin fig). Asked: sharper map hillshade at zoom; then check run 2 track after rerun.

- 12:18 Viewer f0b94e2: zoomed map hillshade from 0.25 m tiles, sharp as Calvin fig. Holding for rerun. Pending Joseph: terrain answer-key job for vision; sigma formula.

- 12:18 Terrain: rim answer key for vision (truth per frame from crater map + true pose + depth; score(); full vs detail lips question), branch step-6-rim-truth, docs/requests/rim-answer-key.md for Calvin.

- 12:20 Joseph screenshot: track in red = OLD planner (app serves 11:43 logs). Measured: old A 74 m/B 85 m in no-go; Calvin planner run1-1203 A 0 m, B 2 m (start inside crater 700,250, 24.6 deg). Scene told to fix start for next rerun. Joseph reminder: workers have agency; brief goals not methods.

- 12:22 Scene status: parallel display frames byte-identical (45/45), sensor frames live in loop, pool sized to RAM (0.61 GB/worker), run1 B start moved to (700,256.75), all tracks 0 m >15 deg. Uncommitted; asked to push. Rerun: run1 ~12:22, run2 ~12:50, then rerun with new code.

- 12:23 Started: viewer = particle display (scene logs particles in run_demo); integrator = judge pass over app.py, mail owners. Scene 980e248 pushed (handoffs fp_sub/frames additions accepted).

- 12:24 Run 1 with ParticleNav + Calvin planner live on app.py (integration c9c7931): A err 0.89 sigma 0.84, B 0.21/0.75, confirmed, tracks off No-go, banner drops nav. Checked screenshot. Camera not smooth: viewer must read fp_sub (told: before particles). Run 2 ~12:50-13:00.

- 12:25 UI: Joseph says cluttered/ugly. New worker designer (handle designer, branch step-5-design, owns app.css + index.html layout; viewer keeps web/js). Brief docs/briefs/design.md. Waiting for Joseph to open session.

- 12:25 Terrain answer key 644cf16 (sim/rim_truth.py, docs/requests/rim-answer-key.md on step-6-rim-truth): ~1/3 of rim shadow from invented lips; recommends vision read display=detail (Joseph decision). handoffs.md updated. Scene logged particles bc9f6ea; viewer told format. Designer handoff rewritten (heredoc broke it).

- 12:26 Integrator judge pass 1: 7 items. Decided planner -> top-level key (handoffs updated). UI items 1,4,5,6,7 -> designer; 2 viewer; 3 scene.

- 12:29 Designer session open (address unknown until it mails). Sent it judge items 1,4,5,6,7.
