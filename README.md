# Galtea Skills

[Agent Skills](https://github.com/anthropics/skills) that teach AI coding assistants (Claude Code, Cursor, etc.) how to work with [Galtea](https://galtea.ai) — the AI product testing and evaluation platform — and an opt-in Claude Code plugin, `galtea-tracing`, that sends your Claude Code sessions to Galtea.

## Skills

| Skill | Description |
|---|---|
| [galtea](./skills/galtea) | Interact with the Galtea Platform. Authenticate, manage products, versions, specifications, datasets, metrics (including classifier metrics), endpoint-connections, runs and monitors, run and monitor evaluations, and inspect sessions, traces, and spans. |
| [galtea-loop](./skills/galtea-loop) | Run the full Galtea improvement loop for an AI product: define specs, generate test cases, evaluate the product against them, then iterate the product and specs based on what failed. Vendor-neutral; drives Galtea via the CLI or the Python SDK. |

## Plugins

| Plugin | Description |
|---|---|
| [galtea-tracing](./plugins/galtea-tracing) | Send each Claude Code conversation to Galtea as a production session: each turn is a trace, with its model calls, tool calls and subagents as spans. Secret redaction is on by default and best-effort. |

Install it from the same marketplace as the skills:

```
/plugin marketplace add Galtea-AI/skills
/plugin install galtea-tracing@galtea
```

The install dialog asks for your API key and the product that receives the sessions. See the [plugin README](./plugins/galtea-tracing/README.md) for what it sends and how to mute it.

## Installation

This section and the next two cover the skills. The plugin above takes its key from its install dialog.

### Claude Code Plugin

Add the Galtea marketplace to Claude Code:

```
/plugin marketplace add Galtea-AI/skills
```

Then install the Galtea plugin:

```
/plugin install galtea@galtea
```

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

The skills need a Galtea account and an API key:

```bash
export GALTEA_API_KEY=gsk_...
```

API keys are found in the Galtea dashboard under **Settings → API Keys**. An account has one key by default, and regenerating it permanently replaces it. Enterprise plans can hold several named tokens.

## Usage

Once installed, the agent will automatically use the skills when relevant — for example:

- Setting up a new product version with an endpoint connection
- Writing specifications and generating datasets + metrics from them
- Running evaluations via `create-from-version`, `create-from-session`, or `create-from-trace`
- Polling async evaluations and reading their scores
- Uploading a dataset you already have as a CSV, with documents or images attached to its rows, and testing a product that reads them
- Tracing agent internals (tool calls, LLM calls) as Span records
- Querying products, datasets, metrics, sessions, traces, and spans

For the full Galtea-side docs page on this skill, see [docs.galtea.ai/sdk/integrations/agent-skill](https://docs.galtea.ai/sdk/integrations/agent-skill).

## Feedback & Requests

Something not working as expected in a skill or the plugin, or want a new skill? [Open an issue](https://github.com/Galtea-AI/skills/issues) with the `skill-feedback` label. For a plugin problem, attach the matching `hook.log` lines. Your coding agent can also submit feedback for you — just ask.
