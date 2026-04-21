# Agents

This repository ships [Agent Skills](https://agentskills.io) for the [Galtea](https://galtea.ai) AI product testing and evaluation platform. Skills teach AI coding assistants (Claude Code, Cursor, and other Agent-Skills-compatible tools) how to interact with Galtea without the user having to explain it first.

## Available skills

| Skill | Purpose |
|---|---|
| [`galtea`](./skills/galtea/) | Interact with the Galtea Platform API: authenticate, manage products/versions/tests/metrics/endpoint-connections, run and monitor evaluations, inspect sessions and traces. |

## Entrypoint

Each skill is a directory containing `SKILL.md`. Start reading from there — the frontmatter (`name`, `description`, `when_to_use`) is the skill's contract, and the markdown body is the playbook. Supporting material lives under `references/` and is loaded on demand.

See [README.md](./README.md) for installation and prerequisites.

## Feedback

For issues with a skill's instructions or behavior (not the Galtea product), file an issue at https://github.com/Galtea-AI/skills/issues with the `skill-feedback` label.
