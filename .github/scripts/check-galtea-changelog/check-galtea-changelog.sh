#!/usr/bin/env bash
# Checks the changelog entry of a pull request. A pull request that changes
# skills/galtea/ adds exactly one entry; any added entry must be well formed.
# Merging the entry releases the skill (release-galtea.sh).
#
# An entry is `changelog/<YYYYMMDD>-<HHMM>-<short-name>.md`:
#   line 1: major or patch (see CONTRIBUTING.md)
#   line 2: empty
#   line 3 on: what changes for the user's agent, in plain sentences
#
# Usage: check-galtea-changelog.sh <base sha> <head sha>. Run it from the repository root.
set -uo pipefail

BASE="${1:?usage: check-galtea-changelog.sh <base sha> <head sha>}"
HEAD="${2:?usage: check-galtea-changelog.sh <base sha> <head sha>}"
GUIDE="See 'Changelog entries' in CONTRIBUTING.md."

added=$(git diff --name-only --diff-filter=A "$BASE...$HEAD" -- changelog/)
skill_changes=$(git diff --name-only "$BASE...$HEAD" -- skills/galtea/)
count=$(printf '%s' "$added" | grep -c . || true)

if [ -n "$skill_changes" ] && [ "$count" -eq 0 ]; then
  echo "::error::This pull request changes skills/galtea/ but adds no changelog entry. Add one file under changelog/. $GUIDE"
  exit 1
fi
if [ "$count" -gt 1 ]; then
  echo "::error::This pull request adds $count changelog entries. Add exactly one: its merge is one release. $GUIDE"
  exit 1
fi
if [ "$count" -eq 0 ]; then
  echo "No skill change and no changelog entry: this merge releases nothing."
  exit 0
fi

entry="$added"
name="${entry#changelog/}"
if ! [[ "$name" =~ ^[0-9]{8}-[0-9]{4}-[a-z0-9-]+\.md$ ]]; then
  echo "::error file=$entry::The entry name must be <YYYYMMDD>-<HHMM>-<short-name>.md, in lowercase, for example 20261119-1030-read-deployment-docs.md. $GUIDE"
  exit 1
fi
level=$(git show "$HEAD:$entry" | sed -n 1p)
if [ "$level" != major ] && [ "$level" != patch ]; then
  echo "::error file=$entry,line=1::The first line must be 'major' or 'patch', got '$level'. Minor is not used. $GUIDE"
  exit 1
fi
if [ -n "$(git show "$HEAD:$entry" | sed -n 2p)" ]; then
  echo "::error file=$entry,line=2::The second line must be empty. $GUIDE"
  exit 1
fi
if [ -z "$(git show "$HEAD:$entry" | sed -n '3,$p' | tr -d '[:space:]')" ]; then
  echo "::error file=$entry,line=3::The entry says nothing from line 3 on. Write what changes for the user's agent. $GUIDE"
  exit 1
fi
echo "$entry is a valid $level entry: its merge releases the skill."
