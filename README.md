# issues-loop

A small, file-based way to run a project with Claude Code (or any coding
agent): a YAML issue ledger in the repo, GitHub issues as the permanent
record, a confirmation-gated workflow the agent follows step by step, and a
versioned release at the end of every issue.

It is what I use on my own projects. Copy it into a new repo and you get the
whole loop in a minute.

## The loop

```
you file an issue          .issues/issues.yaml        (agent files its own in issues-agent.yaml)
        │
        ▼
"take care of issue 12"    the agent reads the entry
        │
        ▼
gh issue create            GitHub issue = the permanent record
        │
        ▼
implement + test           ─── STOP, you confirm ───
        │
        ▼
subagent code review       ─── STOP, you confirm ───
        │
        ▼
delete ledger entry  →  commit (Conventional Commits)  →  push
        │
        ▼
close GitHub issue with the commit id + a walkthrough comment
        │
        ▼
bash scripts/release.sh minor --yes        tag + GitHub release, version bumped here and only here
```

Each arrow after "implement" waits for your explicit yes. Nothing is
committed, pushed, closed, or released without it.

## Why it works

- **The backlog lives next to the code.** The agent reads `.issues/` the
  same way it reads the source. No context-switch to a tracker, no copy-paste
  of tickets into the chat.
- **Two files, one ledger.** Your issues get plain ids (`12`); anything the
  agent discovers goes in its own file with a suffixed id (`12-agent`). Two
  threads can append at the same time and never collide, and you can always
  see what the agent thinks is worth doing without it touching your list.
- **Done means deleted.** When an issue ships, its entry is removed. The
  ledger is always exactly the backlog, never a graveyard. GitHub keeps the
  history, with the commit and a walkthrough on every closed issue.
- **Stop-and-confirm gates.** The agent halts after implementing and again
  after the review. You look at the diff while it is still cheap to say no.
- **A release per issue.** Every shipped change is a tag with notes generated
  from the commits. The version number changes only in the release script, so
  a feature commit never argues with a release commit.
- **Mid-flow tasks become issues, not squeeze-ins.** Hand the agent something
  unrelated while it is busy and it files it first, then does it in its own
  worktree. One task, one issue, one commit.
- **Thread handoffs.** When a chat thread gets long you retire it, and the
  agent writes `docs/handoff/<date>-<slug>.md` with everything a fresh thread
  cannot recover from the ledgers. The new thread starts by reading it.
- **Agents hand off to each other without you.** With the
  [`handoff` plugin](https://github.com/sinan/claude-handoff),
  a designer and a coder (or any two sessions, in any worktree or branch)
  post handoffs on a shared bus. The waiting agent runs `handoff wait` in the
  background and goes idle; the moment the handoff lands, the command exits
  and Claude Code wakes the session with the handoff as its output. No relay,
  no polling turns, no tokens spent while waiting.

## Adopt it in a project

```bash
git clone https://github.com/<you>/issues-loop
bash issues-loop/adopt.sh /path/to/your/project
```

`adopt.sh` copies the files below, never overwrites anything that exists, and
appends the workflow sections to an existing `CLAUDE.md` for you to merge.
Then:

1. Fill in the `<placeholders>` in `CLAUDE.md`: project name, how to run tests,
   the standing QA command an issue must pass before it counts as done.
2. Make sure a version file exists. `release.sh` auto-detects `VERSION`,
   `pyproject.toml`, `package.json`, `Cargo.toml`, or an `.xcodeproj`
   (set `RELEASE_VERSION_FILE` to force one).
3. `gh auth status` — the release script and the workflow use the GitHub CLI.
4. Drop the example entry and file your first issue:

```bash
python scripts/issues.py remove 1
python scripts/issues.py add --title "..." --description "..."
```

5. Commit, then tell Claude: **take care of issue 1**.

## What is in here

| path | what it is |
|------|------------|
| `CLAUDE.md` | template: the Issue Workflow section and the conventions the agent follows. This is the part that makes the loop work. |
| `.issues/readme.md` | the ledger rules: two files, id claiming, entry format, the editing hazard |
| `.issues/issues.yaml` | your issues (plain numeric ids) |
| `.issues/issues-agent.yaml` | agent-filed issues (`-agent` ids) |
| `.issues/visuals/` | images referenced by issues; uploaded with the GitHub issue |
| `scripts/issues.py` | safe ledger editor: `list`, `show`, `next-id`, `add`, `remove`, `validate`. No dependencies. |
| `scripts/release.sh` | semver release from Conventional Commits: bump, commit, tag, push, `gh release create` |
| `docs/handoff/` | thread handoff convention + template, and the agent handoff protocol |
| `.claude-plugin/marketplace.json` | lists the [`handoff` plugin](https://github.com/sinan/claude-handoff) (it lives in its own repo), so `claude plugin install handoff@issues-loop` also works |
| `.github/workflows/issues.yml` | CI: validates both ledger files on every push |
| `adopt.sh` | copies all of the above into another project |

## Day to day

Things you say to the agent:

- **"file an issue: …"** — it appends to `issues.yaml` (your file) with the next id.
- **"take care of issue 12"** — runs the loop above, stopping for your confirmation.
- **"take care of issues 12, 15 and 3-agent"** — one subagent per issue, each
  in its own git worktree, the main thread as project manager. You get a
  one-liner to test each result.
- **"retire this thread"** — it writes the handoff file, you open a new chat.
- **"you are the coder; join the handoff bus and wait for the designer"** —
  it joins as `coder`, arms its waiter, and wakes on every handoff. Tell the
  other session the same with its own role. Needs the plugin, once per
  machine: `claude plugin marketplace add sinan/claude-handoff` then
  `claude plugin install handoff@claude-handoff`.

Things you run yourself:

```bash
python scripts/issues.py list            # your backlog
python scripts/issues.py list --agent    # what the agent wants to do
python scripts/issues.py show 12-agent
python scripts/issues.py validate
handoff status --all                     # each agent: unread mail, waiting or not, tree + branch
bash scripts/release.sh                  # interactive release
```

## Rules learned the hard way

- **Never edit the ledger with a multiline regex.** A greedy pattern once
  deleted 29 entries in a single write, silently. `issues.py` splits the file
  at entry boundaries and asserts the entry count before and after every
  write. That assertion is the only thing that has ever caught it.
- **`12` and `12-agent` are different issues.** Do not "deduplicate" them,
  do not renumber. Cite ids verbatim, suffix included.
- **Merged worktrees get deleted, from git and from disk, in the same turn
  they merge.** Sixteen stale trees once piled up in a day, each a full copy
  of the repo.
- **Ask before committing. Always.** The agent proposes, you confirm.
- **Only the release script touches the version.**

## Customize

Everything is plain text. The usual edits:

- add project-specific rules at the bottom of `.issues/readme.md` (privacy
  constraints, a QA gate)
- change the confirmation gates in `CLAUDE.md` if you want fewer stops
  (I would not remove the one after implementation)
- point `release.sh` at another version file with `RELEASE_VERSION_FILE`
- drop the review step (step 5) for tiny projects; keep it for anything
  that matters

## License

MIT
