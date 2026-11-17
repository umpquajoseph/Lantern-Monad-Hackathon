# Part 2: cross-checks and map refinement (handle `crossing`, branch `campaign-crossing`)

Read `docs/briefs/campaign-common.md` first.

**Goal.** The paper's two contributions as plain code (spec 3.5, draft 4.3
and 4.4). First, a second Scout searches around a find and confirms it or
not. Second, unmatched rim sightings from different sorties and Scouts are
merged at the dock into new map craters, only when independent sightings
agree.

**Files.** New `sim/crossing.py`: pure functions with the signatures in
`docs/handoffs.md` "Part 2 gives part 1". No I/O, no global state; the rng is
passed in.

**Points to get right.**

- The searcher is never the finder. The search radius comes from both Scouts'
  95th-percentile bounds and the sensor footprint.
- Both Scouts share the dock registration error, so their errors are not
  independent (draft 4.4). The judgement must not treat them as independent.
- Added craters carry the observers' position error, and their size comes
  from a partial rim (draft 4.3). A crater added in the wrong place must be
  able to cause wrong matches later.
- False confirmations and missed deposits are counted against the hidden
  truth, and no Scout ever reads the truth.

**Done when.** Your functions run inside part 1's loop (agree with `engine`
by mail how and when they merge in), and on one run you have looked at the
record and seen a find confirmed by another Scout, one left unconfirmed, a
crater added after two sightings, and a wrong match caused by a misplaced
added crater, or the reason none occurred.
