# Brief: the integrator (one app, everything wired)

You own Lantern as one working app. Read `CLAUDE.md`, `docs/spec.html`,
`docs/handoffs.md`, `docs/WORKING.md` and `ops/LEADER_STATE.md` first.

## What it is for

Joseph: "have a new agent compile everything into a single app or something
so everything is wired together, right now its all piecewise."

Today the pieces run apart:

- **Terrain** (`sim/terrain.py`, merged): `load_terrain()` gives the 1 km
  grid and crater list.
- **Display layer** (`sim/terrain_display.py`, merged): texture, rocks and
  the real LOLA backdrop. Only the slow still renderer
  `sim/terrain_look.py` uses it.
- **Scene** (`sim/scene.py`, merged): first person and chase view at
  0.17-0.29 s per frame. It does not load the display layer, so
  `python preview.py` shows untextured ground.
- **Demo spine** (`run_demo.py`, worker `scene`, branch `step-4-spine`,
  in progress): both demo runs, ice, stand-ins, and run logs.
- **Web viewer** (`web/`, worker `viewer`, branch `step-5-viewer`, in
  progress): the hosted page that replays run logs.
- **Chain** (teammate's branch `origin/step-1-chain`): a contract, Foundry
  tests and a Python client. Not Joseph's; read it, do not edit it.

Joseph wants one thing he can launch and see the whole demo working, and
later a hosted link (a requirement; local-only is disqualified).

## Done means

- One command builds what is missing, runs the demo, and opens the app on
  it, end to end. That covers the textured terrain, both rovers, the ice
  finds and checks, chain transactions (real if step 1 is usable, otherwise
  labelled stand-ins), and both views.
- The seams between pieces are found and fixed, or handed to the owner with
  a precise ask.
- You have run it the way a judge would and looked at the result. Report
  what you saw, with screenshots and the command.

## Who owns what

You own the wiring: the single entry point (for example `app.py` or a
`lantern` command), the glue between pieces, and an integration branch where
everything is tested together. The others keep their parts:

- `scene` owns `sim/scene.py` and the spine (`run_demo.py`, `sim/rover.py`,
  ice and stand-ins).
- `viewer` owns `web/`.
- `terrain` owns `sim/terrain*.py` and the data. It is idle now and is the
  natural one to wire the display layer into the scene, if `scene` agrees.
- The chain is a teammate's.

When a seam is in someone else's file, mail them the exact change you need,
and why. If they are busy or blocked, mail me and I decide. Do not rewrite
another worker's code. Small glue in your own files is yours.

## Talking to others

Your handle is `integrator`. Follow
`C:\Monad-Hackathon-Project\ops\PROTOCOL.md`. Ask me (`leader`) or any worker
by mail. Their watchers are armed; ring the doorbell with `SendMessage` at
these addresses. All sessions share one display name, so use the address.

| handle | doorbell address |
|---|---|
| leader | `Hackathon project setup review [5247c4]` |
| terrain | `uds:\\.\pipe\LOCAL\cc-msg-3956fbcec9b14cf96512bbeaf1a4ac16` |
| scene | `uds:\\.\pipe\LOCAL\cc-msg-003a54f5030ab1d0041668abdbf2f6dc` |
| viewer | not known yet; mail alone reaches it |

## Hard limits

- Work in your own worktree on branch `integration`, cut from
  `origin/joseph`. You may merge other workers' pushed branches into
  `integration` to test them together. Never merge into `joseph` or `main`;
  I do that after review.
- Real versus made-up stays visible on screen and in data.
- No keys in anything that gets hosted.
- No commit message names a person. Mail `ts` comes from `date`.

## Report

A `report` mail to `leader` with the headings in `ops/PROTOCOL.md`, plus
the launch command and the screenshots you looked at. Send interim reports
when something lands; do not wait for everything.
