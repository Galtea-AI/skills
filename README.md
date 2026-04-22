# Galtea Skills

[Agent Skills](https://github.com/anthropics/skills) that teach AI coding assistants (Claude Code, Cursor, etc.) how to work with [Galtea](https://galtea.ai) — the AI product testing and evaluation platform.

## Skills

| Skill | Description |
|---|---|
| [galtea](./skills/galtea) | Interact with the Galtea Platform API. Query and manage products, versions, endpoint connections, tests, metrics, evaluations, sessions, and traces. |

## Installation

### Cursor Plugin

```
/add-plugin galtea
```

### skills CLI

```bash
npx skills add Galtea-AI/skills --skill "galtea"
```

### Manual symlink

```bash
git clone https://github.com/Galtea-AI/skills.git /path/to/galtea-skills
ln -s /path/to/galtea-skills/skills/galtea ~/.claude/skills/galtea
```

## Prerequisites

You need a Galtea account and an API key:

```bash
export GALTEA_API_KEY=gsk_...
```

API keys are found in the Galtea dashboard under **Settings → API Keys**.

## Usage

Once installed, the agent will automatically use these skills when relevant — for example:

- Setting up a new product version with an endpoint connection
- Triggering and monitoring evaluation runs
- Inspecting evaluation results and debugging failed sessions
- Querying products, tests, metrics, and traces
