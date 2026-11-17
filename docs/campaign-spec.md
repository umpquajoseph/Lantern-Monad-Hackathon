# Lantern spec

17 Nov 2026. Approved in chat on 17 Nov 2026. This replaces every earlier
version of this file. The 16 Nov versions (`d2be7f9`, `9fa39e4`, `f1aed12`)
are history only: do not build from them. Formats live in
`docs/handoffs.md`, "Campaign simulator". Constants live in
`sim/settings.py`, each with its source or marked `assumed`.

## Intent, in the product owner's words

"upgrade hackathon's lantern to what our paper proposes, which is a
fundamental workflow for these rovers to map, corroborate, and discover
resources. It's not entirely specific to PROSPECT because im sure we'll
change our paper over time, but its a good start"

## What stays

The hackathon's Lantern: Mission Control as the main page, its views and its
look. The chain, Monad and agency parts go.

## What changes: the rovers run the paper's workflow

1. **Map.** Rovers leave a base, find their way, and add what they see to a
   shared map. Each later trip navigates on a better map.
2. **Discover.** A rover's sensor flags a possible resource. The rover logs
   it with where it thinks it is and how unsure that position is.
3. **Corroborate.** A second rover goes to that spot and checks it. The find
   is confirmed or rejected.

## Not tied to one mission

The workflow is kept apart from the mission's numbers. PROSPECT is the first
set of numbers: 1 to 5 Scouts, one dock, battery limits, an ice sensor and a
drill. When the paper changes, the numbers change and the workflow stays.

## Two decisions (approved 17 Nov 2026)

- **Rovers navigate by their live camera.** Each step renders the lamp-lit
  view, detects crater rims and updates the particle filter. Nothing is drawn
  from a table of published errors.
- **Lantern produces the paper's numbers** from many seeded runs.

## Done when

1. One run plays end to end in Mission Control: the map grows, a rover
   discovers a find, and a second rover checks it, whatever the verdict.
2. A batch of runs gives the numbers the paper quotes, each traceable to the
   runs it came from.

If the workflow cannot complete under honest settings, that is a result for
the paper. Report it. Do not hide it by tuning.

## What the simulation is for (product owner, 17 Nov 2026)

"this is not about forcing rovers to come home but developing a wholistic
and realistic simulation according to our papers proposed workflow, and if
workers cant come home, then fine shadownav isnt accurate enough."

So a change is right when it makes the simulation more faithful to the
paper's workflow or to the real Moon: a bug fixed, a value given a source.
A change is wrong when its purpose is a better outcome. Model only what
the paper proposes: an aid the paper does not propose is not added to
rescue Scouts.
