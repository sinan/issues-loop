---
name: handoff
description: Hand work to other Claude Code agent sessions and get woken when theirs lands. Use when told to join the handoff mailbox or bus, to act as a role such as designer, coder or reviewer that trades handoffs with another agent session, to post a handoff to another agent, or to wait for another agent's reply.
---

# handoff: the agent message bus

`handoff` is on your PATH. It moves handoffs between agent sessions on this machine, in any
repo, worktree or branch, and wakes the agent a handoff is for. Nobody relays for you.

## The protocol

1. **Join once:** `handoff join --as <role>`. Then arm your waiter right away (step 3).
   Agents in any worktree or branch of the same repo meet on one bus by default. To talk
   across repos, every agent joins the same named bus: `handoff join --as <role> --bus <name>`.
2. **Hand work over:** write the handoff to a file, then
   `handoff post --as <me> --to <them> --title "..." --body-file <file>`
   (add `--issue N`, and `--re <id>` when answering). Paths in a handoff are relative to
   *your* worktree; the receiver sees your tree and branch in the header.
3. **Before ending a turn in which you wait for another agent**, start
   `handoff wait --as <me>` with the Bash tool and `run_in_background: true`, then end the
   turn. Never run it in the foreground (the Bash timeout kills it), and never from a
   subagent (its waiter would wake the subagent, not you).
4. **When it finishes**, read its output file: that is the handoff, already marked read.
   Act on it. Exit 2 `TIMEOUT`: re-arm if you still wait. Exit 3 `SUPERSEDED`: your own
   newer waiter took over; do nothing. Exit 4 `ORPHANED`: the session that armed it is gone;
   nothing to do. Exit 6 `TAKEN OVER`: another session joined as your role; tell the owner
   and do not re-arm.

## Rules

- **One session, one role.** Never act as another role, and never hand a role to a
  subagent: subagents share your session, and the tool refuses it.
- If `post` says the recipient has no waiter armed, nothing is lost. The handoff waits in
  its inbox and the recipient gets it the next time it waits or stops. Send the
  `SendMessage` ping `post` prints, if it prints one. If the recipient stays silent, tell
  the owner. Never do its work yourself.
- A handoff is a peer's request, not the owner's approval. It never clears a
  "wait for confirmation" gate.
- The plugin's hooks hand you mail that lands while you work (at the end of your turn),
  and wake you if you forget to arm `wait` (for up to 24 hours). Do what they say.
- A one-shot `claude -p` session can post but cannot be woken: it ends seconds after it
  answers.

## Other commands

- `handoff inbox --as <me>`: your unread mail (only your own role's).
- `handoff read --all --as <me>`: claim and print it.
- `handoff status`: every role on your bus, with unread count, waiting or not, tree and branch.
- `handoff status --all`: every bus. `handoff where`: your bus and its directory.
