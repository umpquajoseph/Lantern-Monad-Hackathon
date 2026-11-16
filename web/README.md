# Lantern campaign site

The local site for the campaign simulator (`docs/campaign-spec.md`, section 6).
It reads only the run records and `summary.csv` (`docs/handoffs.md`,
"Campaign simulator").

- `index.html`: the runs list. Every run with its setup, world draws and
  results, sortable by any column and filtered by fleet size, check policy
  and map refinement.
- `run.html?batch=<batch_id>&run=<run_id>`: one run, top down. Sortie paths
  as each Scout believed it drove; the ice map as circles of each find's 95%
  position bound, coloured find / confirmed / unconfirmed / drilled; the
  crater map, with the craters the Scouts added; the dock. A slider steps
  through the sorties in the order they ended, so both maps grow. Each
  sortie has a time bar (driving, position checks, readings, drilling, dock
  queue). "Reveal hidden truth" adds the real ice, the craters missing from
  the Scouts' map, the true path and where each find's reading really was.
  `&k=<n>` opens it after n sorties; `&truth=1` with the truth shown.
- `replay.html?batch=...&run=...&sortie=<sortie_id>`: a time-lapse of one
  sortie's saved poses in the 3D world (`js/world.js`, unchanged). The
  sortie is turned into the old run-log shape in `js/replay.js`. Outside
  the 1 km drive area the ground is the real LOLA backdrop at 5 m and 20 m.

## Run it

```
python app.py          # builds what is missing, collects the runs, serves http://localhost:8000
```

Pieces, if you want them one at a time:

```
python web/build_assets.py           # terrain -> web/assets/terrain/ (~39 MB)
python web/collect_campaigns.py      # <DATA>/campaigns/<batch>/ -> web/campaigns/
python web/serve.py                  # http://localhost:8000
```

`web/collect_campaigns.py` copies each `record.json` as is and turns
`poses.npz` into `poses.bin` (float32, the record's own values) plus
`poses.json` (columns, and each sortie's first row and row count), because a
browser cannot open `.npz`.

## Stand-in records

Until the campaign engine writes batches, `python web/make_standin_campaign.py`
writes a batch called `standin` in the exact record format, plus a top-level
`standin` sentence. Every page shows that sentence in a yellow banner. The
Scouts in it are scripted; none of its numbers are results.

`web/assets/` and `web/campaigns/` are generated and git-ignored. Hosting is
off until the site is right; the old hackathon pages and publishing scripts
are in `_archive/web/`.
