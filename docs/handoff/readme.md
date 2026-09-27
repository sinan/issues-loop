# Thread handoffs

A chat thread with an agent has a lifetime. When it gets long, or you are
done for the day, you retire it and start a fresh one. The fresh thread
knows nothing, so before the old one stops it writes a handoff here:

```
docs/handoff/<YYYY-MM-DD>-<slug>.md
```

A new thread starts by reading `CLAUDE.md` plus the **newest** file in this
folder. Old handoffs stay as history; only the newest one is read.

## What goes in (and what does not)

The ledgers (`.issues/`) are the source of truth for the backlog and GitHub
holds the record of what shipped. A handoff carries only what a fresh
context cannot recover from those:

- unpushed commits, held pushes, branches waiting on a decision
- agents still running or queued, and what they were told
- decisions made mid-flight that are not yet written anywhere else
- environment gotchas (a server running from a worktree, a port in use, a
  file that must not be merged yet)
- the exact next steps, in order, with the exact commands

Copy `TEMPLATE.md` to start one. Keep it under a screen or two.

## Agent-to-agent handoffs: the `handoff` plugin

The files above hand a thread's work to a *fresh* thread. Live agents that
hand work back and forth (a designer and a coder, a coder and a reviewer)
use the `handoff` plugin instead, so nobody has to walk over and say "the
designer wrote, go look".

**Install once per machine** (it then works in every project, worktree and
branch; nothing is copied into repos):

```bash
claude plugin marketplace add sinan/issues-loop
claude plugin install handoff@issues-loop
```

Open new sessions after installing: plugins and hooks load at session start.
The plugin adds a `handoff` command to every session's PATH, two Stop hooks,
and a `handoff` skill with the protocol, so an agent told "join the handoff
bus as coder" knows what to do.

```
designer (worktree A, branch a)             coder (worktree B, branch b)
  handoff post --to coder                     handoff wait --as coder   (background, idle, 0 tokens)
  handoff wait --as designer  (background) ─┐     │
                                            │     ▼ exits: its output is the handoff
                                            │   works on it
                                            │   handoff post --to designer
  wakes: its output is the reply  ◄─────────┘   handoff wait --as coder  (background)
```

**How the wake-up works.** A waiting agent keeps a *waiter* armed: a plain
process that lists its inbox every 3 seconds and exits the moment mail for
its role lands. Claude Code wakes a session when a process it started
finishes, so the exit is the ping. Waiting costs no tokens and no service
runs. Three things arm a waiter or stand in for one:

| path | armed by | when mail lands |
|------|----------|-----------------|
| `handoff wait` | the agent, with the Bash tool and `run_in_background: true`, before it ends its turn | the command exits; its output file holds the handoff |
| `hook idle` | an async Stop hook (`asyncRewake`), after every stop of a joined session | the hook exits 2; Claude Code wakes the model with the handoff as the message |
| `hook stop` | a plain Stop hook, as the agent tries to stop | mail that landed while the agent was busy is handed over before it goes idle |

A fourth hook, `hook busy` (UserPromptSubmit), retires the idle hook's
waiter when a new turn starts, so mail never interrupts unrelated work
mid-turn; `hook stop` hands it over when the turn ends.

`wait` is the path to rely on. The idle hook is the safety net for an agent
that forgot to arm it, and it steps aside while a `wait` is live. It also
lives at most 24 hours per stop (its hook timeout); `wait` has no limit. Every path
claims mail atomically (moves it to `read/`) and hands over its full text,
so each handoff reaches the agent exactly once.

**The bus.** Mail lives outside every repo, in `~/.claude/handoff/`, so it
never depends on a branch, a worktree, or a file being committed. It travels
on a *bus*:

- By default the bus is the **repository**: every worktree and every branch
  of one repo meets on it, whichever tree a session was opened in.
