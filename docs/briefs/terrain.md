# Brief: lantern-terrain (step 2)

You own the terrain for Lantern. Read `CLAUDE.md`, `docs/spec.html` (step 2)
and `docs/handoffs.md` first. They tell you what the project is and what the
other steps expect from you.

## What it is for

Two simulated rovers drive a dark lunar landscape. The rover's camera sees
crater rims lit by a lamp, and a particle filter matches them to a crater
map to find its position. The judges watch it in a first-person view and a
third-person view (another worker builds those, in `sim/scene.py`). Your
terrain is the ground everything stands on. It has to look like real
south-pole ground, and it has to contain the small craters the rover
navigates by, with a crater list that matches them exactly.

Judging rewards "working features, end to end, no gaps". A terrain that
loads fast, looks right and never surprises the other steps beats a clever
one.

## Done means

- `sim/terrain.py` builds the terrain from the LOLA tile, saves it, and
  loads it in the format in `docs/handoffs.md` (step 2 section). That
  format is a contract with three other steps. If you think it is wrong,
  say so in your report and explain why; do not change it on your own.
- A synthetic fallback in the same format, in case the LOLA build has a
  problem late in the day.
- You have looked at what it produces: a top-down shaded map with the
  crater list drawn on it, and a close-up at full resolution lit from a
  low angle, the way the rover's lamp will light it. Downscale images
  before reading them back. Tell me what you saw, including anything that
  looks wrong.

## What is known

- Tiles (git-ignored, so use these absolute paths from your worktree):
  `C:\Monad-Hackathon-Project\data\lola\Shoemaker\` has `_surf` (height,
  m), `_slp` (slope, deg), `_toterr` (height error, m) and `_ldec` (LOLA
  returns per pixel). `C:\Monad-Hackathon-Project\data\lola\README.txt`
  describes them. Haworth and Site04 tiles sit beside it.
- Shoemaker: 4000 x 4000 float32, 5 m pixels, south polar stereographic
  metres. The tiepoint (66000, 49000) is the top-left corner of pixel
  [0, 0] (GTRasterTypeGeoKey = PixelIsArea), so that pixel's centre is at
  (66002.5, 48997.5). Rows run south. Heights run -1075 to 714 m.
- Despite the name, this tile is landing-site high ground near Shoemaker,
  not the crater floor. 85% of it is under 15° slope (median 7.8°).
  Do not label it a shadowed crater floor anywhere.
- About 90% of the 5 m pixels are interpolated (PGDA product 78 page), so
  upsampling raw LOLA gives flat facets. A low lamp may turn facet edges
  into false shadow lines.
- ShadowNav navigates by craters too small for LOLA to see. Its crater set
  (Cauligi et al. 2023, Table 1, in `docs/sources/`) runs 2.3 to 11.9 m in
  diameter and 0.25 to 1.0 m deep. Its renders used orbital elevation
  maps scaled to 0.25 m (section 4A). The rover sees rims at roughly 5 to
  25 m (Atha et al. 2024, section IV), so the filter needs a few craters
  within that range almost everywhere it drives.
- The world is about 1 km across at 0.25 m cells (4000 x 4000), per the
  spec. The demo drives about a kilometre in run 2.

## A starting suggestion, which you may overrule

Pick a 1 km window that is mostly under 15° (the planner's limit). Upsample
it smoothly. Add fine roughness only at scales LOLA cannot see, sized from
the height-error map. Stamp in bowl-shaped craters with raised rims, sized
from Table 1, and record each one. If you find a better way, or think part
of this is wrong, do it your way and say why in your report.

## Hard limits

- Write only `sim/terrain.py`, its output under
  `C:\Monad-Hackathon-Project\data\terrain\`, and a short note in
  `README.md` if you add a dependency. Another session is writing
  `sim/scene.py` at the same time.
- Every number from a source carries the source in a comment. Every other
  number is a named parameter marked assumed.
- Work in your own git worktree on branch `step-2-terrain`, cut from
  `origin/joseph`. If your working directory is the main checkout
  `C:\Monad-Hackathon-Project` itself, create a worktree first. Commit and
  push often. Do not merge into `joseph` or `main`; the lead session does
  that after review.
- No commit message names a person.

## Report

When done, end with: what works, what does not, what you changed from this
brief and why, the output file paths, and the paths of the images you
looked at.
