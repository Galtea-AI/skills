---
name: galtea
description: Interact with the Galtea platform -- the AI product testing and evaluation platform for AI/LLM products -- and access its documentation. Use when needing to (1) query or modify Galtea data (products, datasets, evaluations, etc.) via the `galtea` CLI, (2) set up a test pipeline for an AI product, (3) run or monitor evaluations of an AI product, (4) wire an AI product into Galtea via the Python SDK, or (5) look up Galtea docs, concepts, or CLI/SDK usage.
---

# Galtea

Galtea is an AI product testing and evaluation platform. Teams use it to define behavioral specifications, generate datasets of test cases, run evaluations (AI-as-judge, deterministic, or human), and iterate their AI products toward production with confidence.

This skill drives the **`galtea` CLI** on behalf of the user: install the binary, authenticate, discover commands and docs, then run or inspect evaluations. The CLI ships one subcommand per OpenAPI operation under a `galtea <noun> <verb>` tree (e.g. `galtea products list`, `galtea evaluations create-from-version`), so the source of truth for arguments is `galtea <noun> <verb> --help`. This file points at it rather than duplicating endpoint shapes.

If the user is new to Galtea, send them through `https://docs.galtea.ai/quickstart`, then `https://docs.galtea.ai/sdk/tutorials/writing-specifications`, then `https://docs.galtea.ai/sdk/tutorials/run-dataset-based-evaluations` -- the shortest zero-to-evaluation path.

## Quick Links

| Resource | URL | When to use |
|---|---|---|
| Docs index (LLM-optimized) | `https://docs.galtea.ai/llms.txt` | First stop for discovering docs pages. Grep for `/sdk/tutorials/`, `/concepts/`, `/api-reference/`, `/cli/`. |
| Full docs dump | `https://docs.galtea.ai/llms-full.txt` | When you need all docs content in one fetch (large). |
| CLI installation | `https://docs.galtea.ai/cli/installation` | Homebrew / apt / dnf / pip paths and verification. |
| CLI usage | `https://docs.galtea.ai/cli/usage` | Authentication flow + first commands. |
| OpenAPI spec | `https://api.galtea.ai/openapi.json` | Raw source of endpoint shapes. Prefer `galtea <noun> <verb> --help` -- the CLI reads from this same spec. |
| Changelog | `https://docs.galtea.ai/changelog` | Check for recent metrics, endpoints, or feature changes. |
| Quickstart guide | `https://docs.galtea.ai/quickstart` | Onboard new users. |
| SDK installation | `https://docs.galtea.ai/sdk/installation` | Python SDK setup (`pip install galtea`). |
| Platform UI | `https://platform.galtea.ai` | Where users manage products, view results, and generate API keys. |
| Product support | `mailto:support@galtea.ai` | For Galtea product issues (not skill issues). |

Any docs page URL works with a `.md` suffix (e.g. `https://docs.galtea.ai/quickstart.md`) to get clean markdown content.

## How Galtea Works

### Platform entities

The entities the agent will work with most, grouped by lifecycle scope:

- **The product under test:** `Product` -> `Version` -> `EndpointConnection` (how Galtea reaches the running product). `Specification`s define what the product should do; each links `Metric`s and auto-derives `Dataset`s.
- **What you test with:** `Dataset` groups `TestCase`s. A `Metric` defines how to score -- see "Metric types" below.
- **What the product produces at runtime:** `Session` -> `Trace` (one turn) -> `Span` (internal tool / LLM calls captured per turn).
- **How performance is measured:** an `Evaluation` applies a `Metric` to a whole `Session` (conversation-level) or to one `Trace` (turn-level). `create-from-version` is the high-level entry point that resolves all of the above for a `Version` in one call -- see "Evaluation creation paths" below.

**The REST paths and wire fields kept their pre-rename spelling.** The entities were renamed; the HTTP contract was not. So `Dataset` still lives at `/tests`, `Trace` at `/inferenceResults`, and `Span` at `/traces`, and body and query fields stay `testIds`, `testType`, `inferenceResultId`, and so on. You see the new names in the CLI nouns and the SDK; you see the old ones in URLs, JSON payloads, and `--flag` names. Both are current -- do not "fix" either to match the other.

