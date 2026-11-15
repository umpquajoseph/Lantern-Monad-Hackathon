# Brief: the demo spine (`run_demo.py`)

You own the spine of the demo: one command that runs both demo runs end to
end and writes the run logs the web viewer replays. Read `CLAUDE.md`,
`docs/spec.html` (the demo, steps 3 to 5) and `docs/handoffs.md` (all of it)
first.

## What it is for

Judging rewards "working features, end to end, no gaps". Right now the
pieces exist apart: real terrain (`sim/terrain.py`) and a 3D scene with
first-person frames (`sim/scene.py`). No rover drives on it, no ice exists,
and nothing writes a run. The web viewer (worker `viewer`, branch
`step-5-viewer`) is being built against a stand-in log. Your runs replace
that stand-in.

The story the demo tells (spec, "The demo"):

- **Run 1, crater fixes on.** Rover A drives in the dark, finds ice and posts
  it with its position uncertainty. Rover B, from the other agency, reads the
  find, drives there, measures, and posts a check that agrees. Status:
  confirmed.
- **Run 2, A's crater fixes off.** After about a kilometre of odometry alone,
  A is about 20 m unsure (2% drift, ShadowNav section 1), so its find's
  circle is about 40 m wide. B measures at the reported spot, finds nothing,
  and posts a check that disagrees. The find stays on the record, disputed.

## Stand-ins, so the real parts drop in later

Teammates may build the real particle filter (step 3), planner (step 4) and
contract with chain client (step 1). Write the spine against the interfaces
in `docs/handoffs.md`, with the simplest honest stand-in behind each. Then a
real part replaces its stand-in and nothing else changes. Label each
stand-in in the code and in the log, so nobody mistakes it for the real
thing.

- Navigation: with fixes off, dead reckoning with 2% distance noise. With
  fixes on, something that behaves like a working filter (for example true
  pose plus noise at a small sigma), clearly marked as a stand-in.
- Planner: anything that drives sensibly on the terrain and respects the 15°
  limit measured across 2 m (`docs/handoffs.md`, step 2).
- Chain: fake transaction hashes that are obviously fake. `sim/chain.py`
  belongs to the contract owner. Use its interface from `docs/handoffs.md`,
  including the check rule.

## Ice

- A hidden ice map: a few deposits on the drive area. Ice sits under the
  surface, so it is never drawn as visible ground. The viewer may reveal the
  map only as a labelled "hidden truth" layer. The run log's `truth` field
  carries it.
- An ice sensor reading it with noise.
- Ground any number you can in the PROSPECT brief
  (`docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf`), with
  page. Everything else is a named parameter marked assumed. Read the source
  itself; do not trust a remembered figure.
- The deposits and routes have to make the story true: in run 2, B must
  really miss because of A's drift, not because of a coincidence.

## Done means

- `python run_demo.py` writes both runs to
  `C:\Monad-Hackathon-Project\data\runs\` in the run-log format, with
  first-person frames from `sim/scene.py`.
- You have read the logs and looked at frames from both runs. Run 1 ends
  confirmed and run 2 ends disputed, for the right reasons.
- Tell the viewer worker when real runs exist: mail `viewer`, following
  `ops/PROTOCOL.md`.

## What is known

- First person costs about 0.29 s per frame on the RTX 4060. Choose a frame
  interval that keeps a run's render time reasonable, and say what you
  chose.
- The scene's camera, lamp and depth conventions are in `docs/handoffs.md`
  and in `sim/scene.py`, which you wrote.
- The terrain worker is changing display assets only (branch
  `step-2-terrain-display`). The `height` and `craters` contract is
  unchanged.

## Hard limits

- Write `run_demo.py`, `sim/rover.py`, and new `sim/*.py` files for ice and
  stand-ins. Do not edit `sim/terrain.py`, `web/`, or a real `sim/chain.py`
  or `sim/nav.py` if one appears. Small fixes to `sim/scene.py` are yours,
  since you own it.
- Changing the run-log format means mailing the lead with the reason; the
  lead updates `docs/handoffs.md`.
- Work in your own git worktree on branch `step-4-spine`, cut from
  `origin/joseph`. Commit and push often. Do not merge.
- No commit message names a person.

## Mail and report

Report to `leader` by mail with the headings in
`C:\Monad-Hackathon-Project\ops\PROTOCOL.md`, plus the run folders and the
frames you looked at.
