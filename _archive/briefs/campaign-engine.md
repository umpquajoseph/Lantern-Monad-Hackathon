# Part 1: campaign engine (handle `engine`, branch `campaign-engine`)

Read `docs/briefs/campaign-common.md` first.

**Goal.** One command runs a batch of campaigns with sampled navigation and
writes, per run, the record (`record.json` + `poses.npz`) and, per batch,
`summary.csv` and `by_sortie.csv`, exactly as `docs/handoffs.md` "Campaign
simulator" says. Then Figures 1a and 1b from those files.

**Files.** New `sim/campaign.py` (one run), a batch runner (all cores), a
figures script. `sim/settings.py` is shared: add fields there, sourced.

**What it must model** (spec sections 3 and 4): dock queue; sorties with
descent, survey lines and return; turn-back on energy with the 25% reserve;
faults and module swaps; the check policies; sampled fix error and wrong
matches per the draft 4.2 model; one dock registration error shared by all
Scouts; hidden ice, readings, finds; contact gaps logged. Call part 2's
functions (`sim/crossing.py`) for searches and crater merging; until they
land, a clearly named placeholder that part 2 replaces. Use part 3's
`campaign_world` once it lands; until then, the existing Shoemaker tile.

**Done when.**

1. The hand checks in spec 4a pass, and the batch report prints the
   comparison: Table 1 spacing (80 m, 358 m), stop budget (2.6, 9.0,
   12.3 h), loads (205 W, 270 W).
2. One run of 3 Scouts x 16 sorties takes under a minute (spec 8). Say what
   it actually took.
3. Re-running a run from its seed gives the same record.
4. A small batch (for example 50 worlds x fleet 1-5) gives Figures 1a and 1b
   as PNGs, and you have looked at them and said what they show and what
   looks wrong.
