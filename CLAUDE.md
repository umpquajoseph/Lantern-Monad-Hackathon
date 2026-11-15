# Lantern: Monad Blitz Oregon, 15 Nov 2026

A shared map of lunar ice that rovers write themselves. Two rovers from two
agencies drive a dark crater, find their position from lamp-lit crater rims
(after JPL's ShadowNav), post ice finds with their position uncertainty to a
Monad contract, and check each other's finds.

- `docs/spec.html` is the source of truth for scope: goal, demo, the six steps.
- `docs/handoffs.md` is the source of truth for the data passed between steps.
  Read it before writing any function another step calls.

## Event rules

- Build window about seven hours. Demo in the evening, top six pitch.
- Judging criteria, as given by the organizers:
  - Investability.
  - Creativity: "how original is the idea? an original take on a real
    problem scores highest".
  - Completeness: "clean ui/ux and working features, end to end, no gaps".
  - Presentation.
- So: a demo that runs end to end with a clean dashboard beats an extra
  feature. Polish step 5 before any upgrade (real terrain, vision).

## Layout (planned, one file per step)

| Step | Path | Part |
|---|---|---|
| 1 | `contracts/` (Foundry), `sim/chain.py` | Contract and Python client |
| 2 | `sim/terrain.py` | Height grid and crater list |
| 3 | `sim/nav.py` | Particle filter |
| 4 | `sim/rover.py` | Two rovers, planner, ice sensor |
| 5 | `sim/dashboard.py` | Judges' window, backup video |
| 6 | `sim/vision.py` | Raycast render, lamp shadows, Canny rims |
| | `run_demo.py` | Runs both demo runs end to end |

## Hard rules

- **No secrets in the repo.** Private keys, seed phrases and RPC keys live in
  `.env`, which is git-ignored. `.env.example` lists the names only. Throwaway
  testnet keys only.
- **`main` takes merges only when Joseph says so.** Each step lives on its own
  branch, `step-N-name`. Commit and push branches early and often: a laptop that
  dies takes unpushed work with it.
- **Assert only what is grounded.** A number taken from ShadowNav or the
  PROSPECT brief carries its source in a comment (section, table or page).
  Anything else is a named parameter with a default, marked as assumed.
- **Never trust a remembered summary of a source.** Check claims against the
  paper itself (https://arxiv.org/abs/2301.04630), not against memory or a
  summary, including the spec.
- **Stay inside your step's hand-off.** Changing a format in
  `docs/handoffs.md` means telling the others first and updating the file in
  the same commit.

## Verifying

Run the feature and look at what it produces before saying it works: the plot,
the camera frame, the transaction on the block explorer. An exit code is not a
look. The contract gets Foundry tests for its checking rules (step 1). No other
test suites today.

## Working agreement

One step per session. If time runs short, demo the last step that runs. The
demo must work without vision (step 6), on stand-in rim points.
