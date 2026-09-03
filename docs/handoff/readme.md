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
