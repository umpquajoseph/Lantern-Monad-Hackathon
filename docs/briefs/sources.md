# Brief: sources and what they did for Lantern (handle `sources`)

Read `CLAUDE.md`, `ops/PROTOCOL.md` and this file first.

## What it is for

Asked at 15:4x: "compile all the sources and what they did for the project
into an md so i can send it to a teammate who is working on the pitch slides".

The reader builds the pitch deck. They need to say what Lantern stands on: which
papers, datasets, models and tools, and what each one gave the project. Write it
for a smart non-specialist. Every line must be grounded in the repo.

## The deliverable

One file, `docs/SOURCES.md`, about 1-2 pages. Committed and pushed on branch
`docs-sources` from `origin/joseph`. Never push to `main`, `joseph` or
`integration`; the leader merges it.

Shape:
1. **One-paragraph summary**: what Lantern is, and that its navigation,
   terrain, ice numbers and chain are built on the sources below.
2. **Sources table**, one row per source:
   source (author, year, title, link) | what it is | what it did for Lantern
   (concrete: which constant, method or asset, with `file:line`) | slide hook
   (one short line the teammate can put on a slide).
3. **Grouped**: Navigation (ShadowNav papers), Terrain and lighting (LOLA,
   Hapke), Ice and mission (PROSPECT brief, any ice or neutron sources),
   Rover models (NASA 3D Resources), Chain (Monad docs, Foundry, Sourcify,
   Monadscan), Software (Three.js, VTK, NumPy/SciPy, OpenCV, web3.py; list only
   what the code imports).
4. **What is ours vs borrowed**: a short list of what the team built on top
   (particle filter on lamp-lit rims, two-agency find-and-check contract,
   mission sim, Mission Control site), so the deck credits sources without
   underselling the build.
5. **Assumed, not sourced**: the main named parameters marked "assumed" in the
   code, so the pitch never presents them as measured.

## Where to look

- `docs/sources/`: the PDFs and text copies (ShadowNav Cauligi 2023, Atha 2024,
  PROSPECT Team Brief RASC-AL 2027 Theme 3-1, Xu 2020 Hapke CE-1 IIM).
- `README.md` "Sources".
- Citations in code: `git grep -n -iE "arxiv|Cauligi|Atha|PROSPECT|Hapke|LOLA|PGDA|NASA|Sourcify|assumed"`
  over `sim/`, `run_mission.py`, `run_demo.py`, `web/js/`, `contracts/src/`.
- `sim/lantern_deployment.json`: the contract address, chain and verification.
- `docs/spec.html`, `docs/ARCHITECTURE.md`, `docs/science.md` for context.

## Rules

- Assert only what is grounded. Each "what it did" cites `file:line` or a doc.
  Check a paper claim against the PDF or text in `docs/sources/`, never memory.
  Say UNVERIFIED for anything you could not check.
- Short sentences, plain words. Bold at most one phrase per section.
- No commit message names a person.
- Read only, apart from the one new file. No renders, no servers. RAM is short.

## Done

Mail the leader (`ops/PROTOCOL.md` format) with the commit hash, the row count
and anything UNVERIFIED. Then stop.

## Talking to others

Your handle is `sources`. Your inbox is `ops/mailboxes/sources/inbox/`, in
`C:\Monad-Hackathon-Project`. Mailboxes are git-ignored; write files there
directly. Mail `ts` comes from `date`. Give the leader your doorbell address in
your first mail.

| handle | doorbell address |
|---|---|
| leader | `Hackathon project setup review [5247c4]` |