Supporting entities: `Model` (LLM the product runs on, linked to a `Version`) is distinct from the evaluator model named on AI-as-judge `Metric`s. `UserGroup` routes `HUMAN_EVALUATION` metrics to specific reviewers.

Read [Concepts Overview](https://docs.galtea.ai/concepts/overview) (append `.md` for clean markdown) for the canonical diagram, schema-validated relationships, and per-concept docs pages.

### Two evaluation contexts

| Context | When to use | How it works |
|---|---|---|
| **Pre-deployment (dataset-based)** | Regression testing before release against curated or generated `TestCase`s | Create `Dataset` + `TestCase`s, run the agent against them, evaluate. Direct invocation for single-turn; the Conversation Simulator for multi-turn (default for Behavior datasets). |
| **Production monitoring** | Evaluate real user interactions after deployment | Log `Trace`s from production into a `Session` (single-turn or multi-turn), evaluate asynchronously. |

**Multi-turn applies to both contexts.** A `Session` can hold many `Trace`s regardless of origin. By default, only Behavior datasets are *generated* with multi-turn scenarios in mind (`goal`, `userPersona`, `stoppingCriterias`); Accuracy and Security datasets can technically be multi-turn but are typically single-turn.

### Dataset types: the name depends on the surface

`DatasetType` has a user-facing name and a wire value, and they differ. Pick by the surface you are on:

| Meaning | SDK (user-facing) | CLI / raw API (wire) |
|---|---|---|
| Quality and correctness | `ACCURACY` | `QUALITY` |
| Security, safety, and bias | `SECURITY` | `RED_TEAMING` |
| Multi-turn dialogue | `BEHAVIOR` | `SCENARIOS` |

The **SDK accepts either name** on `galtea.datasets.create(type=...)` and normalizes it (`DatasetType.ACCURACY` is the enum form). The **CLI and raw API accept only the wire value** -- `galtea datasets create type: SCENARIOS` works, `type: BEHAVIOR` does not. `galtea datasets list-types` reports wire values too (the distinct types already in use, not the full catalog). `TestType` is the old name for `DatasetType` and still imports, with a deprecation warning.

### Metric types (verify against docs)

The `MetricSource` enum drives this:

- **AI-as-judge** (`PARTIAL_PROMPT`): An LLM scores the output against a user-written judge rubric; Galtea prepends a structured data block built from the separately-supplied `evaluation_params`. User-creatable. Most common. **Use this for any new AI-as-judge metric.**
- **`FULL_PROMPT` (creation rejected by the API):** Legacy AI-as-judge variant where the user wrote the entire prompt, including the location of the data (`evaluation_params`). The API rejects `POST /metrics` calls with `source=FULL_PROMPT` -- both explicit and silently inferred (the latter happens when a judge prompt is supplied without `evaluation_params`). Existing FULL_PROMPT metrics still list, filter, and evaluate normally. Use `PARTIAL_PROMPT` for any new metric.
- **Human evaluation** (`HUMAN_EVALUATION`): A human reviewer scores via the platform. Requires `UserGroup`s. User-creatable.
- **Self-hosted / user-computed** (`SELF_HOSTED`): You compute the score on your own infrastructure and upload it as a `float` -- either pre-calculated, or computed at runtime via an SDK custom-score class (see `/concepts/metric/evaluation-types` and `/sdk/tutorials/evaluate-with-custom-metrics` for current SDK class names). User-creatable.
- **Built-in deterministic** (`DETERMINISTIC`, `DEEPEVAL`): Platform-defined classic NLP and DeepEval metrics (BLEU, ROUGE, JSON field match, etc.). Galtea computes the score. **Not user-creatable** -- pick from the catalog.

Custom-vs-built-in is orthogonal: users can create custom metrics in any of the user-creatable types above. Fetch `/concepts/metric` and `/concepts/metric/evaluation-types` for the authoritative catalog.

## Core Rules

1. **Use the `galtea` CLI for everything that talks to the API.** If `galtea --version` does not return a version, install the binary first (see the CLI Installation section below and [references/cli-install.md](references/cli-install.md)). If `galtea whoami` is not authenticated, run the Authentication flow below before any other call. Never hand-craft `curl -H "Authorization: Bearer ..."` requests when a `galtea` command exists -- the CLI handles auth, retries, output formatting, and follows server-side schema changes via `galtea sync`.
2. **Documentation first -- never advise from memory.** Galtea ships frequently; commands, metrics, and SDK APIs change. Before you advise on a workflow, concept, or argument shape, fetch the relevant docs page (see "Discover docs and commands" below) and run `galtea <noun> <verb> --help` for the live argument shape. **Prefer the raw Markdown form of docs pages by appending `.md` to the route** (for example, `https://docs.galtea.ai/quickstart.md` or `https://docs.galtea.ai/cli/installation.md`) -- it is the fastest and most reliable way to read complete docs content without losing text to tabs or client-side rendering. Examples inlined here are illustrative, not authoritative.
3. **Discover commands via `galtea --help`.** `galtea --help` lists every resource (`products`, `evaluations`, `sessions`, ...); `galtea <noun> --help` lists verbs under a resource; `galtea <noun> <verb> --help` shows the example invocation, request schema, and response schema for one operation. After the API ships new endpoints, run `galtea sync` to refresh the local spec cache.
4. **Argument syntax depends on the HTTP verb.** GET / list operations expose query params as kebab-case `--flag` arguments (e.g. `galtea evaluations list --version-ids v1,v2 --statuses PENDING`). POST / create / update operations take body fields via Restish's inline shorthand (e.g. `galtea evaluations create-from-version versionId: ver_xxx`) or JSON on stdin (e.g. `echo '{"versionId":"v1"}' | galtea evaluations create-from-version`). The `EXAMPLES` block in `--help` shows the right form for each operation. **In non-TTY contexts (scripts, CI, agent harnesses) always redirect stdin on the inline-shorthand form** -- append `</dev/null` (or pipe a JSON body in) -- otherwise the command blocks waiting for stdin even when the inline body is complete. See Gotchas.
5. **Evaluations are async, but the create response shape varies.** `galtea evaluations create-from-version` returns `202` with `{jobId, message, specifications, testCaseCount}` -- the rows have not been created yet, so list them with `galtea evaluations list --version-ids <id> --statuses PENDING` to learn their IDs. The other three create paths (`create-from-session`, `create-from-trace`, `create-single-turn`) return `201` with the array of `Evaluation` rows already persisted -- pull IDs straight from that response, no listing step needed. In every case the rows start at `status: PENDING`; **batch-poll** via `galtea evaluations list` (one HTTP request per poll cycle regardless of count) -- prefer a scoped filter (`--version-ids` / `--session-ids` / `--inference-result-ids`) when one is available, since scoped filters can be combined with `--statuses` safely. When you only have raw IDs (e.g. `create-single-turn`), use `--ids` alone and filter status client-side -- see the "`--ids` + any other filter" gotcha below. Only fall back to `galtea evaluations get <id>` when you have a single ID and need its full detail. Stop when no rows remain at `status: PENDING`. Terminal states are `SUCCESS`, `FAILED`, `SKIPPED`, and `PENDING_HUMAN` (waits for a human reviewer -- treat as terminal for polling and surface to the user).
6. **Soft deletes.** Deleted rows have `deletedAt` set; list endpoints exclude them by default.
7. **Discover the user's org via `galtea auth get-current-user`.** The authenticated user belongs to exactly one org, exposed as `organizationId` on the returned `User`. For credit status: `galtea organizations get-credit-status <organizationId>` (positional id).
8. **`list` endpoints default to your own org.** A bare `galtea <noun> list` (no filters) returns only the entities in *your* organization -- the API key implies the org, so you never pass an org id to see your own data. Narrow within your org via the relation filters shown in `--help` (`--product-ids`, `--version-ids`, …).

## Environment

| Variable | Purpose |
|---|---|
| `GALTEA_API_KEY` | `gsk_*` bearer token. When set, takes precedence over the cached key in `apis.json`. Useful for CI / scripts / Docker; once `galtea login` has run on a developer machine, this is optional. |
| `GALTEA_CONFIG_DIR` | Override the per-user config directory where `apis.json` (host + token) lives. Defaults: `~/.config/galtea` (Linux), `~/Library/Application Support/galtea` (macOS), `%AppData%\galtea` (Windows). |
| `GALTEA_CACHE_DIR` | Override the cache directory for the parsed OpenAPI spec (`raw.cbor`). Defaults to `~/.cache/galtea/`. |
| `NO_COLOR` | Set to any value to disable ANSI color in CLI output. |

Run `galtea help environment` for the canonical list as the binary sees it.

The changelog at `https://docs.galtea.ai/changelog` lists every new metric, endpoint, and feature by date -- consult it when the user asks about something recent.

## CLI Installation

If `galtea --version` does not return a version, install the CLI before doing anything else. The full Homebrew / apt / dnf / pip install paths and verification steps live in [references/cli-install.md](references/cli-install.md). Quickest path on any OS with Python 3.9+:

```bash
pip install galtea-cli
galtea --version
```

The PyPI wheel bundles the same `galtea` binary used by every other channel. For Homebrew, Debian/Ubuntu (`apt`), Fedora/RHEL/Rocky/Alma (`dnf`/`yum`), and the official repository setup, see [references/cli-install.md](references/cli-install.md). Do not run `sudo` install commands on the user's machine without their explicit approval -- surface the command and let them run it.

## Authentication

Galtea uses bearer-token auth. The CLI handles the `Authorization` header, retries, and key persistence -- you do not write `curl -H "Authorization: Bearer ..."` yourself.

### First-time login

```bash
galtea login
# Paste your gsk_* API key when prompted.
# The CLI validates against the server, stores the key at the platform
# config dir (mode 0600), and refreshes the OpenAPI command tree in the same step.
```

If the user does not have a key, send them to `https://platform.galtea.ai/settings`. Each account has a single key; regenerating permanently replaces it. Keys are shown once -- they should store it somewhere safe before closing the page.

### Non-interactive (CI, Docker, scripted agents)

```bash
export GALTEA_API_KEY=gsk_...
galtea login          # optional once GALTEA_API_KEY is exported
```

When `GALTEA_API_KEY` is set, `galtea login` becomes optional -- API calls pick up the env var directly. Run `galtea login` once on a developer machine to also refresh the OpenAPI command tree; in a short-lived CI job that runs only one or two API calls, the env var alone is sufficient.

There is intentionally **no plaintext `--api-key` flag** -- a flag value lands in shell history (`~/.bash_history`, `~/.zsh_history`) and `ps` listings, so credentials passed that way leak. Use the env var or the interactive prompt.

**Receiving a pasted key from the user.** Take the key as sensitive free-text in the chat; do **not** use any structured-question tool (e.g. Claude Code's `AskUserQuestion`) for this -- pasting secrets into option metadata leaks them into logs. Either (a) ask the user to run `galtea login` themselves and paste at the CLI prompt, or (b) accept the key in chat and pass it once via `GALTEA_API_KEY=<key> galtea login` -- never echo it back, never write it to a tracked file.

### Self-hosted / staging

```bash
galtea login --host https://galtea.your-company.example.com
```

The host is persisted in `apis.json`; subsequent calls hit that host until you run `galtea login` again with a different `--host`.

### Verify

```bash
galtea whoami                  # host + key fingerprint + auth status (exits non-zero if not authenticated)
galtea auth get-current-user   # full User object, including organizationId
galtea health                  # pings the API; useful as a connectivity probe
```

### On 401 from a previously valid key

The key was rotated or revoked. Reset:

```bash
galtea logout
galtea login          # paste the new key
```

## Discover docs and commands

### Docs index (`llms.txt`)

Cache the index under `/tmp` with a 24-hour TTL so you do not re-download each turn:

```bash
if [ ! -f /tmp/galtea-llms.txt ] || \
   [ -n "$(find /tmp/galtea-llms.txt -mmin +1440 2>/dev/null)" ]; then
  curl -s https://docs.galtea.ai/llms.txt > /tmp/galtea-llms.txt
fi

grep '/sdk/tutorials/'  /tmp/galtea-llms.txt   # tutorials (end-to-end playbooks)
grep '/concepts/'       /tmp/galtea-llms.txt   # entity definitions + hierarchy
grep '/api-reference/'  /tmp/galtea-llms.txt   # per-endpoint reference pages
grep '/cli/'            /tmp/galtea-llms.txt   # CLI installation / usage

# Fetch one specific page as clean markdown (append .md to any page URL)
curl -s https://docs.galtea.ai/sdk/tutorials/run-dataset-based-evaluations.md
```

For end-to-end playbooks (creating a product, simulating conversations, tracing an agent, human evaluation, production monitoring) look under `/sdk/tutorials/`. For entity definitions and the hierarchy between them look under `/concepts/`. For per-endpoint reference pages look under `/api-reference/`. For CLI installation and usage, look under `/cli/`.

**Tool preference for doc fetching.** If your host agent provides `WebFetch` / `WebSearch` (Claude Code, Cursor, etc.), prefer them over `curl` -- they handle summarization, caching, and large-page trimming for free. Use `curl` when you need raw bytes for a `grep` pipeline, when caching to `/tmp`, or when no native fetch tool is available.

### CLI commands

```bash
galtea --help                     # all resources (products, evaluations, sessions, ...)
galtea <noun> --help              # all verbs under one resource
galtea <noun> <verb> --help       # description, request schema, response schema, examples
galtea sync                       # refresh the local OpenAPI command tree after an API release
galtea raw <operationId>          # escape hatch for the bare Restish form (hidden from --help, still works)
```

The CLI re-parents every OpenAPI operation under a `<noun>` parent generated from the spec's `tags`, with the `x-cli-name` extension as the verb. The resource list always reflects the latest `galtea sync`.

### Output formats

```bash
galtea X Y                  # JSON (default for endpoint responses)
galtea X Y -o table         # only valid against array/list responses; objects and error bodies render as JSON
galtea X Y -o json | jq …   # pipe through jq for ad-hoc filtering
galtea X Y -f body.id       # restish result filter (single field, no jq needed)
```

For debugging an HTTP-level issue, add `-v` or `--verbose` to any command -- the CLI prints the underlying request/response. (Note: `-V` is bound to `--version`; both `-v` and `--verbose` enable HTTP debug output.)

## Evaluation creation paths

Choose the right creation command based on what the user wants to evaluate. For a complete end-to-end CLI walkthrough of the `create-from-version` path (find product -> find version -> create -> list PENDING -> poll), read [references/evaluate-version.md](references/evaluate-version.md) -- fetch it whenever the user asks to run a full evaluation pass on a version, kick off evaluations, or needs to see the async lifecycle concretely.

| User goal | Command | Key input | Notes |
|---|---|---|---|
| Evaluate all datasets for a version at once | `galtea evaluations create-from-version` | `versionId: <id>` | Resolves specs, metrics, and datasets automatically |
| Evaluate a specific conversation | `galtea evaluations create-from-session` | `sessionId: <id>` | Optional `metrics`, `specificationIds` body fields to narrow scope |
| Evaluate a single turn (production monitoring) | `galtea evaluations create-from-trace` | `inferenceResultId: <id>` | Body field keeps the old name -- see the wire-contract note above. Optional `metrics`, `specificationIds` |
| Quick one-off evaluation without sessions | `galtea evaluations create-single-turn` | inline input + metric body fields | No session/version setup |
| Re-run failed evaluations | `galtea evaluations retry` | `ids: [e1, e2, e3]` (body) | Only retries `FAILED` evaluations; returns 202 with `{retried, skipped, errors}` |
| Replay onto a new metric revision | `galtea evaluations replay-from-metrics` | `metricGroupId`, `newMetricId`, `productIds` (body) | Returns 202 |

The four create paths split into two groups by response shape:

- **`create-from-version` -- async, returns 202 + `jobId`.** The actual evaluation rows are created in the background. List them with `galtea evaluations list --version-ids <id> --statuses PENDING` to learn their IDs (this is the path walked end-to-end in [references/evaluate-version.md](references/evaluate-version.md)).
- **`create-from-session`, `create-from-trace`, `create-single-turn` -- synchronous-creation, return 201 + array of `Evaluation` rows.** Pull IDs straight from the response; no separate listing step needed. Then **batch-poll** the whole set in one call:

  ```bash
  # Capture the array, build a comma-separated id list, then poll the batch.
  # `list --ids` returns the full Evaluation rows in one HTTP request,
  # avoiding the N-call overhead of looping `evaluations get` per id.
  # </dev/null on the create call is required in non-TTY contexts -- see Gotchas.
  IDS=$(galtea evaluations create-from-session sessionId: <sessionId> -o json </dev/null | jq -r '[.[].id] | join(",")')
  galtea evaluations list --ids "$IDS" -o json | jq '.[] | {id, status, score}'

  # Poll until none are PENDING. Filter status CLIENT-SIDE -- combining `--ids` with
  # `--statuses PENDING` triggers a false 404 once the rows leave PENDING (see Gotchas).
  while [ "$(galtea evaluations list --ids "$IDS" --no-cache -o json | jq '[.[] | select(.status == "PENDING")] | length')" -gt 0 ]; do
    sleep 5
  done
  galtea evaluations list --ids "$IDS" -o json | jq '.[] | {id, status, score}'
  ```

  Substitute `create-from-trace` (with `inferenceResultId: <id>` -- the verb took the new name, the body field kept the old one) or `create-single-turn` (see `--help` for the body shape) -- the response shape and follow-up polling are identical.

In both groups, individual evaluations start at `status: PENDING` and reach `SUCCESS` / `FAILED` / `SKIPPED` / `PENDING_HUMAN` as workers process them. Prefer `galtea evaluations list --ids …` (or `--version-ids` / `--session-ids` if the scope already filters them) over a per-id loop with `evaluations get`; the batch call traffics one HTTP request per poll cycle regardless of how many evaluations you queued. Treat `PENDING_HUMAN` as terminal for polling -- it waits for a human reviewer.

## Common Workflows

Each workflow below maps to a docs page. Fetch the page via `llms.txt` before advising -- the skill provides routing, not the full procedure.

| User wants to... | Workflow | Docs path |
|---|---|---|
| Get started from zero | Quickstart: create product, install SDK, create dataset, choose metric, run evaluation | `/quickstart` |
| Define what their product should do | Write specifications, then auto-generate datasets and metrics from them | `/sdk/tutorials/writing-specifications` |
| Run dataset-based evaluations | Create datasets + metrics, run agent against test cases, evaluate | `/sdk/tutorials/run-dataset-based-evaluations` |
| Use specifications to drive everything | Spec-driven flow: specs generate metrics + datasets, then evaluate | `/sdk/tutorials/specification-driven-evaluations` |
| Test multi-turn conversations | Simulate user conversations against the agent, then evaluate sessions | `/sdk/tutorials/simulating-conversations` |
| Evaluate past conversations | Evaluate already-completed multi-turn sessions | `/sdk/tutorials/evaluating-conversations` |
| Monitor production responses | Log real user queries as traces, evaluate asynchronously | `/sdk/tutorials/monitor-production-responses-to-user-queries` |
| Set up human evaluation | Create UserGroups, assign metrics, reviewers claim + score via platform | `/sdk/tutorials/human-evaluation` |
| Trace agent internals | Capture internal tool calls / LLM calls as Span records | `/sdk/tutorials/tracing-agent-operations` |
| Integrate with CI/CD | Run evaluations in GitHub Actions | `/sdk/integrations/github-actions` |

For other workflows (custom datasets, judge prompts, agentic evaluation, custom metrics, platform-only inferences, Langfuse integration, model tracking), grep `llms.txt` for the relevant tutorial.

To fetch any page: `curl -s "https://docs.galtea.ai<path>.md"` or use `WebFetch`.

## CLI vs Python SDK

This skill drives the `galtea` CLI by default -- it covers everything the REST API does. For workflows that involve actually *running* the user's AI product (the agent-function loop, conversation simulation, tracing internal LLM/tool calls, inline production logging), the [Python SDK](https://docs.galtea.ai/sdk/installation) (`pip install galtea`) is usually the better fit. For the decision framework and SDK routing hints, see [references/cli-vs-sdk.md](references/cli-vs-sdk.md). Mixing the CLI and SDK in one project is safe -- they target the same backend.

When wiring an agent function for `galtea.evaluations.run(...)` or the simulator, **annotate the first parameter** -- the SDK picks the argument shape from it (`str` → latest user message, `galtea.AgentInput` → structured input, anything else **including no annotation** → a `list[dict]` chat history). The recommended approach is to use the signature `def my_agent(messages: list[dict]) -> str` or the `galtea.Agent` / `galtea.AgentInput` adapter; an unannotated first parameter silently receives a `list[dict]`, which crashes string-assuming agents. See [references/cli-vs-sdk.md](references/cli-vs-sdk.md) for the full mapping.

## Gotchas

Runtime behaviors that are not in `--help` text -- these are the only items a well-informed agent still needs explicit reminders for.

- **Body-taking commands hang on stdin in non-TTY contexts.** Every POST / PUT / PATCH command (`evaluations create-from-*`, `evaluations retry`, `evaluations replay-from-metrics`, `products create`, `versions update`, …) reads stdin when it's open, even when you provide all required fields via the inline shorthand form. In an interactive terminal this is fine. In scripts, CI jobs, agent harnesses, or piped invocations the command blocks forever -- the parse already succeeded, but the process never returns. Always either redirect stdin (`galtea X create field: value </dev/null`) or pipe a JSON body in (`echo '{...}' | galtea X create`). Symptom: command produces no output and the harness eventually times out.
- **`--sort` takes `field,direction` pairs -- do not use a leading `-` for descending.** On list endpoints, pass sort values like `--sort createdAt,desc`. Do **not** write `--sort -createdAt` -- the CLI parses `-createdAt` as a bundle of short flags, and `-t`/`--timeout` can end up consuming `edAt` as a duration argument, causing the command to fail before the request is sent.
- **`--ids` + any other filter yields false 404s.** Across every `list` endpoint that accepts `--ids` (evaluations, sessions, versions, products, metrics, …), the server compares `entitiesReturned.length` to the requested id count *after* all filtering has run and throws `404 "<Entity>s not found: <id-list>"` when they differ. So combining `--ids id1,id2,id3` with **anything else** -- `--statuses PENDING`, `--from-created-at`, even another id-list filter like `--metric-ids` -- yields a 404 listing every id whose row was excluded by the *other* filter, even though the ids exist. This is especially mean in poll loops: once your evaluations leave `PENDING`, `list --ids "$IDS" --statuses PENDING` flips from `[]` to a 404 error object, and `jq 'length'` on that object returns `1` (one key: `message`), trapping a naive `while … -gt 0` forever. **Relation-id filters (`--version-ids`, `--session-ids`, `--inference-result-ids`, `--product-ids`, `--metric-ids`, `--test-ids`, …) are NOT subject to this check** and combine freely with each other and with `--statuses` / date ranges. Note the flag names come from the wire fields, so `--test-ids` filters by dataset and `--inference-result-ids` filters by trace. Two safe patterns: **(a)** if you have a relation scope (versionId, sessionId, inferenceResultId, productId), filter by `--<scope>-ids` instead of `--ids` -- those combine cleanly with `--statuses`; **(b)** if you only have raw primary-key ids, use `--ids` alone (no other filter) and filter status client-side via `jq '[.[] | select(.status == "...")] | length'`.
- **`--ids` with many IDs triggers a WAF `403`.** `--ids` values are encoded into the query string (`ids%5B%5D=…`), so large id lists produce URLs that the upstream WAF/proxy rejects *before* the application responds -- returning a raw HTML `403 Forbidden` page (not JSON). Because the response is HTML, piping through `jq` also crashes, compounding the failure. The cutoff is low: as few as ~50 evaluation-length IDs (~2.3 KB query string) can trigger the block. Prefer relation-id filters (`--version-ids`, `--product-ids`, `--session-ids`, …) which send a single parent id regardless of how many child rows exist. When you only have raw primary-key ids, **batch requests to ≤ 50 ids each**.
- **Boolean filters are bare flags, not `--flag true`.** CLI booleans like `--has-inference-results`, `--existent-in-evaluations`, `--include-deleted`, `--can-retry` take no value: presence means `true`, absence means `false`. `galtea versions list --has-inference-results true` errors with `accepts 0 arg(s), received 1`. The `--help` output omits a type after the flag name (`--has-inference-results` rather than `--has-inference-results bool`) -- that is the cue.
- **Attaching an existing dataset or metric to a specification is done from the specification side -- do not conclude it's impossible from `datasets update` / `metrics update`.** `specificationId` is settable only at `datasets create`; after that, `datasets update` mutates only `name` and `metadata`, and the `specifications update` payload has no dataset or metric fields either. That is a deliberate dead end, not a missing capability: the link is managed via dedicated verbs -- `galtea specifications link-datasets | unlink-datasets | link-metrics | unlink-metrics` (POST/DELETE `/specifications/{id}/tests` and `/specifications/{id}/metrics`, and the link body field is `testIds`), with `galtea specifications list-datasets` / `galtea specifications list-metrics` to inspect what is currently linked. Note the create/get responses do not echo linked datasets/metrics back -- verify a link took effect via `galtea specifications list-datasets` / `galtea specifications list-metrics`, not by re-reading the specification.
- **Datasets must be `status: SUCCESS`** before an evaluation can run against them. `PENDING` / `AUGMENTING` will be skipped silently. Workflow constraint, not a schema rule.
- **Duplicate names return `400 Bad Request`** (not 409) -- the underlying unique-constraint violation is caught server-side and re-thrown as a bad-request error across every create endpoint (products, versions, datasets, metrics, endpoint connections, user groups, models, sessions, specifications, evaluations). The body `message` consistently contains the substring `"with the same"` followed by the colliding fields, but the surrounding wording varies per entity. Examples: `"A Product with the same Name already exists."`, `"A Test with the same Name and Type already exists."`, `"An EndpointConnection with the same name already exists for this product."`, `"A Specification with the same description and type already exists for this product."`, `"An Evaluation with the same Version and Test already exists."`, `"A Session with the same customId: '...' already exists for version with ID: '...'."`. Those two messages really do still say `Test` -- the server text was not renamed, so match it as quoted. Match on `"with the same"` (case-insensitive) to distinguish unique-constraint violations from other 400s; do not blind-retry.
- **Span rows may have `null` `inputData` / `outputData` / `metadata`** even on valid rows (note the exact field names -- it is `inputData`, not `input`; there is no `attributes` field). Null-guard before reading. Spans are `galtea spans` on the CLI and live at `/traces` on the wire.
- **Credits are consumed** by evaluations and dataset generation only -- reads and auth are free. For a pre-flight check, call `galtea auth get-current-user` to resolve `organizationId`, then `galtea organizations get-credit-status <organizationId>` for `totalCredits` / `usedCredits` / `remainingCredits`. When an org runs out, operations fail with a `message` in the body; there is no dedicated HTTP status for it, so inspect the message rather than matching on a code.
- **Error response shape is stable; coverage in OpenAPI is not.** All error responses conform to `{error: string, message: string}`. `401` is declared on ~every operation, `404` and `400` are declared on many, but `500` and runtime-only codes (credit exhaustion, upstream failures, race conditions) are frequently undeclared. On any non-2xx, read `message` from the body before deciding what to do -- do not rely on the HTTP code alone, and do not assume the spec enumerates everything the server can return.
- **`galtea sync` is needed after API releases.** If `galtea <noun> <verb>` returns "unknown command" but the docs say it exists, the local spec cache is stale -- run `galtea sync` and retry.

## Skill Feedback

When the user expresses that something about this skill is not working as expected, gives incorrect guidance, is missing information, or could be improved -- offer to submit feedback to the skill maintainers. This includes when:

- The skill gave wrong or outdated instructions
- A workflow did not produce the expected result
- The user wishes the skill covered something it does not
- The user explicitly says something like "this should work differently" or "this is wrong"

**Do NOT trigger this** for issues with the Galtea product itself -- only for issues with this skill's instructions and behavior. For product issues, direct the user to `support@galtea.ai`.

When triggered, follow the process in [references/skill-feedback.md](references/skill-feedback.md).

## When not to use this skill

- **Building the AI product itself.** This skill is for *evaluating* products, not authoring them.
- **Pure UI browsing.** If the user just wants to look at results visually, point them at `https://platform.galtea.ai` instead of replaying the CLI chain.
- **Hand-writing test content.** Galtea generates test cases from specifications (see `/sdk/tutorials/writing-specifications`). Let the platform do that work.
