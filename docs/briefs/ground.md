# Brief: `ground` (terrain position for the paper and the sim)

Branch `lantern-ground`, worktree `C:\Monad-Hackathon-ground`, from
`lantern-workflow-sim` `f463d02`. You research and recommend. You do not
change `sim/` or `web/`: `workflow` owns `sim/` and builds whatever the
product owner approves. Your own files go in `docs/terrain/` and
`bench/terrain/`.

Read only these: `docs/campaign-spec.md`, this brief, `ops/PROTOCOL.md`,
and the code and settings named below. Old briefs in `_archive/` are
history and do not set scope.

## Goal

Decide what the paper and the sim should do about terrain, so that every
terrain choice can be defended to a reviewer. The product owner: terrain
"largely dictates the results so surely it should be defensible."

## Why now

`workflow`'s batch `C:\Monad-Hackathon-Project\data\campaigns\workflow-first3c`
lost all 9 Scouts. At 7 of 8 stalls, the assumed small-scale roughness
alone made the ground too steep. The LOLA layer was over the slope limit
on 0-9% of that ground. Its report is
`C:\Monad-Hackathon-Project\ops\mailboxes\leader\archive\20261117-131545_workflow_report.md`.
Navigation error also depends on terrain, through the small-crater density
and the orbital map cut.

## What to cover

Every terrain choice that moves the results, with its current value and
source as `sim/settings.py` gives it. At least these:
- the LOLA product and resolution, and how it is upsampled;
- the small-scale roughness added on top;
- the crater density, size distribution and shapes, and the map cut of
  craters a Scout knows from orbit;
- the slope limit the planner uses, and the scale it is measured on.

For each one, find what ShadowNav did (Atha 2024, Cauligi 2023, in
`docs/sources/`) and what published lunar measurements say, ideally for
south-polar shadowed craters. One lead, from memory and unverified:
Rosenburg et al. 2011, lunar slopes and roughness from LOLA.

## Rules

- Check every claim against the paper itself, never memory or a summary.
  Cite section, table or page. Say plainly where no source exists.
- Save open-access sources you rely on into `docs/sources/`, and list
  them in `docs/SOURCES.md`.
- You may measure the current terrain with your own scripts in
  `bench/terrain/`. Ask `workflow` by mail for any run you need.
- Commit and push your branch often. Never merge into `main`.

## Done when

`docs/terrain/position.md` holds one row per terrain choice. Each row
gives the current value, the recommended value, its source, and the
sentence the paper would say. It also lists which choices need a
sensitivity case in the paper. Mail `leader` a report that ends with the
decisions the product owner must make.
