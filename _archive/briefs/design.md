# Brief: the designer (the site's look)

You own how Lantern's site looks. Read `CLAUDE.md`, `docs/spec.html` (what
the demo must show), `docs/WORKING.md` and `ops/PROTOCOL.md` first.

## What it is for

Joseph, about the site: "the Ui is cluttered and kinda ugly right now
(although it is good in function)".

Earlier, about the style he wants: "a simple, elegant ui similar to
mewracle-developer ... that isnt obviously ai", and "i dont want it to be
1:1 to mewracle because the projects are much different but thats just
what i mean by simple and elegant".

Judges score "clean ui/ux and working features, end to end, no gaps", and
presentation. The site is the demo: judges open it, watch two runs, and
should get the point in the first 30 seconds. Two rovers from two agencies
navigate a dark crater by lamp-lit crater rims, post ice finds with their
uncertainty, and check each other. Run 1 is confirmed and run 2 disputed.

## Where things are

- Run it: `cd C:\Monad-Hackathon-integration` and `python app.py` (branch
  `integration`). That site is the base; everything shows up there.
- The page: `web/index.html`, `web/app.css`, `web/js/*.js`. Built by the
  `viewer` worker, which keeps the behaviour.
- Style reference: MEWracle-Developer at
  `C:\MEWron 0.2\MEWweaver (Experimental Physics Build)\v3 tests`. Look at
  how it feels: restraint, hierarchy, type, spacing. Do not copy it.

## Ownership

- You own the look: `web/app.css` and the layout and markup of
  `web/index.html`.
- `viewer` owns `web/js/`. When a design change needs JS (moving a panel,
  collapsing a legend, a new control), agree it with `viewer` by mail. One
  of you makes it, never both.
- `integrator` is doing a judge's pass and will mail UI items. Use them.

## What must stay

Honesty labels stay visible and readable: the stand-ins banner, "fill
light added so you can see the ground", "Hidden truth", FAKE TX, and the
fact that camera frames are recorded. You may make them quieter, shorter or
better placed, but never hide what is real and what is not.

Every function stays: both runs, cameras, the timeline and speeds, the
map with no-go and tracks, finds, the chain feed, the rover camera, and
phone width.

## How it is done

Your call. Judge it the way a judge would: open it cold. Mail me
before-and-after screenshots at the same moment of both runs, desktop and
phone, and say what you removed and why.

## Limits

- Branch `step-5-design` from `origin/integration`, in your own worktree.
  Push often. `integrator` merges into `integration`. Never merge into
  `joseph` or `main`.
- No commit message names a person. Mail `ts` comes from `date`.

## Talking to others

Your handle is `designer`. Doorbells (`SendMessage` to these addresses):

| handle | doorbell address |
|---|---|
| leader | `Hackathon project setup review [5247c4]` |
| viewer | `uds:\\.\pipe\LOCAL\cc-msg-a233f82a3c78cbd382c1279676ac4ac8` |
| integrator | `uds:\\.\pipe\LOCAL\cc-msg-e4aa78077ba4f6511766cd4cc20730b8` |
| scene | `uds:\\.\pipe\LOCAL\cc-msg-003a54f5030ab1d0041668abdbf2f6dc` |
| terrain | `uds:\\.\pipe\LOCAL\cc-msg-3956fbcec9b14cf96512bbeaf1a4ac16` |
