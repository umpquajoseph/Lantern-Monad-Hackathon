# For the integrator: wiring the results ledger into app.py

From Kellen's `results` session, branch `step-7-results`. Nothing here edits
anyone else's file; these are the calls to add on your side.

## What it is

After each run, `sim/results.py` turns `data/runs/<run_id>/log.json` into
`data/runs/<run_id>/results.json`: each rover's believed path with sigma, every
find with lat/lon, reading, modelled ice area/depth/mass, 8 volatiles (LCROSS
ratios, Colaprete 2010 Table 2), checks, status, totals, and `results_hash`.
`sim/survey.py` posts paths, assays and the hash to `LanternSurvey`, which sits
next to Lantern and takes its writers from `Lantern.isAgency`. Format:
`docs/handoffs.md`, "Results ledger".

## The three hooks

1. After `run_demo.py` writes a run (stand-in chain or real):
   `python sim/results.py build data/runs/<run_id>`
   (pure Python, no network, about 0.1 s; reads `ice_reading` per step).
2. To put it on Monad, once `sim/survey_deployment.json` exists:
   `python sim/survey.py post data/runs/<run_id> [--post-finds]`
   `--post-finds` posts finds/checks that have no `tx` to Lantern first.
   Re-running sends nothing twice. It writes the tx hashes into
   `results.json` under `chain`.
3. Viewer: read `data/runs/<run_id>/results.json` (`finds[].ice.mass_kg`,
   `finds[].volatiles_ppm`, `finds[].lat/lon`, `totals`, `rovers[].path`,
   `chain.*.url`), or from chain with `SurveyClient().runs() / .traverses() /
   .assays()`. ABI: `sim/survey_abi.json`.

`survey/e2e_anvil.sh` runs all of it on a local anvil chain with no MON.
Checked on run_demo.py's own runs: run 1 confirmed, 7.6 t modelled H2O;
run 2 disputed, A max sigma 20.9 m.

## Honest labels for the UI

Ice mass and volatiles are modelled from the simulated reading, not measured:
label them "modelled". Lat/lon use a sphere of 1737.4 km (ASSUMED, check
against data/lola/README.txt).
