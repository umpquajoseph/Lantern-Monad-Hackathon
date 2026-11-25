# Lantern

A campaign simulator for the PROSPECT Scouts, for the team's RASC-AL 2027
paper.

One dock in a permanently shadowed crater sends out 1 to 5 identical
Scouts. Each sortie navigates by its live camera: it renders the lamp-lit
view, finds crater rims and updates a particle filter against the crater
map. Scouts survey, log ice finds with their position bound, and a second
Scout checks each find. Craters a Scout sees that the map lacks are added
to a shared map. Mission Control replays any run.

Scope: `docs/campaign-spec.md`. Formats: `docs/handoffs.md`. Constants,
each with its source or marked `assumed`: `sim/settings.py`.

## Run it

```
python -m sim.live_runs --sites haynes --scouts 1 --sorties 1 --batch-id <name> --serial --progress 500
python app.py --no-open --port 8070      # Mission Control at http://127.0.0.1:8070/
```

`--set sortie_h=4` cuts a test sortie to about 4 minutes. Run one sim at a
time on this machine.

Each run writes `record.json`, `poses.npz` and `steps.npz` to
`data/campaigns/<batch_id>/<run_id>/`.

## Layout

- `sim/`: the simulator. `campaign.py` runs a campaign, `campaign_camera.py`
  a camera sortie, `live_runs.py` launches batches. `calibrate.py` compares
  the camera navigation with ShadowNav. `drive_check.py` replays a stretch
  of driving without the camera.
- `web/`: Mission Control. `app.py` builds what is missing and serves it.
- `docs/`: spec, formats, terrain position, sources (`docs/sources/`, with
  text copies), findings for the paper.
- `bench/terrain/`: the measurements behind `docs/terrain/position.md`.
- `lantern_data/`: committed terrain, for running without building it.
- `ops/`: the handoff between leader sessions and the mailbox protocol.

## What is real

Real: LOLA elevation of the sites. Simulated: craters below orbital
resolution, the ice, and every instrument reading. Mission Control is for
looking; the paper's numbers come from the batch files.

Lantern began at Monad Blitz Oregon on 15 November 2026 as an on-chain map
of lunar ice. That build is in git history and at lantern-teal.vercel.app.
