# Brief: lantern-viewer (the web viewer, the demo link)

You own the web viewer for Lantern: the page judges open from the demo
link. Read `CLAUDE.md`, `docs/spec.html` (the demo and step 5) and
`docs/handoffs.md` (Scene and Run log sections) first.

## What it is for

Two rovers from two agencies drive a dark lunar crater. Each finds its
position from lamp-lit crater rims (after JPL's ShadowNav), posts ice finds
with its position uncertainty to a Monad contract, and checks the other
agency's finds. The demo runs twice: run 1 with crater fixes on, and run 2
with rover A's fixes off, where its find ends up disputed.

The rules: projects hosted only locally are disqualified, and there must be
a demo link. The plan is to build locally and port to a hosted link at the
end. So this page is both the step 5 dashboard and the thing that gets
hosted. It replays real runs from the run log in `docs/handoffs.md`. The
heavy rendering stays in Python (`sim/scene.py`) for first person. The
viewer draws third person live in the browser, on the judge's own GPU, so
they can orbit, pause and scrub.

Judging criteria: investability; creativity; completeness ("clean ui/ux and
working features, end to end, no gaps"); presentation.

## Done means

- A static page (no server needed beyond a file host) that loads one or more
  runs and shows, on one timeline:
  - Each rover in 3D on the real terrain, third person.
  - Its recorded first-person frames.
  - A map with the true, odometry and estimated tracks and the uncertainty
    circle.
  - Each find and check with its status: reported, confirmed or disputed.
  - The chain feed with explorer links.
- It runs locally from one command, and it is ready to deploy as static files
  (for example to Vercel or Netlify) with nothing machine-specific in it.
- You have driven it the way a judge would, start to finish, and looked at
  it.

## What is known

- Terrain: `C:\Monad-Hackathon-Project\data\terrain\shoemaker.npz`. That is
  4000 x 4000 float32 at 0.25 m, 64 MB, which is too big to ship raw. Load it
  with `load_terrain()` in `sim/terrain.py`. How you get it to the browser
  (quantised, tiled, decimated) is your call.
- The terrain worker is making display assets right now on
  `step-2-terrain-display`: surface detail, rocks, moon-like reflectance and
  a 20 km LOLA backdrop. They will be data files under
  `C:\Monad-Hackathon-Project\data\terrain\`. Plan to load them; start
  without them.
- `sim/scene.py` (merged in `joseph`) renders the first-person frame with
  depth, and a chase view you can use as a reference for the look. Its
  `python -m sim.scene` renders a scripted 80 m drive. Lamp: camera 2.5 m up,
  lamp 0.2 m below it, 60° horizontal field of view.
- No run log exists yet. `run_demo.py` is not written, and the contract
  (step 1) belongs to a teammate. Make your own stand-in run with
  `sim/scene.py` and scripted poses, in the run-log format, with fake
  transaction hashes clearly marked as fake.
- Machine: Windows 11, RTX 4060, Python 3.13. Check whether Node is
  installed before you plan on it.

## UI

Joseph wants it simple and elegant, similar to the MEWracle-Developer app,
and "not obviously AI". MEWracle-Developer is at
`C:\MEWron 0.2\MEWweaver (Experimental Physics Build)\v3 tests`; read it,
do not write to it. It is a reference for the feel, not a template: Joseph
said he does not want it "1:1 to mewracle because the projects are much
different but thats just what i mean by simple and elegant". The UI stays
open until close to submission, so get the
structure and flow right and do not sink time into polish yet.

## Hard limits

- Write only under `web/`, plus any small script it needs (for example
  `web/build_assets.py` or `web/make_standin_run.py`). Do not edit `sim/`,
  which other workers own. If you need a change there, ask by mail.
- The run-log format is shared with the future `run_demo.py`. Change it only
  by mailing the lead with the reason; the lead updates `docs/handoffs.md`.
- No keys or secrets in the page. It reads the chain, it never signs.
- Real versus made-up stays visible: stand-in data and fake hashes are
  labelled as such on screen.
- Work in your own git worktree on branch `step-5-viewer`, cut from
  `origin/joseph`. Commit and push often. Do not merge.
- No commit message names a person.

## Mail and report

Your handle is `viewer`. Talk to the lead session by mailbox, following
`C:\Monad-Hackathon-Project\ops\PROTOCOL.md`. Questions go to `leader` as
one mail. When done, send a `report` mail to `leader` with the headings in
that file, plus how to run it and the screenshots you looked at.
