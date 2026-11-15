# Who is working on what

**Last updated: 2026-11-15 11:30 PST.** Stable checkpoint: tag `checkpoint-1105`. Before you start something, add a
row here and push. When you finish, change its status. Formats passed
between parts are in `docs/handoffs.md`: read it before writing a function
another part calls.

## In progress

| Work | Owner | Branch | Files it writes | Started | Status |
|---|---|---|---|---|---|
| Terrain from the LOLA Shoemaker tile, 1 km at 0.25 m, with small craters and the crater list (step 2) | Joseph, agent `terrain` | `step-2-terrain` | `sim/terrain.py`, `data/terrain/` | 10:30 | done 10:45, merged into `joseph` |
| Realistic display of the terrain: surface detail, rocks, reflectance, 20 km backdrop (display only; the sim keeps the 0.25 m grid) | Joseph, agent `terrain` | `step-2-terrain-display` | `sim/terrain_*.py`, `data/terrain/` | 10:46 | done 11:00, merged into `joseph` |
| 3D scene: first-person lamp view with depth, third-person chase view (step 6 render) | Joseph, agent `scene` | `step-6-scene` | `sim/scene.py` | 10:30 | done 10:49, merged into `joseph` |
| Real-crater terrain from a 1 m south-pole DTM (Hemmi et al. 2025, CR1 connecting ridge), LOLA build stays default until reviewed (step 2) | Joseph, agent `terrain` | `step-2-terrain-real` | `sim/terrain*.py`, `data/terrain/` | 11:13 | dropped 11:25: too few real craters for the demo; not merged |
| Web viewer: the hosted demo page, replaying runs with live 3D third person, first-person frames, map, finds and chain feed (step 5) | Joseph, agent `viewer` | `step-5-viewer` | `web/` | 10:55 | merged 11:27; hosting next |
| Demo spine: `run_demo.py` writing both demo runs end to end, hidden ice map and sensor, stand-ins for nav, planner and chain (steps 4 and 5 glue) | Joseph, agent `scene` | `step-4-spine` | `run_demo.py`, `sim/rover.py`, `sim/ice.py`, `sim/standin_*.py` | 10:58 | done 11:21, merged into `joseph` |
| Integration: one launchable app wiring terrain, display layer, scene, spine, chain and viewer together | Joseph, agent `integrator` | `integration` | `app.py` and glue; asks owners for changes in their files | 11:22 | starting |
| Briefs, review, merging into `joseph` | Joseph, lead session | `joseph` | `docs/`, `ops/PROTOCOL.md` | 10:00 | ongoing |

## Not claimed yet

Claim one by adding a row above. The part numbers are from `4 specs.md`.

| Work | Spec step | Part in `4 specs.md` |
|---|---|---|
| Contract with Foundry tests, and `sim/chain.py` (not Joseph) | 1 | 4, Contracts and Chain Bridge |
| Particle filter, `sim/nav.py` | 3 | 3, Navigation and Autonomy |
| Crater rims from the camera image, `sim/vision.py` | 6 | 2, Perception |
| Pitch: who pays (investability), slides, script | | |
