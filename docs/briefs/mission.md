# Worker `mission`: Mission Control plays campaign runs

Branch `campaign-mission`, worktree `C:\Monad-Hackathon-mission`.
Read `docs/briefs/live-common.md` first.

**Goal.** The hackathon's Mission Control is the main page again, and any
campaign run plays inside it. Read spec section 1, "What a viewer sees".

**Restore.** `campaign-viewer` moved Mission Control to `_archive/` together
with the chain code. Bring back everything except the chain: `web/js/main.js`,
`map.js`, `mission.js`, `mission_player.js`, `science_rig.js`,
`web/app.css`, `web/mission.css`, `web/mission.html`, the rover models,
`collect_runs.py`, `make_standin_run.py`, `check_site.py`, and the hackathon
`web/index.html` (from `main`). Keep archived: `contracts/`, `registry/`,
`survey/`, `sim/chain.py`, ABIs and deployments, `registry_client.py`,
`survey.py`, `results.py`, `web/api/chain.js`, `web/chain*`,
`web/js/chain.js`, `web/js/live_chain.js`, `collect_chain.py`, and the
hosting files (`vercel.json`, `.vercelignore`, `publish.py`,
`package.json`; hosting stays local). Remove every chain, Monad, agency and
hackathon word a viewer can see. Reuse the campaign runs list, run map and
replay that `campaign-viewer` built where they help.

**What it does.**

- The root page is Mission Control. It opens on a campaign run. A Runs
  button lists every run (setup and results) and loads the chosen one.
- A sortie timeline (Scout, number, result). Pick one and play, with the
  hackathon's speeds, step keys and controls.
- One card per Scout (all Scouts are identical): energy, task, true against
  estimated position.
- A Finds panel in place of On chain: find, second Scout's search,
  confirmed or not, drilled.
- The crater map grows per sortie, with added craters marked. Hidden truth
  sits behind a toggle.
- Every view, for every run: third-person 3D, satellite map full window,
  the first-person box, lidar top-down and first person, particle cloud.
  Frames are not saved, so the first-person box draws from the logged rim
  points, route and lidar on the 3D ground, not from an image. Label it
  plainly.

**Data.** Worker `live-nav` adds the per-step pipeline data to the run
record in `docs/handoffs.md`; pull `origin/campaign-live-nav` for it. Until
it lands, build on the existing camera run in
`C:\Monad-Hackathon-Project\data\campaigns\showcase\` and on stand-in
campaigns (`web/make_standin_campaign.py`), and switch over when the format
is pushed.

**Serving.** `web/serve.py` from your worktree on port 8001. Port 8000 is
the old viewer; leave it alone.

**Done when** you have opened the page in a browser, played a campaign run
start to finish in every view, and read back downscaled screenshots of
each. Put the screenshot paths in your report.
