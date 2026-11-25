# Mailboxes: how the leader and worker sessions talk

Every message is a markdown file. Mailboxes live at one absolute path that
every worktree shares, git-ignored:

    C:\Monad-Hackathon-Project\ops\mailboxes\<handle>\inbox\
    C:\Monad-Hackathon-Project\ops\mailboxes\<handle>\archive\

Handles: `leader`, `joseph`, and one per worker. A new worker gets a
handle, a mailbox and a brief in `docs/briefs/`.

## Worker folders

Each worker's checkout lives at
`C:\Monad-Hackathon-Project\.claude\worktrees\<handle>\` (git-ignored). The
leader makes it: `git worktree add .claude/worktrees/<handle> <branch>`.
Data and mailboxes stay in the main checkout.

The leader removes a worker's folder when its branch is merged or the
worker is retired. First check it holds nothing unsaved (no uncommitted,
untracked or unpushed work) and no link or junction inside (`rmdir` a link
first: a delete follows it into the real folder). Then
`git worktree remove .claude/worktrees/<handle>`. Never `rm -rf` one.

## Every session

Read every file in your inbox, then the brief it names. When you have
handled a mail, set `status: done` and move it to `archive\`.

## Sending mail

One file: `mailboxes\<to>\inbox\<YYYYMMDD-HHMMSS>_<from>_<kind>.md`

    ---
    to: leader
    from: nav
    kind: report
    summary: One sentence. This is the line everyone reads.
    status: delivered
    re: docs/briefs/nav.md
    branch: lantern-nav
    ts: 20261124-221500
    ---

`kind` is one of `handoff`, `question`, `reply`, `report`, `fyi`. Keep every
key. All your questions go in one `question` mail to `leader`; Joseph may
answer in your session instead.

## Waiting

Nothing wakes a session when a file lands. After each inbox read, run this
with `run_in_background: true` (your handle in it), then end your turn. It
fires once; re-arm it after each mail.

    timeout 43200 bash -c 'd="C:/Monad-Hackathon-Project/ops/mailboxes/<handle>/inbox"; seen=$(ls "$d"); while true; do new=$(comm -13 <(echo "$seen") <(ls "$d") | grep -v "^$"); if [ -n "$new" ]; then echo "mail: $new"; exit 0; fi; sleep 5; done'

## Reports

A `report` mail with these headings, a few lines each: **What was asked**,
**What I changed** (files), **What I checked it against, and what I saw**
(the output you looked at, not an exit code), **What I am blind to**,
**Where I stopped**, **What I changed from the brief, and why**.

## Workers never

- Call the `Agent` tool.
- Run `git reset --hard`, `git checkout -- .`, `git clean`, `rm -rf`, or `rm`
  with a glob. Delete one file at a time by literal path.
- Merge into `joseph` or `main`. Push your own branch only.
