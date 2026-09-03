#!/usr/bin/env python3
"""Safe editor for the two-file issue ledger (.issues/issues.yaml, .issues/issues-agent.yaml).

Why this exists: the ledger is plain YAML and tempting to edit with a regex. A greedy
multiline pattern once deleted 29 entries in one write. This tool never regex-edits the
file. It splits the text into entries at column-0 "- id:" boundaries, keeps every entry
verbatim, and ASSERTS THE ENTRY COUNT before and after every write.

No dependencies. PyYAML is used for `validate` only if it happens to be installed.

Usage:
  python scripts/issues.py list [--agent]
  python scripts/issues.py show <id>
  python scripts/issues.py next-id [--agent]
  python scripts/issues.py add --title "..." --description "..." [--agent]
  python scripts/issues.py remove <id>
  python scripts/issues.py validate

An id ending in "-agent" always resolves to issues-agent.yaml; a plain number to
issues.yaml. `--agent` selects the agent file for list/next-id/add.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = ROOT / ".issues"
HUMAN_FILE = LEDGER_DIR / "issues.yaml"
AGENT_FILE = LEDGER_DIR / "issues-agent.yaml"
ENTRY_RE = re.compile(r"^- id: (\S+)\s*$", re.M)
WRAP = 100
INDENT = "    "


class Ledger:
    """A ledger file split into a header and verbatim entry blocks."""

    def __init__(self, path: Path):
        self.path = path
        text = path.read_text() if path.exists() else ""
        starts = [m.start() for m in ENTRY_RE.finditer(text)]
        if not starts:
            self.header, self.entries = text, []
            return
        self.header = text[: starts[0]]
        bounds = starts + [len(text)]
        self.entries = [text[a:b] for a, b in zip(bounds, bounds[1:])]
        # every entry block ends with exactly one newline so joins stay stable
        self.entries = [e.rstrip("\n") + "\n" for e in self.entries]

    @property
    def suffix(self) -> str:
        return "-agent" if self.path == AGENT_FILE else ""

    def ids(self) -> list[str]:
        return [ENTRY_RE.match(e).group(1) for e in self.entries]

    def numbers(self) -> list[int]:
        out = []
        for i in self.ids():
            m = re.fullmatch(r"(\d+)(-agent)?", i)
            if m:
                out.append(int(m.group(1)))
        return out

    def next_id(self) -> str:
        return f"{(max(self.numbers()) + 1) if self.numbers() else 1}{self.suffix}"

    def find(self, issue_id: str) -> int:
        for idx, i in enumerate(self.ids()):
            if i == issue_id:
                return idx
        raise SystemExit(f"error: no entry with id {issue_id!r} in {self.path.name}")

    def write(self, expected_count: int) -> None:
        # THE assertion: the file is only ever rewritten when the entry count is what the
        # operation promised. A wrong count means a parsing surprise, so stop and touch nothing.
        assert len(self.entries) == expected_count, (
            f"refusing to write {self.path.name}: expected {expected_count} entries, "
            f"have {len(self.entries)}"
        )
        header = self.header if self.header.endswith("\n") or not self.header else self.header + "\n"
        self.path.write_text(header + "".join(self.entries))
        # and re-read to prove the file round-trips to the same count
        assert len(Ledger(self.path).entries) == expected_count, "round-trip count mismatch"


def ledger_for(issue_id: str) -> Ledger:
    return Ledger(AGENT_FILE if issue_id.endswith("-agent") else HUMAN_FILE)


def format_entry(issue_id: str, title: str, description: str) -> str:
    # json.dumps gives a YAML-compatible double-quoted scalar with proper escaping
    body = textwrap.fill(
        " ".join(description.split()), width=WRAP, initial_indent=INDENT, subsequent_indent=INDENT
    )
    return f"- id: {issue_id}\n  title: {json.dumps(title)}\n  description: >-\n{body}\n"


def title_of(entry: str) -> str:
    m = re.search(r"^  title: (.*)$", entry, re.M)
    return m.group(1) if m else "(no title)"


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_list(args):
    led = Ledger(AGENT_FILE if args.agent else HUMAN_FILE)
    for i, e in zip(led.ids(), led.entries):
        print(f"{i:>10}  {title_of(e)}")
    print(f"({len(led.entries)} entries in {led.path.name})", file=sys.stderr)


def cmd_show(args):
    led = ledger_for(args.id)
    print(led.entries[led.find(args.id)], end="")


def cmd_next_id(args):
    print(Ledger(AGENT_FILE if args.agent else HUMAN_FILE).next_id())


def cmd_add(args):
    led = Ledger(AGENT_FILE if args.agent else HUMAN_FILE)
    before = len(led.entries)
    issue_id = led.next_id()
    led.entries.append(format_entry(issue_id, args.title, args.description))
    led.write(before + 1)
    print(f"filed {issue_id} in {led.path.name}")


def cmd_remove(args):
    led = ledger_for(args.id)
    before = len(led.entries)
    del led.entries[led.find(args.id)]
    led.write(before - 1)
    print(f"removed {args.id} from {led.path.name} ({before} -> {before - 1} entries)")


def cmd_validate(args):
    ok = True
    for path, pattern in ((HUMAN_FILE, r"\d+"), (AGENT_FILE, r"\d+-agent")):
        led = Ledger(path)
        ids = led.ids()
        dupes = {i for i in ids if ids.count(i) > 1}
        for i in dupes:
            ok = False
            print(f"{path.name}: duplicate id {i}")
        for i, e in zip(ids, led.entries):
            if not re.fullmatch(pattern, i):
                ok = False
                print(f"{path.name}: id {i!r} does not match the file's id format ({pattern})")
            if not re.search(r"^  title: \S", e, re.M):
                ok = False
                print(f"{path.name}: {i} has no title")
            if not re.search(r"^  description: >-\n    \S", e, re.M):
                ok = False
                print(f"{path.name}: {i} description must be a folded block ('>-') indented four spaces")
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(path.read_text()) or []
            if len(data) != len(led.entries):
                ok = False
                print(f"{path.name}: YAML parses to {len(data)} entries, splitter sees {len(led.entries)}")
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"{path.name}: YAML parse error: {exc}")
        print(f"{path.name}: {len(led.entries)} entries")
    if not ok:
        raise SystemExit(1)
    print("ok")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--agent", action="store_true"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("show"); s.add_argument("id"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("next-id"); s.add_argument("--agent", action="store_true"); s.set_defaults(fn=cmd_next_id)
    s = sub.add_parser("add")
    s.add_argument("--title", required=True); s.add_argument("--description", required=True)
    s.add_argument("--agent", action="store_true"); s.set_defaults(fn=cmd_add)
    s = sub.add_parser("remove"); s.add_argument("id"); s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("validate"); s.set_defaults(fn=cmd_validate)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
