# Part 3: terrain and realism (handle `terrain`, branch `campaign-terrain`)

Read `docs/briefs/campaign-common.md` first.

**Goal, first.** Spec 11.1: the LOLA tiles in `data/lola/` may not cover any
shadowed crater floor. Confirm the tiles' coordinates. Find a public LOLA
elevation model that covers a PSR floor plus its rim, and a published map of
permanent shadow. Build terrain windows for 2 or 3 candidate PSRs, each large
enough for a 7 km sortie from a dock on the rim. Mail the leader what you
found, with links and sizes, before downloading anything over 2 GB.

**Then.** `campaign_world(site, seed, ranges)` as in `docs/handoffs.md`
"Part 3 gives part 1": the true crater list at the world's drawn density,
and the Scouts' starting map, missing small craters and a share of larger
ones.

**Then, camera navigation fixes for the showcase run** (draft items 22-24,
spec 7): terrain not chosen for gentle slopes; slope at the native 5 m
spacing or labelled as interpolated; rocks on; the camera on the 1.6 m mast
with the lamp below it (brief p.6); the filter's noise model not copied from
the truth (`sim/nav.py:46-48`).

**Done when.** You have looked at rendered top-down maps of each candidate
window with the dock, the PSR outline and the craters drawn; the source and
licence of each dataset are written down; and `campaign_world` returns a
world that part 1 can run on.
