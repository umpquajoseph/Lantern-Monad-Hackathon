# Who is working on what

**Last updated: 2026-11-15 11:15 PST.** Before you start something, add a
row here and push. When you finish, change its status. Formats passed
between parts are in `docs/handoffs.md`: read it before writing a function
another part calls.

## In progress

| Work | Owner | Branch | Files it writes | Started | Status |
|---|---|---|---|---|---|
| Terrain from the LOLA Shoemaker tile, 1 km at 0.25 m, with small craters and the crater list (step 2) | Joseph, agent `terrain` | `step-2-terrain` | `sim/terrain.py`, `data/terrain/` | 10:30 | done 10:45, merged into `joseph` |
| Realistic display of the terrain: surface detail, rocks, reflectance, 20 km backdrop (display only; the sim keeps the 0.25 m grid) | Joseph, agent `terrain` | `step-2-terrain-display` | `sim/terrain_*.py`, `data/terrain/` | 11:15 | building |
| 3D scene: first-person lamp view with depth, third-person chase view (step 6 render) | Joseph, agent `scene` | `step-6-scene` | `sim/scene.py` | 10:30 | building |
| Briefs, review, merging into `joseph` | Joseph, lead session | `joseph` | `docs/`, `ops/PROTOCOL.md` | 10:00 | ongoing |

## Not claimed yet

Claim one by adding a row above. The part numbers are from `4 specs.md`.

| Work | Spec step | Part in `4 specs.md` |
|---|---|---|
| Contract with Foundry tests, and `sim/chain.py` | 1 | 4, Contracts and Chain Bridge |
| Particle filter, `sim/nav.py` | 3 | 3, Navigation and Autonomy |
| Two rovers, planner, ice sensor, `sim/rover.py` | 4 | 3 (planner), 1 (sensor) |
| Dashboard, `sim/dashboard.py`, and the backup video | 5 | 1, World and Sensor Sim |
| Crater rims from the camera image, `sim/vision.py` | 6 | 2, Perception |
| `run_demo.py`, both demo runs end to end | | |
| Pitch: who pays (investability), slides, script | | |
