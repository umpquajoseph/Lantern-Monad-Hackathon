# Lantern: a simulation of the paper's rover workflow (RASC-AL 2027)

Lantern simulates the workflow the team's paper proposes: rovers map,
discover and corroborate resources. The first mission is PROSPECT: one
dock, 1 to 5 identical Scouts, sorties into a shadowed crater, every Scout
navigating by its live camera. It produces the paper's numbers and
figures, and Mission Control replays any run.

## Start here

1. `ops/HANDOFF.md`: where things stand and what is next.
2. `docs/campaign-spec.md`: scope and intent. It wins over anything else.
3. Your brief in `docs/briefs/`, if you are a worker.
4. `ops/PROTOCOL.md`: how sessions talk through the mailboxes.

Never take scope, tasks or formats from git history or old mail.

## Reference

- `docs/handoffs.md`: the run record and the interfaces between parts.
  Read it before writing a function another part calls.
- `sim/settings.py`: every constant, with its source or `assumed`.
- `docs/terrain/position.md`: the approved terrain position.
- `docs/SOURCES.md` and `docs/sources/`: every source, with copies. The
  team's area 3 draft is `docs/sources/area3-navigation-draft.pdf`, with a
  text copy beside it.

## Rules

- **Realism, not outcomes.** A change is right when it makes the sim more
  faithful to the paper's workflow or to the Moon. It is wrong when its
  purpose is a better result.
- **Ground every number.** A number from a source cites it in a comment
  (section, table or page). Anything else is a named parameter marked
  `assumed`. Check claims against the source itself, never a summary.
- **Comments say what the code does and where a number comes from.** No
  history, dates or discussion; that goes in the commit message.
- **`main` takes merges only when Joseph says so.** Each worker has its own
  branch. Commit and push early.
- **Worker folders live in `.claude\worktrees\<handle>`**, never beside the
  repo. See `ops/PROTOCOL.md`.
- **Formats change only with notice.** Changing `docs/handoffs.md` means
  telling the others and updating it in the same commit.
- **No secrets in the repo.** Keys live in `.env` (git-ignored).
- **Hosting is local only** until Joseph says it is right. Never republish
  lantern-teal.vercel.app (the hackathon record).

## Verifying

Run the feature and look at what it produces: the plot, the camera frame,
the run playing in Mission Control. An exit code is not a look. No test
suites today.
