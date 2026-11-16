# Who is working on what

**Last updated: 2026-11-15 13:30 PST.** The base is branch `checkpoint`: every
branch below merged onto `integration`, with the clean-up in `README.md`.
Switch to it, then push your branch and tell the integrator, who merges it
into `integration`. `main` takes the checkpoint on Joseph's word. What is
left for the demo is in `docs/TODO.md`.

Before you start something, add a row here and push. When you finish, change
its status. Formats passed between parts are in `docs/handoffs.md`: read it
before writing a function another part calls.

## In progress

| Work | Owner | Branch | Files it writes | Started | Status |
|---|---|---|---|---|---|
| Terrain from the LOLA Shoemaker tile, 1 km at 0.25 m, with small craters and the crater list (step 2) | Joseph, agent `terrain` | `step-2-terrain` | `sim/terrain.py`, `data/terrain/` | 10:30 | done 10:45, merged into `joseph` |
| Realistic display of the terrain: surface detail, rocks, reflectance, 20 km backdrop (display only; the sim keeps the 0.25 m grid) | Joseph, agent `terrain` | `step-2-terrain-display` | `sim/terrain_*.py`, `data/terrain/` | 10:46 | done 11:00, merged into `joseph` |
| 3D scene: first-person lamp view with depth, third-person chase view (step 6 render) | Joseph, agent `scene` | `step-6-scene` | `sim/scene.py` | 10:30 | done 10:49, merged into `joseph` |
| Real-crater terrain from a 1 m south-pole DTM (Hemmi et al. 2025, CR1 connecting ridge), LOLA build stays default until reviewed (step 2) | Joseph, agent `terrain` | `step-2-terrain-real` | `sim/terrain*.py`, `data/terrain/` | 11:13 | dropped 11:25: too few real craters for the demo; not merged |
| Web viewer: the hosted demo page, replaying runs with live 3D third person, first-person frames, map, finds and chain feed (step 5) | Joseph, agent `viewer` | `step-5-viewer` | `web/` | 10:55 | merged 11:27; hosting next |
| Demo spine: `run_demo.py` writing both demo runs end to end, hidden ice map and sensor, stand-ins for nav, planner and chain (steps 4 and 5 glue) | Joseph, agent `scene` | `step-4-spine` | `run_demo.py`, `sim/rover.py`, `sim/ice.py`, `sim/standin_*.py` | 10:58 | done 11:21, merged into `joseph` |
| Integration: one launchable app wiring terrain, display layer, scene, spine, chain and viewer together | Joseph, agent `integrator` | `integration` | `app.py` and glue; asks owners for changes in their files | 11:22 | running: `python app.py` serves the site; judge pass 1 done; hosting off for now |
| Realism, first person: display layer, Hapke reflectance and exposure in the rover camera | Joseph, agent `scene` | `step-6-scene` | `sim/scene.py` | 11:28 | building, about 12:40 |
| Realism, third person: Hapke shading, rocks and the near backdrop in the site's 3D view | Joseph, agent `viewer` | `step-5-viewer` | `web/` | 11:36 | starting |
| Realism check: Hapke values against Xu 2020, reference stills, comparisons with ShadowNav images | Joseph, agent `terrain` | `step-2-terrain-realism` | `sim/terrain*.py`, `docs/sources/` | 11:36 | starting |
| Navigation: particle filter `sim/nav.py` and planner fix `sim/rover.py` | Calvin | `Calvin` -> `integration` | `sim/nav.py`, `sim/rover.py` | 11:55 | in `integration`, run 1 live on the site |
| Vision: crater rims from first-person frames (step 6) | Calvin | `Calvin` | `sim/vision.py` | 12:20 | in `checkpoint` (`2e28d61`), opt-in: `run_demo.py --vision` adds camera rims and lidar hazards; default runs use the stand-in rims |
| Rim answer key for scoring vision | Joseph, agent `terrain` | `step-6-rim-truth` | `sim/rim_truth.py` | 12:18 | done 12:25 |
| Site look: clean, uncluttered layout | Joseph, agent `designer` | `step-5-design` | `web/app.css`, `web/index.html` layout | 12:29 | in `checkpoint` (`861bb11`) |
| Contract on Monad testnet with Foundry tests, and the Python client `sim/chain.py` (step 1) | Kellen, agent `chain` | `step-1-chain` | `contracts/`, `sim/chain.py`, `sim/lantern_abi.json`, `sim/lantern_deployment.json` | 10:50 | done 11:25, deployed on Monad testnet (address in `sim/lantern_deployment.json`), demo run on chain; ready to merge |
| Live chain feed for the viewer: read-only browser reader of the contract, standalone page, wiring note for the viewer owner (brief `docs/briefs/chain-feed.md`) | Kellen, agent `chain-feed` | `step-5-chain-feed` | `web/js/chain.js`, `web/chain.html`, `web/collect_chain.py`, `web/chain/` | 12:00 | done 12:20, pushed; `web/chain.html` reads the live contract; wiring note for the viewer owner at the top of `chain.js` |
| Results ledger: master results file per run (paths, find locations, ice quantity, volatiles) and posting it to Monad, `LanternSurvey` contract next to Lantern | Kellen, agent `results` | `step-7-results` | `sim/results.py`, `sim/survey.py`, `sim/survey_abi.json`, `sim/survey_deployment.json`, `survey/` | 11:05 | done on anvil 11:25; testnet deploy next (Lantern is live) |
| Hosting: Vercel config, site check, one-command publish (CLI upload, or the `deploy` branch for the GitHub integration and GitHub Pages), portable data path | Kellen, hosting session | `step-5-hosting` | `web/vercel.json`, `web/.vercelignore`, `web/check_site.py`, `web/publish.py`, `docs/hosting.md`; one-line data-path fixes in `sim/terrain.py`, `run_demo.py`, `web/build_assets.py`, `web/collect_runs.py`, `web/make_standin_run.py` | 12:10 | LIVE 12:20 at https://lantern-teal.vercel.app (stand-in runs from Kellen's Mac, no camera frames yet); final publish is `python web/publish.py vercel` on the machine with the final runs, see docs/hosting.md. 12:30: `chain.html`, the live contract record (from `step-5-chain-feed`, new files only), on the site. 12:39: real-chain runs with camera frames live (run1-real confirmed, run2-real disputed; frames untextured until Joseph re-renders). 12:45: one-line viewer fix in `web/js/main.js` (the stand-in banner said "nothing was posted to Monad; hashes are fake" on real-chain runs; now follows `run.fakeTx`), Joseph please take or redo. 12:49: textured re-render live (run1-real2 confirmed find 17, run2-real2 disputed find 18; display layer on, ParticleNav, live contract). 12:50: paused for Joseph's merge; merge notes at the top of docs/hosting.md |
| Surveyor and Prospector 8-site mission, discovery registry, Mission Control with NASA rover models | Ritvik | `ritvik` | `sim/roles.py`, `sim/mission*.py`, `registry/`, `web/mission.*`, `web/js/mission.js`, `web/js/science_rig.js`, `web/models/` | 11:32 | in `checkpoint`; notes in `docs/CHANGES-ritvik.md` |
| Checkpoint merge and clean-up | Joseph, agent `merger` | `checkpoint` | merges; `README.md`, root layout, `.gitignore`, `.env.example`, `docs/WORKING.md`, `docs/handoffs.md` | 13:06 | pushed; report to the lead |
| Demo mission: `run_mission.py` (Surveyor surveys S1 dry and S2 ice, posts the find; Prospector reads the chain, drives, checks; one unmapped crater per leg for the lidar divert; checkpoints in the log), replay pauses at checkpoints and posts the two chain calls live on each play-through via a Vercel function | Kellen, demo session | `demo-mission` | `run_mission.py`, `web/js/mission_player.js`, `web/js/live_chain.js`, `web/api/chain.js`, `web/package.json`, five hooks in `web/js/main.js`, `docs/handoffs.md` Mission log, `docs/hosting.md` uplink | 13:30 | 14:05: final run `mission-demo` done (real chain, find 20 confirmed, display frames), all 8 checkpoints verified in headless Chrome; preview https://lantern-iezotzqs7-kellensniders-projects.vercel.app; 15:25: PRODUCTION https://lantern-teal.vercel.app is Joseph's `joseph` branch at ca0337e (Mission Control UI) with the mission rerun by his run_mission.py (find 21 confirmed, 1431 frames incl. lidar_fp_frame); live chain calls still wait on the Vercel env keys (docs/hosting.md) . 16:36: production republished from joseph-deploy (= joseph + chain-live-fix): red no-go overlay back (nogo.png had never reached the host), lidar camera frames back (prune now keeps lidar_fp_frame), key overlay hidden on Kellen's call, fresh run with Joseph's done-A pause; uplink keyed, signers on |
| Briefs, review, merging into `joseph` | Joseph, lead session | `joseph` | `docs/`, `ops/PROTOCOL.md` | 10:00 | ongoing |

## Not claimed yet

The open items are the to-do table in `docs/TODO.md`. The first
spec's part numbers are in `docs/moon-ledger-spec.md`.
