#!/usr/bin/env bash
# Tests for release-galtea.sh, on a throwaway git repository and a fake gh.
#
# No `set -e`, deliberately: a case asserting a non-zero exit would abort the
# run at its own command substitution and silently skip every later case.
set -uo pipefail
unset GITHUB_STEP_SUMMARY

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/release-galtea.sh"
PASS=0
FAIL=0
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }
ok() { echo "  ✓ $1"; PASS=$((PASS + 1)); }

# A fake gh that logs each call and keeps the notes file it was given.
mkdir -p "$WORKDIR/bin"
cat > "$WORKDIR/bin/gh" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/calls.log"
while [ $# -gt 0 ]; do
  if [ "$1" = "--notes-file" ]; then cp "$2" "$FAKE_DIR/notes.md"; fi
  shift
done
FAKE
chmod +x "$WORKDIR/bin/gh"

# A repository with a base commit and the given tags on it; the case then commits the push on top.
setup() {
  rm -rf "$WORKDIR/repo" "$WORKDIR/fake" && mkdir -p "$WORKDIR/repo/changelog" "$WORKDIR/fake" && cd "$WORKDIR/repo" || exit 1
  git init -q -b main && git config user.email t@t && git config user.name t
  echo "skill" > SKILL.md && git add . && git commit -qm base
  for tag in "$@"; do git tag "$tag"; done
  BEFORE=$(git rev-parse HEAD)
}
push() { git add -A && git commit -qm push && SHA=$(git rev-parse HEAD); }
run() { PATH="$WORKDIR/bin:$PATH" FAKE_DIR="$WORKDIR/fake" bash "$SCRIPT" "$BEFORE" "$SHA" > "$WORKDIR/out" 2>&1; echo $?; }
created() { grep -q "^release create $1 --target $SHA --title $2 --notes-file" "$WORKDIR/fake/calls.log" 2> /dev/null; }

echo "the first major entry releases 1.0.0 with the entry as notes"
setup
printf 'major\n\nThe skill has versions.\nOne release per merge.\n' > changelog/20261119-1030-first.md; push
code=$(run)
[ "$code" = 0 ] && created galtea--v1.0.0 "galtea 1.0.0" && ok "creates galtea--v1.0.0 on the pushed commit" || fail "exit $code: $(cat "$WORKDIR/out")"
[ "$(cat "$WORKDIR/fake/notes.md")" = "$(printf 'The skill has versions.\nOne release per merge.')" ] && ok "notes hold the entry text, not its level" || fail "notes: $(cat "$WORKDIR/fake/notes.md")"

echo "a patch entry bumps the last number"
setup galtea--v1.2.3
printf 'patch\n\nA typo.\n' > changelog/20261119-1030-typo.md; push
code=$(run)
[ "$code" = 0 ] && created galtea--v1.2.4 "galtea 1.2.4" && ok "creates galtea--v1.2.4" || fail "exit $code: $(cat "$WORKDIR/fake/calls.log" 2> /dev/null)"

echo "a major entry bumps the first number"
setup galtea--v1.2.3
printf 'major\n\nNew behaviour.\n' > changelog/20261119-1030-new.md; push
code=$(run)
[ "$code" = 0 ] && created galtea--v2.0.0 "galtea 2.0.0" && ok "creates galtea--v2.0.0" || fail "exit $code: $(cat "$WORKDIR/fake/calls.log" 2> /dev/null)"

echo "the last version is found by version order, not text order"
setup galtea--v1.9.0 galtea--v1.10.0 galtea-tracing--v9.0.0
printf 'patch\n\nA link.\n' > changelog/20261119-1030-link.md; push
code=$(run)
[ "$code" = 0 ] && created galtea--v1.10.1 "galtea 1.10.1" && ok "creates galtea--v1.10.1 and ignores other tag series" || fail "exit $code: $(cat "$WORKDIR/fake/calls.log" 2> /dev/null)"

echo "a push with no new entry releases nothing"
setup galtea--v1.0.0
echo "readme" > README.md; push
code=$(run)
[ "$code" = 0 ] && [ ! -e "$WORKDIR/fake/calls.log" ] && ok "calls gh not at all" || fail "exit $code: $(cat "$WORKDIR/fake/calls.log" 2> /dev/null)"

echo "a commit that is already released is left alone"
setup galtea--v1.0.0
printf 'patch\n\nA typo.\n' > changelog/20261119-1030-typo.md; push
git tag galtea--v1.0.1 "$SHA"
code=$(run)
[ "$code" = 0 ] && [ ! -e "$WORKDIR/fake/calls.log" ] && grep -q "already released as galtea--v1.0.1" "$WORKDIR/out" && ok "a re-run creates nothing" || fail "exit $code: $(cat "$WORKDIR/out")"

echo "an entry with a bad level is refused"
setup galtea--v1.0.0
printf 'minor\n\nA step.\n' > changelog/20261119-1030-step.md; push
code=$(run)
[ "$code" = 1 ] && [ ! -e "$WORKDIR/fake/calls.log" ] && ok "exits 1 and creates nothing" || fail "exit $code: $(cat "$WORKDIR/out")"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
