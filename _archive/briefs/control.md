# Brief: `control` (Mission Control)

Branch `lantern-workflow-web`, worktree `C:\Monad-Hackathon-workflow-web`,
from `lantern-workflow`. You own `web/`. Do not edit `sim/`,
which `workflow` owns.

Read only these: `docs/campaign-spec.md` (rewritten 17 Nov), this brief,
`docs/handoffs.md` "Campaign simulator", `ops/PROTOCOL.md`. The old briefs
are in `_archive/briefs/`. An earlier spec sent the first build the wrong
way, so do not take scope from them or from old mail.

## Goal

A viewer opens Mission Control, picks a real camera run, and watches the
paper's workflow happen. The map grows, a Scout discovers a find, a second
Scout searches for it, and the find is confirmed or rejected. The product
owner wants Lantern "upgrade[d] ... to what our paper proposes, which is a
fundamental workflow for these rovers to map, corroborate, and discover
resources."

## Where it stands

Mission Control (merged from `campaign-mission`) plays stand-in runs and
one early camera run, `live-nav/w00001-bcce1993`, in every view. It has not
played a real batch. Three real camera runs are in
`C:\Monad-Hackathon-Project\data\campaigns\live-nav-first3\`. `workflow` will
mail you a new batch when it lands.

## Decided

When hidden truth is off, the 3D view and the map draw where each Scout
believes it is. Truth on adds the true pose beside it.

## Rules

- Real runs are the default. Anything made up stays labelled as a stand-in.
- A format you need from the simulation goes to `workflow` and `leader` by
  mail. Do not change `docs/handoffs.md` alone.
- Commit and push your branch often. Never merge into `main`.

## Done when

You play a real camera run start to finish in the browser. You look at it
through screenshots, downscaled before reading. You report what a viewer sees
at a find, at the second Scout's search, and at the outcome. Until a run with
a corroboration exists, show the closest one and say what is missing.
