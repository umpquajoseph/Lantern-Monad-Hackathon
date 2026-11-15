# Hosting the demo

**Live: https://lantern-teal.vercel.app** (Vercel project `lantern` in
Kellen's scope `kellensniders-projects`, Hobby plan; first published
2026-11-15 12:20 PST with stand-in runs and no camera frames). Every
`python web/publish.py vercel` replaces it in about a minute.

The judges' link is a hosted copy of `web/`. Nothing on the server runs: the
page is static files, with relative paths, and loads three.js from jsdelivr.
Two folders in `web/` are generated and git-ignored, so the hosted copy is
`web/` **as it is on disk**, not as it is in git:

| Folder | Made by | What it is |
|---|---|---|
| `web/assets/terrain/` | `python web/build_assets.py` | the terrain, 37 MB, same for every run |
| `web/runs/` | `python web/collect_runs.py` (or `app.py`) | `index.json`, one `log.json` and `fp/*.jpg` per run |

`web/publish.py` does the rest. Every command below first runs
`web/check_site.py`, which follows every file the page fetches and refuses to
deploy a site with a hole in it.

## 1. Build the site

On Joseph's Windows checkout, `python app.py --no-serve` (branch `integration`)
builds whatever is missing and copies the runs into `web/runs/`. By hand, on
any machine:

```
python web/build_assets.py        # terrain -> web/assets/terrain/
python run_demo.py                # both runs -> <data>/runs/  (about 5 min with frames)
python web/collect_runs.py        # <data>/runs -> web/runs/
python web/publish.py check       # complete? sizes? exit 0 means yes
```

`<data>` is `LANTERN_DATA` if set, else `C:\Monad-Hackathon-Project\data` when
that exists, else `data/` beside the repo (`sim/terrain.py`). On a Mac with
no built terrain: `export LANTERN_DATA="$PWD/lantern_data"` uses the committed
copy.

## 2. Put it on Vercel

### Route A, upload from the machine that built it (recommended)

One-time, on that machine: Node 20 or newer, then `npx vercel login` (opens a
browser; any GitHub account works). After that, every time the site changes:

```
python web/publish.py vercel              # production: the demo link
python web/publish.py vercel --preview    # a throwaway URL to look at first
```

The first run creates a Vercel project named `lantern` and links `web/` to it
(`web/.vercel/`, git-ignored). The command prints the URL; paste it into
`docs/WORKING.md`. `web/vercel.json` tells Vercel there is no build, and
`web/.vercelignore` keeps the Python scripts out of the upload. The Vercel
CLI does not read `.gitignore`, so the generated folders go up.

On the first `vercel link` the CLI also tries to connect the GitHub
repository and prints "Failed to connect umpquajoseph/Monad-Hackathon-Project"
when the Vercel account cannot see the private repo. Harmless: that is only
the Git integration (Route B), and the upload goes ahead. It also writes
`web/.env.local` (a token, git-ignored, never uploaded).

**Publishing from Joseph's machine.** The project lives in Kellen's Hobby
scope, which has no collaborators, so either:

- Kellen makes a token at vercel.com/account/tokens and Joseph sets
  `VERCEL_TOKEN` in his shell, then runs `python web/publish.py vercel`
  (the script links to the existing `lantern` project by name); or
- Joseph runs `python web/publish.py branch`, Kellen checks out `deploy`
  somewhere and runs `python web/publish.py vercel --site <that folder>`.

### Route B, GitHub to Vercel (auto-deploy on push)

```
python web/publish.py branch
```

copies the site into an orphan branch `deploy` (site at the root, no source,
no history worth keeping) and pushes it. One-time, in the Vercel dashboard:

1. Add New, Project, import `umpquajoseph/Monad-Hackathon-Project`.
   Framework Preset **Other**, leave Build Command and Output Directory
   as they are, Deploy. (This first deploy is of `main`, which has no site;
   ignore it.)
2. Settings, Environments, Production, Branch Tracking: `deploy`. Save.
3. Settings, Git, Ignored Build Step, Custom command:
   `if [ "$VERCEL_GIT_COMMIT_REF" != "deploy" ]; then exit 0; else exit 1; fi`
   Otherwise every push to every step branch makes a useless preview
   deployment, and Hobby allows 100 deployments a day.
4. Deployments, Redeploy the latest `deploy` commit.

From then on `python web/publish.py branch` is the whole deploy.

### Route C, GitHub Pages, a second link for free

The same `deploy` branch serves on GitHub Pages: repository Settings, Pages,
Source "Deploy from a branch", branch `deploy`, folder `/ (root)`. The URL is
`https://umpquajoseph.github.io/Monad-Hackathon-Project/`. The page lives
under a sub-path there, which is why every path in `web/` stays relative
(`check_site.py` fails on an absolute one). Pages is slower to update
(a minute or two) and has a 1 GB site limit; fine as a backup link.

The repository is private today (its GitHub page answers 404 when logged
out). Pages from a private repository needs GitHub Pro or Team; on a free
account, make the repository public first (judges may want to read the code
anyway). Vercel's GitHub integration works with private repositories.

### No account at all

In an exported copy (`python web/publish.py export DIR`, then `cd DIR`),
`npx vercel deploy --temporary` deploys without logging in. It expires unless
claimed from the printed link. Last resort only.

## 3. After deploying

Open the URL in a fresh browser window and look: the terrain draws, both runs
are listed, play runs, the camera panel shows frames, a transaction hash
opens the explorer, `?run=<run_id>&t=600` opens paused at that time. Check
the console for 404s. Then try it at phone width.

## Limits and things to keep true

- **100 MB** total upload on Vercel Hobby, **15,000** files. Terrain is 37 MB;
  frames are the rest (about 15 KB each). `run_demo.py --frame-every 2` halves
  them. `check_site.py` errors over the limit and warns above 80 MB.
- **No `web/public/` folder**: Vercel's "Other" preset would serve that
  instead of the site.
- **Relative paths only** in `index.html`, the css and the js.
- **Internet at the venue**: three.js comes from jsdelivr, pinned to 0.170.0.
  If the venue network blocks CDNs, copy `three.module.js` and the addons
  into `web/vendor/` and point the import map at them.
- Rebuilt terrain or new runs are not live until published again.
- The site is public. The logs hold poses, finds, tx hashes and the contract
  address, nothing else; keys never leave `.env`.
