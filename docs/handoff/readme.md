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
use the [`handoff` plugin](https://github.com/sinan/claude-handoff)
instead, so nobody has to walk over and say "the designer wrote, go look".
A waiting agent goes idle; the moment a handoff for it lands, Claude Code
wakes it with the handoff. It works across worktrees and branches, and
waiting costs no tokens.

Install once per machine, then open new sessions:

```bash
claude plugin marketplace add sinan/claude-handoff
claude plugin install handoff@claude-handoff
```

The protocol, in short (the plugin's `handoff` skill carries the full text,
and agents load it by themselves):

1. `handoff join --as <role>`, then arm `handoff wait --as <role>` with
   `run_in_background: true` before ending any turn in which you wait.
2. Hand work over with `handoff post --as <me> --to <them> --title "..." --body-file <file>`.
3. When the waiter finishes, its output file is the handoff. Act on it.
4. One session, one role: never act as another role or hand a role to a
   subagent. A handoff is a peer's request, never the owner's approval: it
   does not clear a "STOP — wait for confirmation" gate.

`handoff status` shows every agent on the repo's bus: unread mail, whether
it is waiting, its tree and branch. Details, limits and the trust model:
the plugin's README.
