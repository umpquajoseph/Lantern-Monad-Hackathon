# Lantern web viewer

The page judges open from the demo link. It replays run logs
(`docs/handoffs.md`, "Run log") on one timeline: both rovers in 3D on the
real terrain, drawn live in the browser; each rover's recorded first-person
frame with its rim points; a north-up map with true, odometry and estimated
tracks and the ±σ circle; every find with its status; and the chain feed.

## Run it locally

```
python web/build_assets.py        # once: terrain -> web/assets/terrain/ (~37 MB)
python web/collect_runs.py        # copy run_demo.py output from data/runs/ into web/runs/
python web/serve.py               # http://localhost:8000
```

`web/assets/` and `web/runs/` are generated and git-ignored. Without real runs,
`python web/make_standin_run.py` makes two scripted ones (~10 min).

## Real runs

`run_demo.py` writes `data/runs/<run_id>/` (log.json and fp/) and a list in
`data/runs/index.json`. `web/collect_runs.py` copies them into `web/runs/`
and writes the page's index (run_demo.py's plain list also loads as is):

```json
{"runs": [{"run_id": "run1-...", "label": "Run 1: crater fixes on", "path": "runs/run1-.../log.json"}]}
```

Set `chain.explorer` in the log to the explorer's base URL; the feed links
each hash as `<explorer>/tx/<hash>`. A log with a `standin` key is shown as
stand-in, with its hashes marked fake and not linked. A `truth.ice` list
(`x`, `y`, `radius`) draws the real ice, which the rovers never see.

## Deploy

`web/` is a static site with relative paths only. three.js comes from
jsdelivr, pinned to 0.170.0. Upload the folder as it is on disk, with
`assets/` and `runs/` in it, to any static host (Netlify, Vercel,
GitHub Pages). Height tiles are gzip files named `.bin`; the page checks
the bytes, so it works whether or not the host also gzips them.

## URL

- `?run=<run_id>` opens a run; `&t=<seconds>` opens it paused at that time.
- `?quality=low` draws the drive area at 0.5 m and uses smaller shadow maps, for weak GPUs.
- Space plays and pauses; the arrow keys step.
