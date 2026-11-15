# Brief: performance (handle `perf`)

Read `CLAUDE.md`, `ops/PROTOCOL.md`, `docs/WORKING.md`, `docs/handoffs.md`
and this file first.

## What it is for

Joseph, 12:53: "dude its just taking froever have a performance agent go
and try to improve the performance of the scene/terrain build and
navigation".

A full rerun of both demo runs takes about 20 minutes or more, and every
change to the sim needs one. Calvin's vision step will land soon and need
another. Turnaround is the bottleneck for the whole team. Make a rerun of
both runs, and a fresh asset build, as fast as this machine allows,
without changing what they produce.

## Where things are

- Base: branch `integration`, worktree `C:\Monad-Hackathon-integration`.
  `python app.py` there builds missing assets and serves the site on
  http://127.0.0.1:8000. That site is the product.
- `run_demo.py` runs both runs. Per rover per 2 m step it renders a sensor
  frame live in the loop (`sim/scene.py` `Scene.first_person`), then
  localizes (`sim/nav.py` `ParticleNav`, 100 particles) and plans
  (`sim/rover.py`, A* on 2 m cells). After the sim, display frames
  (`fp_sub`, every 0.5 m) render in a spawn pool (`run_demo.py:357`), sized
  to free RAM with mmap-shared arrays. About 0.5-0.7 s per first-person
  frame on CPU (numpy plus VTK; lamp shadows from a polar horizon map,
  `sim/scene.py:400-450`, 1536 azimuths).
- Asset build: `web/build_assets.py`, `sim/terrain_display.py build`,
  `sim/terrain.py`.
- Machine: 16 cores, 15.7 GB RAM with about 4.5 GB free, RTX 4060 Laptop
  GPU (8 GB), which the render does not use today. About 0.61 GB private
  per pool worker, so RAM caps the pool.

## Rules

- Measure first: profile one real rerun and the asset build, find where the
  time goes, and fix the biggest cost first. Your method is your call.
- Output must not change. Same seed means the same poses, estimates, sigmas,
  rims, finds and events. Frames must match too. Report the max and mean
  pixel difference against the current renderer at the same poses. Any
  visible difference needs a pass from `terrain`, the realism checker,
  before it merges.
- No faking. Do not drop resolution, frames, particles, shadows or Hapke
  shading to win time. Do not move sensor frames out of the live loop: a
  real rover sees each frame before it localizes. If a real trade-off is
  worth it, mail the leader with numbers first.
- Constants keep their source comments (paper, section or table).
- Do not start heavy benchmarks before about 13:10. Scene's rerun
  `run1/run2-20261115-1250` is using the RAM until then. Check with `scene`.

## Ownership

- `scene` owns `sim/scene.py` and `run_demo.py` and is editing them.
  Calvin owns `sim/nav.py` and `sim/rover.py` (his branch `origin/Calvin`).
  You may change any of these on your own branch. Tell `scene` by mail what
  you are changing in its files before you change them. One of you edits a
  given function, never both.
- Work in your own worktree, `C:\Monad-Hackathon-perf`, on branch
  `step-6-perf` from `origin/integration`. Do not edit files in
  `C:\Monad-Hackathon-Project` (the leader's checkout). Push often.
  `integrator` merges into `integration`. Never merge into `joseph` or
  `main`.

## How it is checked

Mail the leader a report (`ops/PROTOCOL.md` format) with:

- wall time for both runs and for a clean asset build, before and after,
  on this machine, plus peak RAM;
- where the time went (profile top lines) and what you changed;
- the identity check: log diff of the same-seed runs, plus pixel diff
  numbers for frames;
- what you are blind to.

## Talking to others

Your handle is `perf`. Your inbox is `ops/mailboxes/perf/inbox/`; the
leader's is `ops/mailboxes/leader/inbox/` (in `C:\Monad-Hackathon-Project`;
mailboxes are git-ignored, so write the files there directly). Mail `ts`
comes from `date`. No commit message names a person. Doorbells (`SendMessage`
to these addresses):

| handle | doorbell address |
|---|---|
| leader | `Hackathon project setup review [5247c4]` |
| scene | `uds:\\.\pipe\LOCAL\cc-msg-003a54f5030ab1d0041668abdbf2f6dc` |
| terrain | `uds:\\.\pipe\LOCAL\cc-msg-3956fbcec9b14cf96512bbeaf1a4ac16` |
| integrator | `uds:\\.\pipe\LOCAL\cc-msg-e4aa78077ba4f6511766cd4cc20730b8` |
| viewer | `uds:\\.\pipe\LOCAL\cc-msg-a233f82a3c78cbd382c1279676ac4ac8` |

In your first mail, give the leader your own doorbell address.
