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
