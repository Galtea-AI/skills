#!/usr/bin/env bash
# Releases the galtea skill for a push to main that added a changelog entry:
# the next version after the last `galtea--v*` tag, a tag on the pushed commit,
# and a GitHub release whose notes are the entry. A push with no new entry
# releases nothing, and a commit that already has a release tag is left alone,
# so a re-run is safe.
#
# Usage: release-galtea.sh <sha before the push> <pushed sha>
# Needs the full history with tags, and GH_TOKEN with contents: write.
set -euo pipefail

BEFORE="${1:?usage: release-galtea.sh <before sha> <pushed sha>}"
SHA="${2:?usage: release-galtea.sh <before sha> <pushed sha>}"

entries=$(git diff --name-only --diff-filter=A "$BEFORE" "$SHA" -- changelog/)
if [ -z "$entries" ]; then
  echo "No new changelog entry: nothing to release."
  exit 0
fi

existing=$(git tag --points-at "$SHA" -l 'galtea--v*')
if [ -n "$existing" ]; then
  echo "$SHA is already released as $existing."
  exit 0
fi

level=patch
notes=""
for entry in $entries; do
  entry_level=$(git show "$SHA:$entry" | sed -n 1p)
  case "$entry_level" in
    major) level=major ;;
    patch) ;;
    *)
      echo "::error file=$entry,line=1::The first line must be 'major' or 'patch', got '$entry_level'." >&2
      exit 1
      ;;
  esac
  notes+="$(git show "$SHA:$entry" | sed -n '3,$p')"$'\n\n'
done

last=$(git tag -l 'galtea--v*' --sort=-v:refname | head -n 1)
current="${last#galtea--v}"
current="${current:-0.0.0}"
IFS=. read -r major minor patch <<< "$current"
if [ "$level" = major ]; then
  next="$((major + 1)).0.0"
else
  next="$major.$minor.$((patch + 1))"
fi

printf '%s' "$notes" > release-notes.md
gh release create "galtea--v$next" --target "$SHA" --title "galtea $next" --notes-file release-notes.md
rm -f release-notes.md
echo "Released galtea--v$next ($level) from ${last:-no earlier release}."