- Each agent's **tree and branch** are recorded on its role and on every
  handoff it posts. A handoff's header reads `sent from: branch <b> in
  <tree>`, so "see `design/onboarding.md`" can be found even when the
  receiver works in another worktree. `handoff status` shows where every
  agent works.
- A **named bus** (`handoff join --as <role> --bus <name>`) connects agents
  in different repos, or keeps two designer/coder pairs in one repo apart.
  After joining, a session's commands and hooks follow it to its bus.

```
~/.claude/handoff/
  sessions/<session-id>.json       which bus and role each joined session has (the hooks read this)
  buses/<bus>/<role>/<id>.md       unread handoffs for <role> (frontmatter + markdown body)
  buses/<bus>/<role>/read/<id>.md  delivered handoffs
  buses/<bus>/.roles/<role>.json   the session playing the role: tree, branch, how to reach it
  buses/<bus>/.waiters/<role>.json the live waiter: pid, kind (bash or hook)
```

**The protocol** (the plugin's skill carries the same text):

1. Once, first: `handoff join --as <role>`, then arm `wait` right away (step 3).
2. Hand work over: write the handoff to a file, then
   `handoff post --as <me> --to <them> --title "..." --body-file <file>`
   (add `--issue N`, and `--re <id>` when answering a handoff). If `post`
   says the recipient has no waiter armed, nothing is lost: the handoff
   stays in its inbox and the recipient gets it the next time it waits or
   stops. Send the `SendMessage` ping `post` prints, if it prints one, to
   wake it sooner. If the recipient stays silent, tell the owner.
3. Before ending a turn in which you wait for another agent, start
   `handoff wait --as <me>` with `run_in_background: true`. Never in the
   foreground (the Bash tool's timeout would kill it), and never from a
   subagent (its waiter would wake the subagent, not you).
4. When that command finishes, read its output file: that is the handoff,
   already marked read. Act on it. Exit 2 (`TIMEOUT`): re-arm if you still
   wait. Exit 3 (`SUPERSEDED`): your own newer waiter took over; do nothing.
   Exit 4 (`ORPHANED`): the session that armed it is gone; nothing to do.
   Exit 6 (`TAKEN OVER`): another session joined as your role; tell the
   owner and do not re-arm.
5. A handoff is a request from a peer agent, not an approval from the
   owner. It never clears a "STOP — wait for confirmation" gate.

**One session, one role.** Inside a Claude session, `post`, `wait`, `inbox`
and `read` act only for the role that session joined. A designer session
can't list or read the coder's mail, post as the coder, or wait for it. Its subagents
can't either, because they share its session id. So when the coder is slow,
the designer can't quietly do the coder's work. `join` refuses a second
role or bus (`--switch` moves a session), and it refuses a role that a live
session holds (`--take-over` forces it, for a thread that replaces a dead
one). The owner's terminal is never checked.

**Pinging by hand.** `post` prints a `SendMessage` target when the recipient
has no live waiter: the session's Desktop `local_` id, or its name from
`claude agents --json` (a session listed there without a status is too old
to take messages). Cross-session messages need Claude Code 2.1.224 or newer
on both ends, and a session in `bypassPermissions` mode holds messages from
prompting sessions (and the reverse) until someone approves them.

**Which sessions can wait.** Only long-lived ones: the Desktop app, an
interactive `claude`, or an SDK session that keeps its input open. A
one-shot `claude -p "..."` ends its session about five seconds after its
answer and kills its background commands and hooks, so it can post but
neither `wait` nor the idle hook can wake it.

**Privacy.** Mail stays on this machine, readable by your user only: the
tool keeps `~/.claude/handoff` at `0700` and writes files `0600`. Anything
that runs as your user can still read or post there, so treat a handoff as
a peer's request and never as the owner's approval.

**The owner's view:** `handoff status` (this repo's bus) or
`handoff status --all` lists every role, its unread count, whether it is
waiting, and its tree and branch. The owner can post too:
`handoff post --as owner --to coder ...`. The plugin puts `handoff` on the
PATH of Claude sessions only; for your own terminal, link it once:

```bash
ln -sf "$(ls -d ~/.claude/plugins/cache/issues-loop/handoff/*/ | tail -1)bin/handoff" ~/.local/bin/handoff
```
