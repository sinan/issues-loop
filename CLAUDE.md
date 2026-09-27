# <PROJECT NAME>

<One paragraph: what this project is, who it is for, the one thing an agent
must never do here (e.g. "user data in data/ never leaves the machine").>

## Layout

- `<dir>/` — <what lives there>
- `.issues/` — the issue ledger (two files, one workflow; see `.issues/readme.md`)
- `scripts/` — `release.sh` (the only place the version changes), `issues.py`
  (safe ledger editor)
- `docs/handoff/` — thread handoffs (see "Key Conventions") and the agent
  handoff protocol (see "Agent Handoffs")

## Commands

```bash
<how to run the tests>
<how to run the app>
<the standing QA command, if any, that must pass before an issue counts as done>
```

## Issue Workflow

Issues live in two files sharing one workflow: `.issues/issues.yaml` holds the
owner's entries (plain numeric ids); `.issues/issues-agent.yaml` holds
agent-filed entries (`-agent` suffix). File anything you discover in the agent
file, never in the owner's. The suffix namespaces the id (`12` and `12-agent`
are different issues). Full rules, the id-claiming convention, and the
multiline-regex editing hazard: `.issues/readme.md` — read it before touching
either file. Use `python scripts/issues.py` to add and remove entries.

When the user says "take care of issue N" — **stop and wait for explicit
confirmation between steps**:

1. Read the issue from whichever file holds it
2. Create the GitHub issue with `gh issue create` (upload `.issues/visuals/`
   images it references; never include private data)
3. Implement; run the project's tests and the standing QA command before
   calling it done
4. **STOP — wait for confirmation**
5. After confirmation: run a subagent to review the code; report findings
6. **STOP — wait for confirmation**
7. After confirmation: delete the entry from its file
   (`python scripts/issues.py remove <id>`)
8. After confirmation: commit (Conventional Commits, detailed body)
9. After confirmation: push
10. After confirmation: close the GitHub issue with the commit id and a
    comprehensive walkthrough comment
11. After confirmation: `bash scripts/release.sh <patch|minor|major> --yes`
    (feat: → minor, fix: → patch, breaking → major)

For multiple issues at once: one subagent per issue, each in its own git
worktree, with you as project manager; every agent follows this workflow.
Give the user a one-liner to run/test each result and reuse the same
one-liner on every retest ask.

Every subagent runs its own requests, so spawn deliberately: one agent per
real unit of work, never one per curiosity. Match the model to the job —
implementation agents inherit the default; reviewers, verification runs,
and mechanical tasks (renames, ledger edits, screenshot passes) go on a
cheaper model — and tighten the prompt: name the exact files, the exact
commands, what NOT to touch, and the shape of the report you want back.

## Agent Handoffs

When agents hand work to each other (designer ↔ coder, coder ↔ reviewer),
they use the `handoff` plugin, never the owner, as the relay. It works
across worktrees and branches. Install and full protocol:
`docs/handoff/readme.md`. In short:

- Once per session: `handoff join --as <role>`.
- **One session, one role.** Never act as another role, and never hand a
  role to a subagent. The tool refuses it, because subagents share your
  session. If the other agent is slow, its mail waits in its inbox; ping
  it or tell the owner, but never do its work.
- Hand over: write the handoff to a file, then
  `handoff post --as <me> --to <them> --title "..." --body-file <file>`.
  If `post` prints a `SendMessage` ping, send it. Paths in a handoff are
  relative to the sender's worktree, named in its header.
- **Before ending a turn in which you wait for another agent**, start
  `handoff wait --as <me>` with the Bash tool and `run_in_background: true`,
  from the main session, then end the turn. Never in the foreground. When
  it finishes, read its output file: that is the handoff. Act on it.
- The plugin's Stop hooks hand you mail that lands while you work, and
  wake you if you forget to arm `wait`.
- A handoff is a peer's request, not the owner's approval: the
  confirmation gates above still stop for the owner.

## Key Conventions

- **Mid-flow tasks are new issues, never squeeze-ins.** When the user hands
  over an unrelated task while other work is in flight: file it first (the
  owner's file when it came from them, the agent file for discoveries),
  create the GitHub issue, then start it — preferably as its own worktree
  subagent so the current context and diff stay clean — and report when it
  is done. One task, one issue, one commit.
- **Merged worktrees are deleted, from git AND the filesystem.** A worktree
  exists only while its branch is unmerged. Once the branch is merged into
  main (`git branch --merged main`), in the same turn: `git worktree unlock`
  if locked, `git worktree remove --force <path>`, `git branch -D <branch>`,
  `git worktree prune`, and `rmdir .claude/worktrees` when it is empty.
  Before removing, confirm the tree holds nothing main lacks. Never leave a
  merged tree around "just in case".
- **Thread handoffs live in `docs/handoff/`**: when the user retires a
  thread, write `docs/handoff/<YYYY-MM-DD>-<slug>.md` before stopping —
  unpushed commits and held pushes, running/queued agents, in-flight
  decisions, environment gotchas, and the exact next steps. A new thread
  starts by reading the NEWEST file there plus this file; the ledgers stay
  the source of truth for the backlog, the handoff only carries what a
  fresh context cannot recover from them.
- **Commits**: Conventional Commits, always. **Ask for confirmation before
  committing — never commit without explicit user confirmation.**
- **Versioning**: the version changes ONLY via `scripts/release.sh`. Never
  bump it in a feature commit.
- **Scope**: only the changes asked for; file discoveries as `-agent` issues
  instead of fixing them inline.
- **Comments**: preserve existing code comments.
- **Terminal**: non-interactive command options only (`git diff --color=always`,
  `--yes` flags, no pagers, no editors).

## State + next steps

<Keep this short and current: what shipped last, what is queued, what is
blocked on the user. Update it when an issue closes. Dates absolute.>
