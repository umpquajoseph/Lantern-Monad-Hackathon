# Rules for every campaign-live worker (16 Nov 2026)

Read first: `docs/campaign-spec.md` section 1 (the agreed intent; it
overrides the rest of the spec), `docs/handoffs.md` "Campaign simulator",
`CLAUDE.md`, `sim/settings.py`. Sources are in `docs/sources/`; check any
ShadowNav number against `docs/sources/ShadowNav-Cauligi-2023.txt`, never
memory.

The product owner's words, which no spec overrides:

- "all runs should use camera vision for pathfinding"
- "frames do not need to be saved to disk"
- "lets just start with 3 runs, which each have a few rovers and a few sorties"
- Lantern expands, it does not replace: everything the hackathon site
  showed stays (Mission Control, first person, lidar, satellite map,
  particles); only the chain, Monad and agency wording go.
- No faking to look right. Sensor frames are rendered live in the sim loop;
  nothing is "simulated first, rendered later". Anything made up is labelled.

Working rules:

- Work only in your own worktree and branch. Commit and push often. Never
  merge to `main`, never touch another worker's branch. The leader merges.
- Do not start subagents or other sessions.
- Never broad-revert (`git checkout -- .`, `git reset --hard`). Stash.
- Commit messages describe the code. They never name a person or "the user".
  End each with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  Multi-line messages go through `git commit -F <file>`.
- Constants: source in a comment (paper, section/table/page) or a named
  setting marked `assumed`.
- A change to the run record goes into `docs/handoffs.md` in the same
  commit.
- Data: code falls back to `C:\Monad-Hackathon-Project\data` (see
  `sim/terrain.py:43`). Write new run output under that folder's
  `campaigns/`, in a batch folder named for your part.
- Technical choices are yours. Decide, build, look, push. If you hit a
  question of scope (what the product is), take the reading closest to the
  quotes above, keep going, and put the question in your final report.
- Done means you ran it and looked at the real output (the record, the
  printed numbers, the page in a browser screenshot you read back,
  downscaled). An exit code is not a look. Write no new test suites.
- Your final message is your report to the leader: the first 50 words say
  what works now, what doesn't, and any question. Then the branch, commits,
  commands to reproduce, and the numbers you measured. About 200 words of
  prose; tables and numbers are free.
