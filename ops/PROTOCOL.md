# Mailboxes: how the lead and worker sessions talk

Every message is a markdown file. Mailboxes live at one absolute path that
every worktree shares, and they are git-ignored:

    C:\Monad-Hackathon-Project\ops\mailboxes\<handle>\inbox\
    C:\Monad-Hackathon-Project\ops\mailboxes\<handle>\archive\

Handles (workflow build, from 17 Nov 2026): `leader` (the lead session),
`workflow` (simulation), `control` (Mission Control), `ground` (terrain
research), `joseph`. Every earlier
handle is closed.

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
