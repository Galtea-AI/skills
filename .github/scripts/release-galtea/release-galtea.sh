#!/usr/bin/env bash
# Releases the galtea skill for a push to main: every changelog entry added
# since the last `galtea--v*` tag goes into one release, the next version after
# that tag, tagged on the pushed commit. Diffing from the tag, not from the
# push, lets a run catch up the entries of a run that was cancelled or failed.
# Releases nothing when no entry was added, for a commit already tagged, or for
# a commit the last release already includes, so a re-run is safe.
#
# Usage: release-galtea.sh <pushed sha>
# Needs the full history with tags, and GH_TOKEN with contents: write.
set -euo pipefail

SHA="${1:?usage: release-galtea.sh <pushed sha>}"

existing=$(git tag --points-at "$SHA" -l 'galtea--v*')
if [ -n "$existing" ]; then
  echo "$SHA is already released as $existing."
  exit 0
fi

last=$(git tag -l 'galtea--v*' --sort=-v:refname | head -n 1)
if [ -z "$last" ]; then
  entries=$(git ls-tree -r --name-only "$SHA" -- changelog/)
else
  ancestor=0
  git merge-base --is-ancestor "$last" "$SHA" || ancestor=$?
  if [ "$ancestor" -eq 1 ]; then
    if git merge-base --is-ancestor "$SHA" "$last"; then
      echo "$SHA is older than the last release $last, which already includes it."
      exit 0
    fi
    echo "::error::$SHA and the last release $last have diverged: $last is not on this history. Move or delete that tag, then re-run." >&2
    exit 1
  elif [ "$ancestor" -ne 0 ]; then
    exit "$ancestor"
  fi
  entries=$(git diff --name-only --diff-filter=A "$last" "$SHA" -- changelog/)
fi
if [ -z "$entries" ]; then
  echo "No new changelog entry: nothing to release."
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
