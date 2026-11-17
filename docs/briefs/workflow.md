# Brief: `workflow` (the simulation)

Branch `lantern-workflow-sim`, worktree `C:\Monad-Hackathon-workflow-sim`,
from `lantern-workflow` `d9d887a`. You own `sim/` and `scripts/`. Do not edit
`web/`, which `control` owns.

Read only these: `docs/campaign-spec.md` (rewritten 17 Nov), this brief,
`docs/handoffs.md` "Campaign simulator", `ops/PROTOCOL.md`. The old briefs
are in `_archive/briefs/`. An earlier spec sent the first build the wrong
way, so do not take scope from them or from old mail.

## Goal

In camera runs, the paper's workflow completes: Scouts map, one discovers
a find, and a second Scout corroborates it. The product owner wants Lantern
"upgrade[d] ... to what our paper proposes, which is a fundamental workflow
for these rovers to map, corroborate, and discover resources."

## Where it stands

Three camera runs ran overnight at `9108d1c`. They are in
`C:\Monad-Hackathon-Project\data\campaigns\live-nav-first3\`. Each had
3 Scouts, at s89_134e, s89_014e and haynes.

- 19 sorties, 2 finds, 0 confirmed, 0 drilled. 6 of 9 Scouts were lost.
- Scouts spent 10-42% of steps turning in place. 108-117 waypoints were
  skipped as blocked on s89_014e.
- An A/B test on the s89_014e sortie gave the same outcome on `9af3ad0`. So
  the turning comes from the planner, not the speed-ups.
- The p95 survey error was 5 to 70 m. The confirm rule needs a bound under
  6 m.

The workflow never reaches corroboration. Find out why. Fix what is a bug.
If honest navigation is too poor for a second Scout to corroborate, that is a
result for the paper. Measure it and report it. Do not hide it.

## Rules

- Every constant you add or change has a source (paper, section, table) or
  is marked `assumed`. Nothing is tuned toward a hoped-for number.
- A format change in `docs/handoffs.md` goes to `leader` and `control` by
  mail first, then lands in the same commit as the code.
- The runs need CuPy and numba. Leave the `LANTERN_*` switches unset.
- Commit and push your branch often. Never merge into `main`.

## Done when

You run the first batch again: 3 Scouts, 3 sorties, at the same three sites.
You look at what happened, and report per run the finds, second-Scout
searches, confirmed, drilled and lost, with run ids. At least one find is
corroborated. If none can be, the report shows why with numbers. Mail
`control` the batch path as soon as it exists.
