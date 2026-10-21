# Agents

This repository ships [Agent Skills](https://agentskills.io) for the [Galtea](https://galtea.ai) AI product testing and evaluation platform, and one Claude Code plugin with hooks, `galtea-tracing`. Skills teach AI coding assistants (Claude Code, Cursor, and other Agent-Skills-compatible tools) how to interact with Galtea without the user having to explain it first.

## Available skills

| Skill | Purpose |
|---|---|
| [`galtea`](./skills/galtea/) | Interact with the Galtea Platform API: authenticate, manage products/versions/specifications/datasets/metrics/endpoint-connections/runs/monitors, run and monitor evaluations, inspect sessions, traces, and spans. |
| [`galtea-loop`](./skills/galtea-loop/) | Orchestrate the end-to-end Galtea improvement loop for an AI product: define specs, generate datasets, evaluate, iterate. Vendor-neutral; composes the `galtea` skill for CLI/SDK mechanics. |

## Plugins

| Plugin | Purpose |
|---|---|
| [`galtea-tracing`](./plugins/galtea-tracing/) | Claude Code plugin with hooks, not a skill. It sends each conversation to Galtea as a production session: each turn is a trace, with spans for its steps. Its code is `scripts/galtea_cc.py` (stdlib Python 3.9+), launched by `scripts/run.sh`. Its tests run with `python3 -m unittest discover plugins/galtea-tracing/tests`, and in CI on Linux, macOS and Windows. Its `plugin.json` sets no `version`, so each merge reaches users; tag a release `galtea-tracing--v<version>` for self-hosted users who pin one. |

## Entrypoint

Each skill is a directory containing `SKILL.md`. Start reading from there — the frontmatter (`name`, `description`, `when_to_use`) is the skill's contract, and the markdown body is the playbook. Supporting material lives under `references/` and is loaded on demand.

See [README.md](./README.md) for installation and prerequisites.

## Feedback

For issues with a skill's instructions or behavior, or with the `galtea-tracing` plugin (not the Galtea product), file an issue at https://github.com/Galtea-AI/skills/issues with the `skill-feedback` label.
