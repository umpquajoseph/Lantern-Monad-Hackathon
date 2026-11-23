# Mailboxes: how the lead and worker sessions talk

Every message is a markdown file. Mailboxes live at one absolute path that
every worktree shares, and they are git-ignored:

    C:\Monad-Hackathon-Project\ops\mailboxes\<handle>\inbox\
    C:\Monad-Hackathon-Project\ops\mailboxes\<handle>\archive\

Handles: `leader` (the lead session), `joseph`, and from 20 Nov the workers
`nav` (`docs/briefs/nav.md`) and `sensors` (`docs/briefs/sensors.md`). Every worker of 17 Nov
(`workflow`, `control`, `ground`) is retired. A new worker gets a new
handle, a mailbox and a brief in `docs/briefs/`.

## Worker folders

Each worker's checkout lives inside this repo, never beside it on `C:\`:

    C:\Monad-Hackathon-Project\.claude\worktrees\<handle>\

`.claude/` is git-ignored. The leader makes it before the worker's first
session: `git worktree add .claude/worktrees/<handle> <branch>`. Data and
mailboxes stay in the main checkout at their absolute paths, so a worker
needs no copy of either.

The leader removes a worker's folder when its branch is merged or the
worker is retired. Before removing, check it holds nothing unsaved: no
uncommitted or untracked files, nothing unpushed, no link or junction
anywhere inside (a delete follows a link into the real folder; `rmdir` the
link first). Then `git worktree remove .claude/worktrees/<handle>`. The
branch stays. Never `rm -rf` a worker folder.

## First thing every session

Read every file in your inbox, then the brief it names. Mail in an inbox is
unread. When you have handled one, set `status: done` and move it to
`archive\`.

## Sending mail

One file: `mailboxes\<to>\inbox\<YYYYMMDD-HHMMSS>_<from>_<kind>.md`

    ---
    to: leader
    from: terrain
    kind: report
    summary: One sentence. This is the line everyone reads.
    status: delivered
    re: docs/briefs/terrain.md
    branch: step-2-terrain
    ts: 20261115-110000
    ---

`kind` is one of `handoff`, `question`, `reply`, `report`, `fyi`. Keep every
key. No doorbell this build: the recipient's inbox watcher picks the file up.

## Questions

All your questions go in one `question` mail to `leader`. Then arm the
watcher and stop. Joseph may answer you directly in your session; that
counts.

## Waiting

Nothing wakes a session when a file lands. After each inbox read, run this
with `run_in_background: true` (put your handle in), then end your turn:

    timeout 43200 bash -c 'd="C:/Monad-Hackathon-Project/ops/mailboxes/<handle>/inbox"; seen=$(ls "$d"); while true; do new=$(comm -13 <(echo "$seen") <(ls "$d") | grep -v "^$"); if [ -n "$new" ]; then echo "mail: $new"; exit 0; fi; sleep 5; done'

It fires once. Re-arm it after handling each mail.

## Reports

A `report` mail with these headings, a few lines each:

- **What was asked**: the slice, and the brief it came from.
- **What I did or changed**: files and line numbers.
- **What I checked it against, and what I saw**: the comparator, then the
  output you actually looked at. A passing exit code is not a look.
- **What I am blind to**: every number you recorded but did not judge, and
  every part you did not exercise.
- **Where I stopped**: what is done, what is half done, the next action.
- **What I changed from the brief, and why.**

## Workers never

- Call the `Agent` tool, for anything. The tree is one level deep.
- Run `git reset --hard`, `git checkout -- .`, `git clean`, `rm -rf`, or `rm`
  with a glob. Delete one file at a time by literal path.
- Merge into `joseph` or `main`. Push your own branch only.
