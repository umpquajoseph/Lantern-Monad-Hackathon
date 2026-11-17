# Brief: the checkpoint merge and clean-up (handle `merger`)

Read `CLAUDE.md`, `ops/PROTOCOL.md`, `docs/TODO.md`, `docs/handoffs.md` and
this file first.

## What it is for

Joseph, 12:56: "we need to do a huge merge soon to merge ritvik, kellen,
and calvins into mine and put it on main so we can all switch to a
checkpoint foundation in the final 2 hours before the final merge". And at
13:06: "give the merge to a new agent. it needs to clean everything up".

The goal is one tree that every teammate and worker can switch to. It holds
all the work, keeps every improvement, and reads clearly. The demo built on
it is in `docs/TODO.md`: Ritvik's 8-site Surveyor/Prospector mission on our
renderer, navigation and site, posting to Kellen's Lantern contract.

## Joseph's decisions (13:03-13:06)
- The chain is back in scope. Kellen's Lantern contract is the demo's
  chain. Ritvik's registry contract is not used by the demo.
- Keep Kellen's host (https://lantern-teal.vercel.app).
- Ritvik's Surveyor/Prospector roles and 8-site mission are the story.
- Use the NASA rover models.
- "We should keep all our improvements."
- Monadscan opens in a second window. It cannot be embedded.

## What to merge (all `origin/`)
Start from `integration` (the localhost site's base). The leader checked
these with merge-tree at 12:58:

| branch | owner | text conflicts against integration |
|---|---|---|
| `Calvin` | Calvin | `run_demo.py`. Integration already has his three ParticleNav edits (abb65cc), so keep integration's. His `web/build_assets.py` LANTERN_DATA change merges clean. |
| `step-1-chain` | Kellen | none. Also answers `docs/requests/chain-review.md` |
| `step-5-chain-feed` | Kellen | none |
| `step-5-hosting` | Kellen | `run_demo.py` (RUNS_DIR from `sim.terrain.DATA`) and `web/js/main.js` (banner says the chain is real when it is). Take both on top of integration's code. |
| `step-7-results` | Kellen | `docs/WORKING.md`. Its contract files are an older copy of `step-1-chain`'s; keep `step-1-chain`'s |
| `ritvik` | Ritvik | none, new files only |
| `main` | | none |

Skip `data-shoemaker`, which holds built data the scripts regenerate.
Integration keeps moving (viewer, designer, scene and perf push into it), so
merge its latest head again just before you report.

Read `docs/CHANGES-chain.md` (on `step-1-chain`) and
`docs/CHANGES-ritvik.md` (on `ritvik`). They are each author's notes for
this merge.

## Clean up
After the merge, a judge or a new teammate should find their way around
quickly:
- One README that says what Lantern is, how to run the site, the sim, the
  chain and Mission Control, and where everything is.
- Stray root files go where they belong, for example `4 specs.md` and
  `request.md` into `docs/`. Fix any links that break.
- Duplicate or stale copies get resolved, not left in two places: contract
  files, ABIs, deployment JSONs, `.env.example` entries, `.gitignore`.
- `docs/WORKING.md` and `docs/handoffs.md` describe the merged tree.
- Do not delete anything whose purpose you cannot tell. List it in your
  report. Never delete a branch; Joseph decides that.

What else counts as clean-up is your call. Say what you changed and why.

## Rules
- Branch `checkpoint` from `origin/integration`, in your own worktree
  `C:\Monad-Hackathon-checkpoint`. Push often. Never merge into `joseph`
  or `main`. That happens on Joseph's word after the leader reviews.
- No keys anywhere. Grep the merged tree for 64-hex strings and
  `PRIVATE_KEY=` values before every push.
- Never broad-revert (`git checkout -- .`, `git reset --hard`). No commit
  message names a person. Mail `ts` comes from `date`.
- A real design choice you cannot settle from this brief goes to the
  leader, not into a guess.

## How it is checked
Mail the leader a report (`ops/PROTOCOL.md` format) with:
- each branch merged, with its conflicts and how you settled them;
- the clean-up list: moved, merged, deleted, and left alone with the reason;
- `python app.py` from a clean checkout of `checkpoint`: both runs play,
  with no console errors (read it back, not the exit code);
- `web/mission.html` loads, and `web/chain.html` reads the live contract;
- `forge test` in `contracts/` and `registry/`, if Foundry is installed;
  say so if it is not;
- `git diff origin/integration checkpoint -- sim/scene.py sim/nav.py
  sim/rover.py web/js/` shows no lost improvement;
- the key grep result.

## Talking to others
Your handle is `merger`. Your inbox is `ops/mailboxes/merger/inbox/`, in
`C:\Monad-Hackathon-Project`. Mailboxes are git-ignored, so write files
there directly. Give the leader your doorbell address in your first mail.

| handle | doorbell address |
|---|---|
| leader | `Hackathon project setup review [5247c4]` |
| integrator | `uds:\\.\pipe\LOCAL\cc-msg-e4aa78077ba4f6511766cd4cc20730b8` |
| scene | `uds:\\.\pipe\LOCAL\cc-msg-003a54f5030ab1d0041668abdbf2f6dc` |
| viewer | `uds:\\.\pipe\LOCAL\cc-msg-a233f82a3c78cbd382c1279676ac4ac8` |
| designer | `uds:\\.\pipe\LOCAL\cc-msg-42257a837952d8f3cb6dda87cb29e0fb` |
| perf | `uds:\\.\pipe\LOCAL\cc-msg-b5fa754137f62a9b8f31896194e2668b` |
| terrain | `uds:\\.\pipe\LOCAL\cc-msg-3956fbcec9b14cf96512bbeaf1a4ac16` |
