#!/usr/bin/env bash
# Tests for check-galtea-changelog.sh, on a throwaway git repository.
#
# No `set -e`, deliberately: a case asserting a non-zero exit would abort the
# run at its own command substitution and silently skip every later case.
set -uo pipefail
unset GITHUB_STEP_SUMMARY

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-galtea-changelog.sh"
PASS=0
FAIL=0
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }
ok() { echo "  ✓ $1"; PASS=$((PASS + 1)); }

# A repository with one base commit; the case then commits its pull request on top.
setup() {
  rm -rf "$WORKDIR/repo" && mkdir -p "$WORKDIR/repo/skills/galtea" "$WORKDIR/repo/changelog" && cd "$WORKDIR/repo" || exit 1
  git init -q -b main && git config user.email t@t && git config user.name t
  echo "skill" > skills/galtea/SKILL.md && echo "readme" > README.md
  git add . && git commit -qm base
  BASE=$(git rev-parse HEAD)
}
commit_pr() { git add -A && git commit -qm pr && HEAD_SHA=$(git rev-parse HEAD); }
run() { bash "$SCRIPT" "$BASE" "$HEAD_SHA" > "$WORKDIR/out" 2>&1; echo $?; }
expect() { # <exit> <text> <case>
  local code
  code=$(run)
  if [ "$code" = "$1" ] && grep -q "$2" "$WORKDIR/out"; then ok "$3"; else fail "$3 (exit $code: $(cat "$WORKDIR/out"))"; fi
}

setup; echo "new" >> skills/galtea/SKILL.md; printf 'major\n\nThe agent reads the docs first.\n' > changelog/20261119-1030-docs-first.md; commit_pr
expect 0 "valid major entry" "a skill change with a valid entry passes"

setup; echo "new" >> skills/galtea/SKILL.md; commit_pr
expect 1 "adds no changelog entry" "a skill change without an entry fails"

setup; echo "new" >> skills/galtea/SKILL.md; printf 'patch\n\nA.\n' > changelog/20261119-1030-a.md; printf 'patch\n\nB.\n' > changelog/20261119-1031-b.md; commit_pr
expect 1 "adds 2 changelog entries" "two entries fail"

setup; echo "new" >> skills/galtea/SKILL.md; printf 'patch\n\nA typo.\n' > changelog/typo-fix.md; commit_pr
expect 1 "must be <YYYYMMDD>-<HHMM>-<short-name>.md" "a badly named entry fails"

setup; echo "new" >> skills/galtea/SKILL.md; printf 'minor\n\nA new step.\n' > changelog/20261119-1030-step.md; commit_pr
expect 1 "must be 'major' or 'patch', got 'minor'" "a minor entry fails"

setup; echo "new" >> skills/galtea/SKILL.md; printf 'patch\nA typo.\n' > changelog/20261119-1030-typo.md; commit_pr
expect 1 "second line must be empty" "an entry with no empty second line fails"

setup; echo "new" >> skills/galtea/SKILL.md; printf 'patch\n\n\n' > changelog/20261119-1030-empty.md; commit_pr
expect 1 "says nothing from line 3 on" "an entry with no text fails"

setup; echo "more" >> README.md; commit_pr
expect 0 "releases nothing" "a change outside the skill with no entry passes"

setup; printf 'major\n\nFirst versioned release.\n' > changelog/20261119-1030-first.md; commit_pr
expect 0 "valid major entry" "an entry with no skill change passes"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
