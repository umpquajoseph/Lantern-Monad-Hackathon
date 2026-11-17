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

Work out what position Lantern and the paper take on terrain generation
and terrain detail: how the ground is made, and how much detail it has at
each scale. Get there by deep research into sources and into current
precedent in lunar simulators. The product owner: terrain "largely
dictates the results so surely it should be defensible."

The position is the deliverable. Settings values follow from it.

## Why now

`workflow`'s batch `C:\Monad-Hackathon-Project\data\campaigns\workflow-first3c`
lost all 9 Scouts. At 7 of 8 stalls, the assumed small-scale roughness
alone made the ground too steep. The LOLA layer was over the slope limit
on 0-9% of that ground. Its report is
`C:\Monad-Hackathon-Project\ops\mailboxes\leader\archive\20261117-131545_workflow_report.md`.
Navigation error also depends on terrain, through the small-crater density
and the orbital map cut.

## What to research

1. **Precedent.** How do current lunar simulators and simulation-based
   lunar rover studies generate terrain and fine detail? Include
   ShadowNav (Atha 2024 sec. IV, Cauligi 2023, in `docs/sources/`), and
   other planetary navigation and traverse-planning work and lunar
   simulators. Note what each one generates, from what data, at what
   scale, and how it justifies that. Say which are peer-reviewed.
2. **Measurements.** What published lunar data constrain the ground at
   each scale, from orbital elevation maps down to the meter scale a
   Scout drives on: slopes, roughness, crater and rock populations,
   ideally for south-polar shadowed craters.
3. **Lantern today.** Every terrain choice that moves the results, with
   its current value and source as `sim/settings.py` gives it. At least
   these:
- the LOLA product and resolution, and how it is upsampled;
- the small-scale roughness added on top;
- the crater density, size distribution and shapes, and the map cut of
  craters a Scout knows from orbit;
- the slope limit the planner uses, and the scale it is measured on.

One lead, from memory and unverified: Rosenburg et al. 2011, lunar slopes
and roughness from LOLA. Search wide. Do not stop at the first source that
fits.

## Rules

- Check every claim against the paper itself, never memory or a summary.
  Cite section, table or page. Say plainly where no source exists.
- Save open-access sources you rely on into `docs/sources/`, and list
  them in `docs/SOURCES.md`.
- You may measure the current terrain with your own scripts in
  `bench/terrain/`. Ask `workflow` by mail for any run you need.
- Commit and push your branch often. Never merge into `main`.

## Done when

`docs/terrain/position.md` holds:
1. The position in a few sentences: how Lantern generates terrain and
   detail, and why that is defensible, with the precedent it follows.
2. A precedent table: each simulator or study, its terrain method and
   scale, and its source.
3. One row per terrain choice: current value, recommended value, source,
   and the sentence the paper would say.
4. Which choices need a sensitivity case in the paper.

Mail `leader` a report that ends with the decisions the product owner
must make.
