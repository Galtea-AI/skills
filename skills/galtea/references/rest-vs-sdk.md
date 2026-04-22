---
name: galtea-rest-vs-sdk
description: Decision framework for recommending the Galtea REST API (curl) vs the Python SDK (`pip install galtea`), plus routing hints for the main SDK capabilities. Use when the user asks which approach to use for a Galtea workflow, mentions the SDK, or is deciding how to wire their AI product in.
---

# REST API vs Python SDK

Galtea exposes the same backend through two surfaces: the REST API (usable from anything that can `curl`) and the Python SDK (`pip install galtea`). Mixing them in a single project is safe.

## When to recommend the REST API (curl)

- Quick one-off queries (list products, check evaluation status).
- Debugging or inspecting API responses directly.
- Environments where Python is not available.
- Read-only introspection from CI, shell scripts, or other AI agents.

## When to recommend the Python SDK (`pip install galtea`)

- Any workflow that involves **running the user's AI product**. The SDK wraps the agent function loop and handles batching, retries, and inference logging so the user does not have to write that plumbing.
- **Conversation simulation** -- the SDK's simulator plays the user role across multi-turn scenarios, calling the agent each turn.
- **Tracing agent internals** -- decorator and context-manager forms capture tool calls and LLM calls as Trace records.
- **Production monitoring with inline logging** -- single-call utilities that run the agent and persist the inference result together.
- The user is already writing Python.

If Python is not an option and the user still needs one of the SDK-leaning workflows above (simulation, tracing, production logging), the REST API can do all of them, it just requires the caller to build the orchestration themselves.

## Key SDK capabilities (routing hints only)

Identifiers below are routing hints, not canonical names. Fetch the relevant `/sdk/api/*` page in `llms.txt` (see `SKILL.md`'s "Discover docs and endpoints" section) before advising on the exact method signature -- the SDK evolves frequently.

- **Agent function** -- the user's AI product, wrapped by the SDK. Multiple signatures are auto-detected; check the installation docs or `/sdk/api/*` for the current list.
- **Simulator** -- plays the user role across multi-turn conversations, calling the agent each turn.
- **Tracing** -- captures internal agent operations (tool calls, LLM calls) as Trace records; both decorator and context-manager forms are available.
- **Inference generation** -- single-call utility that runs the agent and logs the inference result in one step.

SDK API reference pages live under `/sdk/api/*` in `llms.txt`. Installation instructions live at `https://docs.galtea.ai/sdk/installation`.
