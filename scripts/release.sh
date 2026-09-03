#!/bin/bash
# release.sh — semver release from Conventional Commits
#
# Reads the commits since the last tag, suggests a bump (feat: → minor, fix: → patch,
# "!" or BREAKING → major), writes the new version into the project's version file,
# commits, tags, pushes, and creates a GitHub release. The version changes ONLY here.
# Never bump it in a feature commit.
#
# Version file: auto-detected in this order, or set RELEASE_VERSION_FILE to force one.
#   VERSION                          plain text, e.g. "1.4.2"
#   pyproject.toml                   version = "1.4.2"
#   package.json                     "version": "1.4.2"
#   Cargo.toml                       version = "1.4.2"
#   *.xcodeproj/project.pbxproj      MARKETING_VERSION = 1.4.2;
#
# Usage:
#   bash scripts/release.sh                   # fully interactive
#   bash scripts/release.sh minor             # pre-select bump type, confirm interactively
#   bash scripts/release.sh minor --yes       # fully non-interactive (what the agent runs)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

ARG_BUMP=""
ARG_YES=false
for arg in "$@"; do
    case "$arg" in
        patch|minor|major) ARG_BUMP="$arg" ;;
        --yes|-y)          ARG_YES=true ;;
        *) echo "  Unknown argument: $arg"; echo "  Usage: release.sh [patch|minor|major] [--yes]"; exit 1 ;;
    esac
done

# ── Locate the version file ───────────────────────────────────────────────────
VERSION_FILE="${RELEASE_VERSION_FILE:-}"
if [[ -z "$VERSION_FILE" ]]; then
    for candidate in VERSION pyproject.toml package.json Cargo.toml; do
        [[ -f "$candidate" ]] && { VERSION_FILE="$candidate"; break; }
    done
fi
if [[ -z "$VERSION_FILE" ]]; then
    pbx=$(find . -maxdepth 3 -name project.pbxproj -path '*.xcodeproj/*' 2>/dev/null | head -1)
    [[ -n "$pbx" ]] && VERSION_FILE="${pbx#./}"
fi
if [[ -z "$VERSION_FILE" ]]; then
    echo "  No version file found. Create a VERSION file (e.g. 'echo 0.1.0 > VERSION')"
    echo "  or set RELEASE_VERSION_FILE=<path>."
    exit 1
fi

# One small python helper reads and writes every supported format.
version_tool() {  # version_tool get | version_tool set <new>
python3 - "$VERSION_FILE" "$@" <<'PYEOF'
import re, sys
path, op = sys.argv[1], sys.argv[2]
new = sys.argv[3] if len(sys.argv) > 3 else None
name = path.rsplit("/", 1)[-1]
patterns = {
    "VERSION":         (r"^\s*(\S+)\s*$",                    lambda v: f"{v}\n"),
    "pyproject.toml":  (r'^version\s*=\s*"([^"]+)"',         lambda v: f'version = "{v}"'),
    "Cargo.toml":      (r'^version\s*=\s*"([^"]+)"',         lambda v: f'version = "{v}"'),
    "package.json":    (r'^\s*"version"\s*:\s*"([^"]+)"',    None),
    "project.pbxproj": (r"MARKETING_VERSION = ([^;]+);",     lambda v: f"MARKETING_VERSION = {v};"),
}
if name not in patterns:
    sys.exit(f"  unsupported version file: {path}")
pattern, render = patterns[name]
text = open(path).read()
flags = re.M
m = re.search(pattern, text, flags)
if op == "get":
    print(m.group(1).strip() if m else "0.0.0")
    sys.exit(0)
if not m:
    sys.exit(f"  no version line found in {path}")
if name == "package.json":
    # keep the original indentation of the version line
    out = re.sub(r'^(\s*"version"\s*:\s*)"[^"]+"', lambda mm: f'{mm.group(1)}"{new}"', text, count=1, flags=flags)
elif name == "project.pbxproj":
    out = re.sub(pattern, render(new), text)          # every target gets the same version
else:
    out = re.sub(pattern, render(new), text, count=1, flags=flags)
assert out != text or name == "VERSION", "version substitution changed nothing"
open(path, "w").write(out)
PYEOF
}

CURRENT_VERSION=$(version_tool get)
PROJECT_NAME=$(basename "$REPO_ROOT")

echo ""
echo "  ${PROJECT_NAME} release"
echo "  ─────────────────────────────────────────"
echo "  Version file:    ${VERSION_FILE}"
echo "  Current version: v${CURRENT_VERSION}"

LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
if [[ -n "$LAST_TAG" ]]; then
    echo "  Last release tag: ${LAST_TAG}"
    COMMITS=$(git log "${LAST_TAG}..HEAD" --format="%s")
else
    echo "  No previous tags — analyzing all commits."
    COMMITS=$(git log --format="%s")
fi

if [[ -z "$COMMITS" ]]; then
    echo "  No commits since last release. Nothing to release."
    exit 0
fi

# ── Suggest bump from conventional commits ────────────────────────────────────
SUGGESTED="patch"
if echo "$COMMITS" | grep -qE "^[a-z]+(\(.+\))?!:|BREAKING"; then
    SUGGESTED="major"
elif echo "$COMMITS" | grep -qE "^feat(\(.+\))?:"; then
    SUGGESTED="minor"
fi

echo ""
echo "  Commits in this release:"
echo "$COMMITS" | sed 's/^/    /'
echo ""
echo "  Suggested bump: ${SUGGESTED}"

BUMP="${ARG_BUMP:-}"
if [[ -z "$BUMP" ]]; then
    if $ARG_YES; then
        BUMP="$SUGGESTED"
    else
        read -r -p "  Bump type [${SUGGESTED}]: " BUMP
        BUMP="${BUMP:-$SUGGESTED}"
    fi
fi

NEW_VERSION=$(python3 -c "
parts = (list(map(int, '$CURRENT_VERSION'.split('.'))) + [0, 0, 0])[:3]
maj, mi, pa = parts
bump = '$BUMP'
if bump == 'major': maj, mi, pa = maj + 1, 0, 0
elif bump == 'minor': mi, pa = mi + 1, 0
else: pa += 1
print(f'{maj}.{mi}.{pa}')
")

echo "  New version: v${NEW_VERSION}"
if ! $ARG_YES; then
    read -r -p "  Proceed? [y/N]: " CONFIRM
    [[ "$CONFIRM" == "y" || "$CONFIRM" == "Y" ]] || { echo "  Aborted."; exit 0; }
fi

version_tool set "$NEW_VERSION"

git add "$VERSION_FILE"
git commit -m "chore(release): v${NEW_VERSION}"
# -m keeps this non-interactive even when tag.forceSignAnnotated is set
git tag -m "v${NEW_VERSION}" "v${NEW_VERSION}"
git push
git push origin "v${NEW_VERSION}"

NOTES=$(echo "$COMMITS" | sed 's/^/- /')
gh release create "v${NEW_VERSION}" --title "v${NEW_VERSION}" --notes "$NOTES"

echo ""
echo "  Released v${NEW_VERSION}."
