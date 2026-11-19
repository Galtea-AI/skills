# Contributing to the galtea skill

## What a line may say

A line stays in the skill only if no platform release can make it false. A fact that a release can change, such as a field, a limit, a default, an error text or a version gate, lives in the Galtea docs of each deployment. The skill tells the agent where to read it.

## Every merge is a release

Each pull request that changes `skills/` adds one changelog entry: every skill there ships in the `galtea` plugin. When it merges, a workflow tags the merge commit `galtea--v<version>` and creates the GitHub release with the entry as its notes. If merges land close together, one release can hold several entries; it is major if any of them is. Only the latest version is supported. A pull request that does not change `skills/` adds no entry and releases nothing.

## Changelog entries

An entry is one file, `changelog/<YYYYMMDD>-<HHMM>-<short-name>.md`, with the date and time in UTC and a lowercase name:

```
major

The agent reads the docs of the deployment it is logged in to before it acts.
```

- **Line 1** is the level:
  - **major:** the agent can act differently. This is any change to what the skill tells the agent to do, a security fix included.
  - **patch:** the agent cannot act differently: a typo, a link, formatting, metadata.
  - **In doubt, major.** Minor is not used.
- **Line 2** is empty.
- **From line 3,** say what changes for the user's agent, in plain sentences.

The **📝 Changelog** check refuses a pull request that changes `skills/` with no entry, adds more than one, adds a malformed one, or edits, renames or deletes a released one: its text is already the release notes.
