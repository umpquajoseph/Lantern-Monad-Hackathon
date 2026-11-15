# Who is working on what

**Last updated: 2026-11-15 12:31 PST.** Stable checkpoint: tag `checkpoint-1105`.

**The base is the localhost site.** `python app.py` (branch `integration`) serves the site the
sim runs on, and the same site gets hosted. All work plugs into it. A part is done when it shows
there. Push your branch and tell the integrator, who merges it into `integration`.
 Before you start something, add a
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
| Integration: one launchable app wiring terrain, display layer, scene, spine, chain and viewer together | Joseph, agent `integrator` | `integration` | `app.py` and glue; asks owners for changes in their files | 11:22 | running: `python app.py` serves the site; judge pass 1 done; hosting off for now |
| Realism, first person: display layer, Hapke reflectance and exposure in the rover camera | Joseph, agent `scene` | `step-6-scene` | `sim/scene.py` | 11:28 | building, about 12:40 |
| Realism, third person: Hapke shading, rocks and the near backdrop in the site's 3D view | Joseph, agent `viewer` | `step-5-viewer` | `web/` | 11:36 | starting |
| Realism check: Hapke values against Xu 2020, reference stills, comparisons with ShadowNav images | Joseph, agent `terrain` | `step-2-terrain-realism` | `sim/terrain*.py`, `docs/sources/` | 11:36 | starting |
| Navigation: particle filter `sim/nav.py` and planner fix `sim/rover.py` | Calvin | `Calvin` -> `integration` | `sim/nav.py`, `sim/rover.py` | 11:55 | in `integration`, run 1 live on the site |
| Vision: crater rims from first-person frames (step 6) | Calvin | `Calvin` | `sim/vision.py` | 12:20 | building |
| Rim answer key for scoring vision | Joseph, agent `terrain` | `step-6-rim-truth` | `sim/rim_truth.py` | 12:18 | done 12:25 |
| Site look: clean, uncluttered layout | Joseph, agent `designer` | `step-5-design` | `web/app.css`, `web/index.html` layout | 12:29 | starting |
| Briefs, review, merging into `joseph` | Joseph, lead session | `joseph` | `docs/`, `ops/PROTOCOL.md` | 10:00 | ongoing |

## Not claimed yet

Claim one by adding a row above. The part numbers are from `4 specs.md`.

| Work | Spec step | Part in `4 specs.md` |
|---|---|---|
| Contract with Foundry tests, and `sim/chain.py` (not Joseph) | 1 | 4, Contracts and Chain Bridge |
| Particle filter, `sim/nav.py` (Calvin is building it; it comes in after the realism work) | 3 | 3, Navigation and Autonomy |
| Crater rims from the camera image, `sim/vision.py` | 6 | 2, Perception |
| Pitch: who pays (investability), slides, script | | |
