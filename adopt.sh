#!/bin/bash
# adopt.sh — drop this workflow into an existing project
#
#   bash adopt.sh /path/to/your/project
#
# Copies the ledger, the scripts, the handoff folder and the CI check into the target.
# Never overwrites a file that already exists (prints SKIP instead). If the target has
# no CLAUDE.md, the template is copied; if it has one, the "Issue Workflow" and
# "Key Conventions" sections are appended for you to merge by hand.

set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="${1:?usage: adopt.sh /path/to/project}"
DST="$(cd "$DST" && pwd)"

copy() {  # copy <relative path>
    local rel="$1"
    if [[ -e "$DST/$rel" ]]; then
        echo "  SKIP  $rel (exists)"
    else
        mkdir -p "$(dirname "$DST/$rel")"
        cp "$SRC/$rel" "$DST/$rel"
        echo "  ADD   $rel"
    fi
}

echo ""
echo "  Adopting the issues workflow into $DST"
echo ""
for f in .issues/readme.md .issues/issues.yaml .issues/issues-agent.yaml .issues/visuals/.gitkeep \
         scripts/issues.py scripts/release.sh \
         docs/handoff/readme.md docs/handoff/TEMPLATE.md \
         .github/workflows/issues.yml; do
    copy "$f"
done

if [[ -e "$DST/CLAUDE.md" ]]; then
    if grep -q "^## Issue Workflow" "$DST/CLAUDE.md"; then
        echo "  SKIP  CLAUDE.md already has an Issue Workflow section"
    else
        { echo ""; sed -n '/^## Issue Workflow/,/^## State + next steps/p' "$SRC/CLAUDE.md" | sed '$d'; } >> "$DST/CLAUDE.md"
        echo "  APPEND CLAUDE.md (Issue Workflow + Key Conventions sections; merge by hand)"
    fi
else
    copy CLAUDE.md
fi

if ! grep -qs "^\.claude/worktrees/" "$DST/.gitignore"; then
    printf '\n# agent worktrees: live only until their branch merges\n.claude/worktrees/\n' >> "$DST/.gitignore"
    echo "  APPEND .gitignore (.claude/worktrees/)"
fi

echo ""
echo "  Next:"
echo "    1. Fill in the <placeholders> in CLAUDE.md (project name, commands, QA step)."
echo "    2. Make sure a version file exists (VERSION, pyproject.toml, package.json, Cargo.toml"
echo "       or an .xcodeproj) — scripts/release.sh auto-detects it."
echo "    3. gh auth status   (release.sh and the workflow use the GitHub CLI)"
echo "    4. python scripts/issues.py remove 1   # drop the example entry, then file your first issue"
echo "    5. Commit, then tell Claude: \"take care of issue 1\""
echo ""
