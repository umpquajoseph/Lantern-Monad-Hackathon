# Brief: `sensors` (lidar hazards, and the two unsourced sensor numbers)

Handle `sensors`. Branch `lantern-sensors`, worktree
`C:\Monad-Hackathon-sensors`, from `lantern-workflow`. Leader: `leader`.
Model: Opus.

Read first, in this order: `ops/HANDOFF.md`, `docs/campaign-spec.md`, this
brief, `ops/PROTOCOL.md`. Then the sources named below, in
`docs/sources/`. Anything in `_archive/` is history and sets no scope.

## Goal

Bring what the Scouts sense up to parity with the Moon and with the
workflow the team's paper proposes. The product owner, 20 Nov: "this is
not to check arbitrary boxes but to bring the sim up to parity with
shadownav and the workflow our paper proposes." A change is right when a
sensor reports what a real one would on that ground. It is wrong when its
purpose is a better result. Hazards decide where Scouts can drive, and
the ice sensor decides what they discover and corroborate (spec: map,
discover, corroborate), so both shape the paper's numbers.

## The work, in order

1. **False lidar craters on plain slopes.** On a synthetic 10° by 6° ramp
   `sim/lidar.py hazards` flags 36-44 "crater" cells about 2.2 m ahead,
   with or without body tilt. The handoff suspects the ring-mean edge bias
   (`ring_mean` near the edge of the scanned area, where the ring is cut
   off on one side). Reproduce it first, find the cause, fix the class:
   every place the ring or the slope test reads a cut-off neighbourhood.
   Then check that real craters are still found (ramp plus stamped
   craters, and a run ground).
2. **The tilted-lidar slope sample.** At step 450 of `tilt-check`, 1 of the
   tilted lidar's 8 slope cells was truly over 15°, against 11 of 15 for a
   level lidar. One sample. Check many poses before calling it a bug:
   render poses of that run with tilt on and off and compare the lidar's
   slope cells with `sim/footprint.tilt_deg` on the ground.
3. **Ice sensor false alarms.** The neutron spectrometer stand-in
   (`sim/ice.py`) uses an assumed noise of 0.15 wt% and the brief's 1 wt%
   threshold. Find what a real rover-borne neutron spectrometer reports:
   its noise or detection limit at the Scout's integration time and speed,
   and so how often a reading crosses 1 wt% with no ice. Lead, from
   memory and unverified: VIPER's NSS and Lunar Prospector's neutron
   spectrometer papers. Search wide.
4. **Map crater error in shadow.** `sim/orbital_map.py` places each mapped
   crater with an assumed 1 orbital pixel of position and size error.
   Find what published PSR crater catalogues and shadowed-terrain maps
   achieve (ShadowCam, LROC NAC, LOLA). `docs/terrain/position.md` already
   cites Mazarico et al. 2025: ShadowCam counts complete from 18 m, image
   geolocation about 20 m. Read it in `docs/sources/`.

For 3 and 4, copy every source you use into `docs/sources/` with a text
copy, add it to `docs/SOURCES.md`, and give each setting its source.
Download only from the publisher or an author's or agency's page.

## Your files

`sim/lidar.py`, `sim/ice.py`, `sim/orbital_map.py`, `sim/footprint.py`
(read-only unless the slope test itself is wrong; then mail `leader`),
their fields in `sim/settings.py`, `docs/sources/`, `docs/SOURCES.md`.
`nav` owns `sim/nav.py`, `sim/campaign_camera.py`, `sim/rover.py`,
`sim/campaign.py`, `sim/live_runs.py`; do not edit those. A format change
in `docs/handoffs.md` is told to `leader` first and made in the same
commit.

## Rules

- Check every claim against the source, never memory or a summary,
  this brief included.
- A number from a source names it in a comment; anything else is a named
  setting marked `assumed`.
- Look at what you made: the hazard grid drawn over the ground, the lidar
  panel, the readings plotted. An exit code is not a look. Single test
  sorties only, no batches.
- Commit and push `lantern-sensors` early and often. Never merge into
  `lantern-workflow` or `main`; `leader` merges.
- Decide, build, check, push, then report. Ask `leader` only about scope,
  what the paper will say, and merges.
