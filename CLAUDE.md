# Lantern: a simulation of the paper's rover workflow (RASC-AL 2027)

Lantern is the hackathon's Lantern, upgraded to simulate the workflow the
team's paper proposes: rovers map, discover and corroborate resources. The
first mission is PROSPECT: one dock, 1 to 5 identical Scouts, sorties into
a shadowed crater, every Scout navigating by its live camera. It produces
the paper's numbers and figures, and Mission Control replays any run.

## Start here, in this order

1. `ops/HANDOFF.md`: where things stand, who is doing what, what is next.
2. `docs/campaign-spec.md`: scope and intent. It wins over anything else.
3. Your own brief in `docs/briefs/`, if you are a worker.
4. `ops/PROTOCOL.md`: how sessions talk through the mailboxes.

Anything in `_archive/` is history. Never take scope, tasks or formats from
it, or from old mail.

## Reference

- `docs/handoffs.md`, "Campaign simulator": the run record and the
  interfaces between parts. Read it before writing any function another
  part calls.
- `sim/settings.py`: every constant, each with its source or `assumed`.
- `docs/terrain/position.md` (from branch `lantern-ground`): the approved
  terrain position and its sources.
- `docs/SOURCES.md` and `docs/sources/`: every source, with copies. The
  team's area 3 draft is unpublished and lives outside this repo; never
  copy it in.
- Hosting: local only until Joseph says it is right, then a new Vercel
  project. Never republish lantern-teal.vercel.app (the hackathon record).

## Hard rules

- **Realism, not outcomes.** A change is right when it makes the sim more
  faithful to the paper's workflow or to the Moon: a bug fixed, a value
  given a source. It is wrong when its purpose is a better result.
- **No secrets in the repo.** Keys live in `.env`, which is git-ignored.
  `.env.example` lists the names only.
- **`main` takes merges only when Joseph says so.** Each worker has its own
  branch. Commit and push early and often: a laptop that dies takes
  unpushed work with it.
- **Assert only what is grounded.** A number from a source carries the
  source in a comment (section, table or page). Anything else is a named
  parameter with a default, marked `assumed`.
- **Never trust a remembered summary of a source.** Check claims against
  the paper itself, not against memory or a summary, including the spec.
- **Stay inside your hand-off.** Changing a format in `docs/handoffs.md`
  means telling the others first and updating the file in the same commit.

## Verifying

Run the feature and look at what it produces before saying it works: the
plot, the camera frame, the run playing in Mission Control. An exit code is
not a look. No test suites today.

The hackathon's event rules, layout and working agreement are in
`_archive/docs/CLAUDE-hackathon.md`.
