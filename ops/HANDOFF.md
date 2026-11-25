# Lantern handoff

25 Nov 2026. One leader's notes for the next: check each claim against the
code, the runs and the sources before relying on it. `docs/campaign-spec.md`
wins over this file.

## Where it stands

All work is merged on `lantern-workflow` (`C:\Monad-Hackathon-Project`).
`main` takes nothing without Joseph's word. No worker is active.

The spec's first "done when" is met on camera Scouts: `proof-2x2-fix`
(2 Scouts x 2 sorties, haynes seed 3, preplanned fixes) played end to end
in Mission Control. A find was logged, a second Scout searched it, and the
shared map gained 10 craters. All four sorties came home; each turned back
for time with 544-1,604 m surveyed. Findings so far are in
`docs/paper-findings.md`.

Missing: the paper batch (the spec's second "done when").

## Next steps

1. Agree the run count with Joseph.
2. The paper batch: preplanned and straight-through fixes side by side,
   several seeds, so the findings get a spread.
3. Figure 1 and the fleet and site tables. Due 15 Jan 2027. Site and video
   10 Feb, proposal 24 Feb.

Open, not reproduced: in the proof runs a Scout sometimes turns back and
forth 2-4 times near a crater rim as its lidar map fills in.
`drive_check` from the 2x2 run's S1-02 step 3490 did not reproduce it.
Fix a turn-around only when a replay shows it is a fault. Turning in place
by itself is not one.

## Decided

- Live camera navigation in every run.
- Terrain as `docs/terrain/position.md`: roughness 0.035 m rms (test cases
  0.021, 0.048); crater density 106-1,540 per km2 (336 central); map cut
  10 m (test 18 m); footprint slope test; ShadowNav camera texture; no
  rocks, stated.
- Filter: A24 near-half matching and 20 m gate; scale walk 0 (A24 eqs.
  3-6). No parity test against C23.
- Slope limits: pitch along the heading and roll across it, 15 deg each.
  They are planning limits: Scouts strand only where the true ground stops
  them, and unflagged steep steps are counted.
- When a Scout's own map closes every way out, it retraces its recent
  track (`retrace_track_m`, 50 m, assumed), ignoring the map.
- Position fixes: preplanned by default, straight through as the
  comparison case. A fix whose exposures saw no rim does not reset the
  schedule.
- Scouts plan on the LOLA 5 m slope plus their own lidar map
  (`plan_slope` "orbital").
- Confirm rule 6 m, assumed. Dock registration 6 m, assumed; waits on draft
  item 15.
- Lost reasons include `stranded` and `stuck`. No free hop home.
- Beacons only as comms relays, not homing.
- Mission Control never opens a test case by default.
- Hosting local only; never republish lantern-teal.vercel.app.

## Running things

- **One sim at a time on this machine**: live_runs, calibrate,
  build_assets, render scripts. Check first:
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'live_runs|calibrate'`.
  Stop a run with `taskkill /PID <pid> /T /F`; live_runs restarts a killed
  child otherwise.
- Test sortie: `python -m sim.live_runs --sites haynes --scouts 1 --sorties 1
  --batch-id <name> --serial --progress 500`. Add `--set sortie_h=4` for
  about 4 min; with preplanned fixes that tests the corridor only. Full 22 h
  sorties for merge checks or the return leg. Give several overrides in one
  `--set a=1 b=2`; a second `--set` replaces the first.
- Mission Control: `python app.py --no-open --port 8070`, then
  http://127.0.0.1:8070/ (IPv4 only). A new camera run's ground:
  `python web/build_assets.py --run-ground <batch>/<run>`, then
  `python -c "import app; app.sync_runs(None)"`.
