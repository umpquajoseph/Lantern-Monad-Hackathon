# Worker `live-nav`: camera navigation inside the campaign loop

Branch `campaign-live-nav`, worktree `C:\Monad-Hackathon-live-nav`.
Read `docs/briefs/live-common.md` first.

**Goal.** Every campaign run navigates by camera vision. In
`sim/campaign.py` each Scout, at every step, renders its lamp-lit view
(`sim/scene.py`), detects rims (`sim/vision.py`), updates its particle
filter (`sim/nav.py` `ParticleNav`), scans with the lidar (`sim/lidar.py`)
and plans on what it sees. The sampled navigation (`nav: "sampled"`) stays
only as an option for the paper's comparison column, off by default.
`sim/showcase.py` already runs this pipeline for a short two-Scout run
(it reuses `run_demo.py`'s loop). The campaign's sorties, dock queue,
energy, turn-back, finds, cross-checks and crater-map merging
(`sim/crossing.py`) must keep working on top of it.

**Constraints.**

- Frames are not saved to disk. Render, use, drop.
- The record keeps, per Scout per step, what the pipeline produced, so every
  Mission Control view can replay without images: true and estimated pose,
  95th-percentile bound, the particle cloud (or a compact form of it), the
  rim points used (which mapped crater each matched, and whether that match
  was wrong against the truth), the lidar scan, and the planned route.
  Write the format into `docs/handoffs.md` "Run record" early and push it,
  because worker `mission` reads it. Then keep it stable.
- Ground: the camera needs fine ground. Use the run ground part 3 built
  (`sim/site_ground.py`, `sim/terrain.py`) over the whole sortie area. Say
  what the camera sees if a sortie leaves fine ground.
- The starting crater map, the filter's noise model and rim-depth noise are
  owned by worker `realism` (`sim/nav.py`, `sim/vision.py`, the orbital-map
  cut). Call them through their existing interfaces and do not edit those
  files beyond the call site. Pull `origin/campaign-realism` when it lands.

**Speed.** Measure first, before optimising: seconds per Scout-step, split
into render, vision, filter, lidar and planner. Then make it faster without
changing what the Scouts see. Report both numbers.

**Done when.**

1. One campaign of 2 Scouts x 2 short sorties runs on camera vision, and the
   record shows real rim matches, fixes and the particle bound along the way.
2. The first 3 runs launch with one command: 3 runs, each with 3 Scouts and
   3 sorties, one each on sites `s89_134e`, `s89_014e` and `haynes`, map
   refinement on. Print the expected wall time from your measured speed. Do
   not launch the three; the leader does after merging.
