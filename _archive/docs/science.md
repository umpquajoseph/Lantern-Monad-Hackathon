# Rover operations: missions, science, health, JSON storage

Branch `ritvik`. New files only; nothing another part owns was edited.

## Run it

```bash
export LANTERN_DATA="$PWD/lantern_data"          # Joseph's shared terrain
python sim/mission.py run                         # both rovers, full sorties
python sim/mission.py push --dry-run              # the chain calls it queued
```

To drive with Calvin's planner from his branch without checking it out:

```bash
git show origin/Calvin:sim/rover.py > /tmp/calvin_rover.py
LANTERN_PLANNER=/tmp/calvin_rover.py python sim/mission.py run
```

Needs numpy, scipy, matplotlib; web3 for keccak hashes that match
`sim/chain.py` (without it, sha256, labelled). Runs in about a second.

## What the rover does (rover functions, and where they live)

| Function | File | Grounded in |
|---|---|---|
| Route planning, obstacle avoidance | teammate's planner via `sim/planner_link.py` | Calvin's `Planner`+`Drive`, or the spine's `SlopeMap`, or a labelled stand-in |
| Mission planning: score, choose, order targets | `sim/mission.py` `choose_target` | value / distance, only what it can afford |
| Energy: battery, reserve, return home | `sim/health.py` | 7.9 kWh, 25% reserve, 22 h sortie, 10 cm/s ([PB] p. 3, 6) |
| Thermal: heater load in every energy sum | `sim/health.py` | ASSUMED watts |
| Comms: Earth in view or not; finds queue until it is | `sim/health.py`, outbox `earliest_post_t` | up to 11 days out ([PB] p. 6), compressed for the demo |
| Diagnostics, recovery: drill stall, retry, give up | `sim/mission.py` `science_stop` | ASSUMED stall chance |
| Emergency: abort a leg when the battery hits the return line | `sim/mission.py` `drive` | [PB] p. 6 reserve |
| Prospecting: neutron survey every 5 m while driving | `sim/resources.py` `neutron_reading` | hydrogen to ~1 m ([PB] p. 5) |
| Autonomous exploration: strong survey reading -> new target | `sim/mission.py` `take_survey` | |
| Resource map: survey binned into a grid | `resource_map.json` | |
| Sample collection: arm, 1 m drill, core profile | `sim/science.py` | 1 m drill + mass spectrometer ([PB] p. 5) |
| Geology: regolith class from ilmenite | `sim/resources.py` `classify_regolith` | ilmenite as the oxygen feedstock ([PB] p. 5) |
| Discovery report: record with position, lat/lon, route, result | `sim/science.py` | |
| Safety zone while drilling | outbox `post_safety_zone` | Artemis Accords s. 11 (spec "Upgrades"), step 1 contract |
| Multi-rover coordination: claims, checks across agencies | `sim/resources.py` `TargetBoard` | spec: only another agency can check |
| Blockchain reporting: outbox, push in order | `sim/mission_store.py` `push_outbox` | step 1 `LanternClient` |
| Animation of a science stop | `science/science_anim.js` | |

## Where the data goes

Three layers; the full design is in [storage.md](storage.md).

- **Chain**: per find, position, sigma, reading and the 32-byte dataHash,
  which is also the report's IPFS CID digest.
- **Evidence** (`data/evidence/objects/<cid>.json`): one report and one
  thinned route per science stop, write-once, verified on every read.
- **Cache** (`data/lantern.db`, SQLite): telemetry, events, survey, reports
  index, outbox, chain index.

Per mission, `data/missions/<mission_id>/` (git-ignored; `LANTERN_OUT` moves
it) keeps the small files people and the viewer read: `mission.json`,
`reports/<id>.json` (evidence refs plus the hidden truth), `outbox.json`,
`targets.json`, `resource_map.json`, `run_log.json`, `HIDDEN_TRUTH.json`,
`map.png`.

## For the chain step (leader's Claude)

```bash
python sim/mission.py push --dry-run     # the calls, in order
python sim/mission.py push               # post (needs sim/chain.py and .env)
python sim/mission.py verify             # dataHash -> CID -> report -> route
```

Safe to repeat: it reads the contract first and skips anything already
there (finds by dataHash, checks by find and agency, zones by the report CID
in their purpose). Tested against a fake client with the same signatures
and refusal rules; not yet against the live contract.

## For app.py (the team's base)

```bash
python sim/mission.py run --publish-run <DATA>/runs
```

adds the mission as a run next to `run_demo.py`'s, so `python app.py` shows
it in the viewer. On Joseph's machine `<DATA>` is
`C:\Monad-Hackathon-Project\data`; on a Mac use `data/runs` (not
`lantern_data/`, which is committed).

## For the viewer

`run_log.json` follows `docs/handoffs.md`. It adds a `science` event
(`science.run_log_event`) carrying `phases`, which
`science/science_anim.js` plays. Proposed; the lead decides.

## Settle with the team

- Two different `sim/rover.py` files exist (Calvin's planner on `Calvin`,
  the spine's on `step-4-spine`). `planner_link.py` works with either.
- The spine's `sim/ice.py` is a second hidden ice map. One should feed a
  run; `resources.as_truth()` gives the run log's `truth` shape.
- Navigation is a stand-in (true pose plus a wandering error) until step 3.
- Science timings are demo seconds, so "speed made good" reads near drive
  speed. With real drill times it would drop.

## Grounded vs assumed

Grounded in the PROSPECT brief: battery, reserve, sortie length, wheel speed,
speed target, drill depth, instruments, ilmenite, the ~1 wt% threshold (the
brief's own assumption, p. 9), LCROSS 5.6 wt% ceiling (p. 12). Everything
else is a named parameter marked ASSUMED in the code.
