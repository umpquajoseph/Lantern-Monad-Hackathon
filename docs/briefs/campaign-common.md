# Campaign build: what every part shares

Read first: `docs/campaign-spec.md` (scope), `docs/handoffs.md` section
"Campaign simulator" (record and interfaces), `sim/settings.py` (constants),
`ops/PROTOCOL.md` (mail), `CLAUDE.md` (hard rules).

**Why this exists.** Lantern now produces the numbers in section 4.6 and
Figure 1 of the team's RASC-AL 2027 paper. In Joseph's words: "Lantern can
and should produce numbers its just a question of if they are trustworthy or
not." Spec section 4a says what makes a number trustworthy. Build toward that,
not toward a number that looks right.

**Sources.** ShadowNav: `docs/sources/ShadowNav-Cauligi-2023.pdf` (C23, the
paper `CLAUDE.md` names) and `ShadowNav-Atha-2024.pdf` (A24). The brief:
`docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf`. The team's
area 3 draft: `C:\Life\research\rasc-al\drafts\area3-navigation-draft.html`.
It is unpublished: read it, cite it as `draft N.N`, never copy it into the
repo. Check every number against the source itself, not against the spec or
a summary.

**Rules.**

- Your own worktree and branch, made from `campaign-handoffs` (which holds the
  shared files on top of `main`). Commit and push your branch often. Never
  merge into `main`.
- A new constant is a new field in `sim/settings.py` with its source or
  `assumed`. Say so in your report.
- A change to the record format or an interface: mail the leader first, then
  edit `docs/handoffs.md` in the same commit as the code.
- No test suites. Look at the real output: a record, a plot, the page.
- Hosting is off. Everything runs locally (`python app.py` serves the site).
  Never run `web/publish.py`.
- No commit message names a person.
- No Agent tool, no subagents.

**Deadlines.** Batch results and Figure 1: 15 Jan 2027. Site and video:
10 Feb 2027. Work at full pace now; the earlier it runs end to end, the more
time for the trust checks.

**Act, then report.** Decide the method yourself, build, look at the output,
push, then send one `report` mail (PROTOCOL.md headings) and ring the
doorbell. Ask the leader only about scope, the story, or anything that
changes another part's interface.
