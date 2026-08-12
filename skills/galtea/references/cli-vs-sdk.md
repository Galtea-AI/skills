---
name: galtea-cli-vs-sdk
description: Decision framework for recommending the Galtea CLI (`galtea`) vs the Python SDK (`pip install galtea`), plus routing hints for the main SDK capabilities. Use when the user asks which approach to use for a Galtea workflow, mentions the SDK, or is deciding how to wire their AI product in.
---

# CLI vs Python SDK

Galtea exposes the same backend through two surfaces: the **`galtea` CLI** (one auto-generated subcommand per OpenAPI operation) and the **Python SDK** (`pip install galtea`). Mixing them in a single project is safe.

## When to recommend the CLI (`galtea`)

- Quick one-off queries (list products, check evaluation status, kick off an evaluation).
- Inspecting API responses directly via `-o json | jq` or `-f body.<field>`.
- Environments where Python is not available, or where adding a Python dependency is unwanted (CI shell scripts, Dockerfiles, ops tooling).
- Read-only introspection from CI, shell scripts, or other AI agents.
- Anything an agent (Claude Code, Cursor, Windsurf, etc.) is driving on the user's behalf -- the CLI is the lowest-friction path because the agent can run any command without writing a Python program first.

## When to recommend the Python SDK (`pip install galtea`)

- Any workflow that involves **running the user's AI product**. The SDK wraps the agent function loop and handles batching, retries, and inference logging so the user does not have to write that plumbing.
- **Conversation simulation** -- the SDK's simulator plays the user role across multi-turn scenarios, calling the agent each turn.
- **Tracing agent internals** -- decorator and context-manager forms capture tool calls and LLM calls as `Span` records.
- **Production monitoring with inline logging** -- single-call utilities that run the agent and persist the trace together.
- The user is already writing Python.

If Python is not an option and the user still needs one of the SDK-leaning workflows above (simulation, tracing, production logging), the CLI can talk to all the same endpoints, it just requires the caller to build the orchestration themselves.

## Key SDK capabilities (routing hints only)

Identifiers below are routing hints, not canonical names. Fetch the relevant `/sdk/api/*` page in `llms.txt` (see `SKILL.md`'s "Discover docs and commands" section) before advising on the exact method signature -- the SDK evolves frequently.

- **Agent function** -- the user's AI product, wrapped by the SDK and called by `galtea.evaluations.run(version_id, agent=...)` and the simulator. The SDK auto-detects the agent's input shape **from the first parameter's type annotation**, so annotate it deliberately. The mapping below reflects current SDK behavior; `/sdk/api/*` remains the source of truth if it ever changes:
  - **Recommended:** Use `def my_agent(messages: list[dict]) -> str` (receives the full chat history), or the `galtea.Agent` / `galtea.AgentInput` adapter for structured input plus session/inference context.
  - **Annotation → argument-shape mapping:**
    - `str` → the latest user message as a plain string.
    - `galtea.AgentInput` → a structured object (messages, session_id, trace_id, context_data). `trace_id` is the renamed `inference_result_id`; the old name still resolves, and the wire field stays `inferenceResultId`.
    - **anything else, including no annotation** → a `list[dict]` chat history (`[{"role": ..., "content": ...}, ...]`).
  - **Footgun:** an *unannotated* first parameter (`def agent(user_message):`) silently receives the `list[dict]` default, so an agent that assumes a bare string crashes (e.g. `'list' object has no attribute 'strip'`) and its evaluation ends up `SKIPPED`/unscored. Always annotate the first parameter with the shape you actually handle.
- **Simulator** -- plays the user role across multi-turn conversations, calling the agent each turn.
- **Tracing** -- captures internal agent operations (tool calls, LLM calls) as `Span` records via `galtea.spans`; both decorator and context-manager forms are available.
- **Trace generation** -- single-call utility on `galtea.traces` that runs the agent and logs the turn in one step.

SDK API reference pages live under `/sdk/api/*` in `llms.txt`. Installation instructions live at `https://docs.galtea.ai/sdk/installation`.

## Don't recommend raw `curl`

The CLI replaces every reason to write `curl -H "Authorization: Bearer $GALTEA_API_KEY" ...` calls by hand. If you are tempted to suggest a curl snippet, run `galtea <noun> <verb> --help` first -- there is almost certainly a one-line CLI equivalent that handles auth, retries, output formatting, and pagination for free.
