# The issue ledger

One backlog, two files, one workflow. Everything here also applies to Claude
(or any coding agent) working in this repo, and the rules are written so the
agent can follow them verbatim.

## Two files, one ledger

Which file an entry belongs in is decided by **who filed it**, nothing else:

| file                 | owner             | id format         |
|----------------------|-------------------|-------------------|
| `issues.yaml`        | the human (you)   | plain number `12` |
| `issues-agent.yaml`  | the agent         | suffixed `12-agent` |

Agents never add entries to `issues.yaml`. Anything an agent discovers goes
into `issues-agent.yaml`. Every rule below applies to both files unchanged:
the workflow, the stop-and-confirm steps, deleting an entry once it ships.

**The `-agent` suffix namespaces the id, it does not reserve the number.**
`12` and `12-agent` are different issues and both may exist. That is
intended, and it is the whole reason the suffix exists: two threads append
concurrently, and the suffix keeps them from minting the same id. Do not
"deduplicate" such pairs, and do not renumber either side.

So **cross-references carry the exact id, suffix included**. Writing `12`
when you meant `12-agent` points a reader at a different issue. Copy the id
verbatim.

**Claiming an id**: take the highest number in the file you are filing into
plus one, *at the moment you append*, not earlier. If two threads race, the
first-landed entry keeps the id and the later one renumbers.
`python scripts/issues.py next-id [--agent]` does this for you.

Cross-references point across files freely. Never move an entry between
files to tidy up, never renumber one that has landed. If a human picks up an
agent-filed issue it stays where it is, under its existing id.

## Entry format

```yaml
- id: 12
  title: "short, single-line, quoted"
  description: >-
    a folded block scalar, indented four spaces, wrapped at 100 columns.
    say what is wrong or wanted, where it lives, and how to tell it is done.
    never a one-line string: that makes every grep and diff on this file
    unreadable.
```

Prefer `python scripts/issues.py add --title "..." --description "..."`,
which claims the id, wraps the text, appends, and asserts the entry count.

## Editing hazard (the most important rule here)

**Never edit either file with a multiline regex.** A greedy pattern with the
`/m` flag once silently deleted 29 entries in a single write. Parse into
entries at column-0 `- id:` boundaries, **assert the entry count before and
after every edit**, and write back. That assertion is the only thing that has
ever caught this. `scripts/issues.py` does exactly that; use it for add and
remove.

## Visuals

`./visuals` holds every image an issue references. When creating the GitHub
issue, upload the referenced visuals with it. Never put anything private in
an issue or a visual: both end up on GitHub.

## Lifecycle

1. An entry is filed here. The ledger is the **queue**.
2. "take care of issue N" turns it into a GitHub issue. GitHub is the
   **permanent record** (discussion, commit link, walkthrough).
3. When the work ships, the entry is **deleted** from this file. Done means
   gone: the ledger is always the backlog, never a graveyard.

The full step-by-step workflow, with its confirmation gates, lives in
`CLAUDE.md` under "Issue Workflow".

## Project-specific rules

Add rules that only apply to this project below (privacy constraints, a QA
command that must run before an issue counts as done, etc.).

- (none yet)
