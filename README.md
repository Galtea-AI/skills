# Galtea Skills

[Agent Skills](https://github.com/anthropics/skills) that teach AI coding assistants (Claude Code, Cursor, etc.) how to work with [Galtea](https://galtea.ai) — the AI product testing and evaluation platform.

## Skills

| Skill | Description |
|---|---|
| [galtea](./skills/galtea) | Interact with the Galtea Platform API. Authenticate, manage products/versions/specifications/tests/metrics/endpoint-connections, run and monitor evaluations, and inspect sessions and traces. |

## Installation

### Cursor Plugin

A Galtea [Cursor Plugin](https://cursor.com/docs/plugins) that bundles this skill is on the way. Once it is live on the [Cursor marketplace](https://cursor.com/marketplace), install with:

```
/add-plugin galtea
```

Until the plugin is live, use the **skills CLI** or **Manual symlink** options below.

### skills CLI

Install via the [skills CLI](https://github.com/anthropics/skills):

```bash
npx skills add Galtea-AI/skills --skill "galtea"
```

### Manual symlink

Clone this repo and symlink the skill into your agent's skills directory:

```bash
git clone https://github.com/Galtea-AI/skills.git /path/to/galtea-skills
ln -s /path/to/galtea-skills/skills/galtea /path/to/skills-directory/galtea
```

## Prerequisites

You need a Galtea account and an API key:

```bash
export GALTEA_API_KEY=gsk_...
```

API keys are found in the Galtea dashboard under **Settings → API Keys**. Each account has a single key; regenerating permanently replaces it.

## Usage

Once installed, the agent will automatically use this skill when relevant — for example:

- Setting up a new product version with an endpoint connection
- Writing specifications and generating tests + metrics from them
- Running evaluations via `fromVersion`, `fromSession`, or `fromInferenceResult`
- Polling async evaluations and reading their scores
- Tracing agent internals (tool calls, LLM calls) as Trace records
- Querying products, tests, metrics, sessions, and traces

For the full Galtea-side docs page on this skill, see [docs.galtea.ai/sdk/integrations/agent-skill](https://docs.galtea.ai/sdk/integrations/agent-skill).

## Feedback & Requests

Something not working as expected, or want a new skill? [Open an issue](https://github.com/Galtea-AI/skills/issues) with the `skill-feedback` label. Your coding agent can also submit feedback for you — just ask.
