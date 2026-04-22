---
name: galtea
description: Help users interact with the Galtea platform -- the AI product testing and evaluation platform for AI/LLM products. Covers authentication, managing products/versions/specifications/tests/metrics/endpoint-connections/evaluations/sessions/inference-results/traces, and wiring an AI product into Galtea for automated testing.
when_to_use: Invoke when the user mentions Galtea, asks to run or inspect an evaluation, wants to create a product/version/test/metric, needs to debug a failed session or inference, or is trying to connect their AI product to Galtea's testing platform. Trigger phrases include "galtea", "run evaluation", "gsk_...", "testing my AI product", "list my products", "create a test".
allowed-tools: WebFetch WebSearch Bash(curl *) Bash(jq *) Bash(grep *) Bash(cat *) Bash(ls *) Bash(mkdir *) Bash(chmod *) Bash(test *) Bash(date *) Bash(find *) Bash(rm -f ~/.galtea/api-key) Bash(echo *) Bash(printf *) Bash(tee *) Bash(env *) Bash(awk *) Bash(sed *) Bash(head *) Bash(tail *) Bash(wc *) Bash(sort *) Bash(uniq *) Bash(xmllint *) Bash(gh *)
---

# Galtea

Galtea is an AI product testing and evaluation platform. Teams use it to define behavioral specifications, generate test datasets, run evaluations (AI-as-judge, deterministic, or human), and iterate their AI products toward production with confidence.

This skill helps the agent drive the Galtea REST API and advise on the Python SDK on behalf of the user: authenticate, discover the right docs and endpoints, then run or inspect evaluations. Source-of-truth details live in the OpenAPI spec and the docs; this file points at them rather than duplicating them.

If the user is new to Galtea, send them through `https://docs.galtea.ai/quickstart`, then `https://docs.galtea.ai/sdk/tutorials/writing-specifications`, then `https://docs.galtea.ai/sdk/tutorials/run-test-based-evaluations` -- the shortest zero-to-evaluation path.

## Quick Links

| Resource | URL | When to use |
|---|---|---|
| Docs index (LLM-optimized) | `https://docs.galtea.ai/llms.txt` | First stop for discovering docs pages. Grep for `/sdk/tutorials/`, `/concepts/`, `/api-reference/`. |
| Full docs dump | `https://docs.galtea.ai/llms-full.txt` | When you need all docs content in one fetch (large). |
| OpenAPI spec | `https://api.galtea.ai/openapi.json` | Source of truth for endpoint paths, request/response schemas, and enums. Use `jq` to slice. |
| Changelog | `https://docs.galtea.ai/changelog` | Check for recent metrics, endpoints, or feature changes. |
| Quickstart guide | `https://docs.galtea.ai/quickstart` | Onboard new users. |
| SDK installation | `https://docs.galtea.ai/sdk/installation` | Python SDK setup (`pip install galtea`). |
| Platform UI | `https://platform.galtea.ai` | Where users manage products, view results, and generate API keys. |
| Product support | `mailto:support@galtea.ai` | For Galtea product issues (not skill issues). |

Any docs page URL works with a `.md` suffix (e.g. `https://docs.galtea.ai/quickstart.md`) to get clean markdown content.

## How Galtea Works

### Entity hierarchy

Entities follow a hierarchy. Understanding it is essential for picking the right API calls.

```
Product
 |- Version              (an iteration of the product to compare over time)
 |   +- Session          (a full conversation: one or more turns)
 |       |- Inference Result   (a single user-turn + AI-response pair)
 |       |   |- Trace          (internal agent operations for that turn)
 |       |   +- Evaluation     (score for a single turn; created via fromInferenceResult)
 |       +- Evaluation         (score for the whole conversation; created via fromSession)
 |- Specification        (a behavioral rule the product should follow)
 |   |- links to Metrics (how to score compliance with this spec)
 |   +- links to Tests   (test data to exercise this spec)
 |- Test                 (a dataset of TestCases)
 |   +- TestCase         (one input scenario; may include ground truth)
 |- Metric               (scoring criteria: AI-judge prompt, deterministic, or human)
 |- EndpointConnection   (URL + auth to call the user AI product from the platform)
 +- Model                (an LLM model tracked for a product)
```

**Evaluations attach at the turn level (InferenceResult) or the conversation level (Session).** `fromVersion` orchestrates both by cascading across the version's tests and creating evaluations at the leaf level. See "Evaluation creation paths" below for the full routing table.

**Specifications are the glue.** They connect metrics (how to score) with tests (what to score against). When the user triggers `fromVersion`, Galtea resolves all specifications for the product, finds their linked metrics and tests, and runs evaluations automatically.

### Three evaluation approaches

| Approach | When to use | Key concept |
|---|---|---|
| **Test-based** | Pre-deployment regression testing against synthetic or curated datasets | Create Tests + TestCases, run agent against them, evaluate |
| **Conversation simulation** | Test multi-turn behavior with simulated users | Galtea simulator plays the user role, agent responds, then evaluate the session |
| **Production monitoring** | Evaluate real user interactions after deployment | Log inference results from production, evaluate them asynchronously |

### Metric types (verify against docs)

- **AI-as-judge** (`FULL_PROMPT` / `PARTIAL_PROMPT`): An LLM scores the output using a judge prompt. Most common.
- **Human evaluation** (`HUMAN_EVALUATION`): A human reviewer scores via the platform. Requires UserGroups.
- **Deterministic**: Rule-based scoring (exact match, regex, etc.).
- **Custom**: User-defined metrics with their own scoring logic.

Fetch `/concepts/metrics` from the docs for the current, authoritative list.

## Core Rules

1. **Authenticate before any API call.** If `$GALTEA_API_KEY` is unset and no key is cached at `~/.galtea/api-key`, run the Authentication flow. Never hit the API without a key resolved.
2. **Documentation first -- never implement from memory.** Galtea ships frequently; endpoints, metrics, and SDK APIs change. Before you advise on an endpoint, workflow, concept, or metric, fetch the relevant docs page (see Discover docs and endpoints) or the exact slice of the OpenAPI spec (see Discover docs and endpoints). Examples inlined here are illustrative, not authoritative.
3. **Discover docs via `llms.txt`, then fetch pages as markdown.** The index at `https://docs.galtea.ai/llms.txt` lists every docs page with title, URL, and one-line description. Grep it for the path prefix you need (`/sdk/tutorials/`, `/concepts/`, `/api-reference/`), then fetch the specific page -- every URL works with a `.md` suffix (`Content-Type: text/markdown`) for clean content. Do not guess URLs; do not page through `sitemap.xml` when `llms.txt` is available.
4. **OpenAPI is the source of truth for endpoint shapes.** Fetch `https://api.galtea.ai/openapi.json` (OpenAPI 3.0, ~180 KB, security scheme `bearerAuth` -- both `gsk_*` and `gsk-*` keys accepted) for exact paths, request bodies, response schemas, enums, and validation constraints. `jq` into the slice you need rather than loading the whole file into context.
5. **Filter query params are usually plural** (`productIds`, `versionIds`, `testIds`, `metricIds`, `inferenceResultIds`), though a few endpoints accept singular. When in doubt, check `parameters` for the endpoint in `openapi.json` before guessing.
6. **Evaluations are async.** Trigger via `POST /evaluations/from{Version,Session,InferenceResult}` (returns `202` with no body); list `/evaluations?...&statuses=PENDING` to fetch the created IDs, then poll `GET /evaluations/{id}` until `status` reaches `SUCCESS`, `FAILED`, or `SKIPPED`. `PENDING_HUMAN` means the evaluation is waiting for a human reviewer -- stop polling and surface it to the user.
7. **Soft deletes.** Deleted rows have `deletedAt` set; list endpoints exclude them by default.

## Environment

| Variable | Purpose |
|---|---|
| `GALTEA_API_KEY` | `gsk_*` bearer token scoped to the user's Galtea organization. Unset by default -- see Authentication. |

The changelog at `https://docs.galtea.ai/changelog` lists every new metric, endpoint, and feature by date -- consult it when the user asks about something recent.

**Shell assumption.** The snippets in this skill target a POSIX shell (macOS, Linux, WSL, Git Bash). They rely on `jq`, `grep`, `find`, `chmod`, and Bash substitutions that do not work in native PowerShell or `cmd`. Windows users on native PowerShell should install WSL or Git Bash, or switch to the Python SDK -- install instructions at **https://docs.galtea.ai/sdk/installation** -- which is fully cross-platform.

## Authentication

Galtea uses bearer-token auth. Every request includes `-H "Authorization: Bearer $GALTEA_API_KEY"`.

**Whenever you are about to make a Galtea API call**, start the bash call with this resolver block -- it sets up the URL helpers and loads the cached key, so the rest of the call can use `$GALTEA_API_URL`, `$GALTEA_DOCS_URL`, and `$GALTEA_API_KEY` freely:

```bash
GALTEA_API_URL="${GALTEA_API_URL:-https://api.galtea.ai}"
GALTEA_DOCS_URL="${GALTEA_DOCS_URL:-https://docs.galtea.ai}"
GALTEA_API_KEY="${GALTEA_API_KEY:-$(cat ~/.galtea/api-key 2>/dev/null)}"
```

If that leaves `$GALTEA_API_KEY` empty, run the paste-and-validate flow below. Claude Code's Bash tool resets shell state between calls, so you must run this resolver at the top of every Galtea bash call -- do not assume a prior `export` persists.

### First-time paste flow

When no key is available:

1. Tell the user: *"Open https://platform.galtea.ai, go to **Settings**, then **API Key** section. Copy your existing key (starts with `gsk_`), or click **Generate API Key** if you do not have one. Each account has a single key -- regenerating permanently replaces it. Paste it here as plain text."*
2. Receive the pasted value as sensitive free-text. Do **not** use `AskUserQuestion` for this -- pasting secrets into option metadata leaks them into logs.
3. Cache the key at `~/.galtea/api-key` with file mode `600` (readable only by your OS user):
   ```bash
   mkdir -p ~/.galtea && printf '%s' "$PASTED_KEY" > ~/.galtea/api-key && chmod 600 ~/.galtea/api-key
   ```
4. Validate by hitting the credit-free `/organizations` endpoint (reads never consume credits) -- run the resolver block (above), then:
   ```bash
   curl -s -H "Authorization: Bearer $GALTEA_API_KEY" "$GALTEA_API_URL/organizations" \
     | jq '.[0] | {id, name, remainingSubscriptionCredits, extraCredits}'
   ```
5. On success, report the organization name and remaining credits to the user. On `401`, tell them the key is invalid and loop back to step 1 (up to 3 tries before giving up).

### On 401 from a previously cached key

The key was rotated or revoked. Clear the cache and re-run the paste flow:

```bash
rm -f ~/.galtea/api-key
```

## Discover docs and endpoints

Both `llms.txt` and the OpenAPI spec are large; cache them under `/tmp` with a 24-hour TTL so you do not re-download each turn. Prepend the resolver block from Authentication to every bash call below (auth key is not strictly required for these unauthenticated fetches, but the URL defaults are).

**Tool preference for doc fetching.** If your host agent provides `WebFetch` / `WebSearch` (Claude Code, Cursor, etc.), prefer them over `curl` -- they handle summarization, caching, and large-page trimming for free. Use `curl` when you need raw bytes for a `jq` pipeline, when caching to `/tmp`, or when no native fetch tool is available.

### Docs index (`llms.txt`)

```bash
# Refresh if missing or older than 24h (find -mtime +1 works on GNU and BSD find)
if [ ! -f /tmp/galtea-llms.txt ] || \
   [ -n "$(find /tmp/galtea-llms.txt -mtime +1 2>/dev/null)" ]; then
  curl -s "$GALTEA_DOCS_URL/llms.txt" > /tmp/galtea-llms.txt
fi

# Find the entries you need -- the index is a markdown list of
#   - [Title](URL.md): One-line description
grep '/sdk/tutorials/'  /tmp/galtea-llms.txt   # tutorials (end-to-end playbooks)
grep '/concepts/'       /tmp/galtea-llms.txt   # entity definitions + hierarchy
grep '/api-reference/'  /tmp/galtea-llms.txt   # per-endpoint reference pages

# Fetch one specific page as clean markdown (append .md to any page URL)
curl -s "$GALTEA_DOCS_URL/sdk/tutorials/run-test-based-evaluations.md"

# Fetch everything at once (large but sometimes useful for long-context agents)
curl -s "$GALTEA_DOCS_URL/llms-full.txt"
```

For end-to-end playbooks (creating a product, simulating conversations, tracing an agent, human evaluation, production monitoring) look under `/sdk/tutorials/`. For entity definitions and the hierarchy between them look under `/concepts/`. For per-endpoint reference pages look under `/api-reference/`.

### OpenAPI spec

```bash
# Refresh if missing or older than 24h (find -mtime +1 works on GNU and BSD find)
if [ ! -f /tmp/galtea-openapi.json ] || \
   [ -n "$(find /tmp/galtea-openapi.json -mtime +1 2>/dev/null)" ]; then
  curl -s "$GALTEA_API_URL/openapi.json" > /tmp/galtea-openapi.json
fi

jq '.paths | keys[]'                           /tmp/galtea-openapi.json   # every endpoint path
jq '.paths."/evaluations/fromVersion".post'    /tmp/galtea-openapi.json   # one operation spec
jq '.components.schemas.Evaluation'            /tmp/galtea-openapi.json   # reusable schema
jq '.components.securitySchemes'               /tmp/galtea-openapi.json   # auth schemes
```

**OpenAPI 3.0 in one paragraph.** `.paths.<path>.<method>` describes one operation (its `parameters`, `requestBody`, `responses`). Request/response shapes use `$ref` pointers into `.components.schemas.<Name>`. Resolve references with `jq` incrementally rather than loading the whole spec into context.

## Worked example -- evaluate a product version

End-to-end flow. Each numbered step runs in its own bash call, so start every call with the resolver block from the Authentication section (omitted below for brevity):

```bash
# Resolver (run this at the top of every Galtea bash call)
GALTEA_API_URL="${GALTEA_API_URL:-https://api.galtea.ai}"
GALTEA_DOCS_URL="${GALTEA_DOCS_URL:-https://docs.galtea.ai}"
GALTEA_API_KEY="${GALTEA_API_KEY:-$(cat ~/.galtea/api-key 2>/dev/null)}"
```

```bash
# 1. Find the product
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/products" | jq '.[] | {id, name}'

# 2. Find the version to evaluate
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/versions?productIds=<productId>" | jq '.[] | {id, name}'

# 3. Kick off evaluations for the whole version. Galtea resolves the product's
#    specifications, their linked metrics, and their linked tests automatically.
#    Returns 202 with no body -- jobs are created asynchronously.
curl -s -X POST -H "Authorization: Bearer $GALTEA_API_KEY" -H "Content-Type: application/json" \
  "$GALTEA_API_URL/evaluations/fromVersion" \
  -d '{"versionId":"<versionId>"}'

# 4. List the created evaluations to grab their IDs (the POST returned no body)
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/evaluations?versionIds=<versionId>&statuses=PENDING&sort=-createdAt&limit=20" \
  | jq '.[] | {id, metricId, status}'

# 5. Poll one until it settles (SUCCESS / FAILED / SKIPPED)
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/evaluations/<evalId>" | jq '{id, status, score, reason}'
```

### Evaluation creation paths

The worked example above uses `fromVersion`. Choose the right path based on what the user wants to evaluate:

| User goal | Endpoint | Key input | Notes |
|---|---|---|---|
| Evaluate all tests for a version at once | `POST /evaluations/fromVersion` | `versionId` | Resolves specs, metrics, and tests automatically |
| Evaluate a specific conversation | `POST /evaluations/fromSession` | `sessionId` | Optional: `metrics`, `specificationIds` to narrow scope |
| Evaluate a single turn (production monitoring) | `POST /evaluations/fromInferenceResult` | `inferenceResultId` | Optional: `metrics`, `specificationIds` |
| Quick one-off evaluation without sessions | `POST /evaluations/singleTurn` | Inline input + metric | No session/version setup needed |
| Bulk evaluate many items | `POST /evaluations/batch` | Array of evaluation items | Check OpenAPI for the batch body schema |
| Re-run failed evaluations | `POST /evaluations/retry` | `evaluationIds` | Only retries evaluations with `FAILED` status |

All creation endpoints return `202` with no body. List evaluations afterward to get the created IDs (see step 4 above).

## Common Workflows

Each workflow below maps to a docs page. Fetch the page via `llms.txt` before advising -- the skill provides routing, not the full procedure.

| User wants to... | Workflow | Docs path |
|---|---|---|
| Get started from zero | Quickstart: create product, install SDK, create test, choose metric, run evaluation | `/quickstart` |
| Define what their product should do | Write specifications, then auto-generate tests and metrics from them | `/sdk/tutorials/writing-specifications` |
| Run test-based evaluations | Create tests + metrics, run agent against test cases, evaluate | `/sdk/tutorials/run-test-based-evaluations` |
| Use specifications to drive everything | Spec-driven flow: specs generate metrics + tests, then evaluate | `/sdk/tutorials/specification-driven-evaluations` |
| Test multi-turn conversations | Simulate user conversations against the agent, then evaluate sessions | `/sdk/tutorials/simulating-conversations` |
| Evaluate past conversations | Evaluate already-completed multi-turn sessions | `/sdk/tutorials/evaluating-conversations` |
| Monitor production responses | Log real user queries as inference results, evaluate asynchronously | `/sdk/tutorials/monitor-production-responses-to-user-queries` |
| Set up human evaluation | Create UserGroups, assign metrics, reviewers claim + score via platform | `/sdk/tutorials/human-evaluation` |
| Trace agent internals | Capture internal tool calls / LLM calls as Trace records | `/sdk/tutorials/tracing-agent-operations` |
| Integrate with CI/CD | Run evaluations in GitHub Actions | `/sdk/integrations/github-actions` |

For other workflows (custom test datasets, judge prompts, agentic evaluation, custom metrics, platform-only inferences, Langfuse integration, model tracking), grep `llms.txt` for the relevant tutorial.

To fetch any page: `curl -s "https://docs.galtea.ai<path>.md"` or use `WebFetch`.

## REST API vs Python SDK

This skill can execute REST API calls via `curl` directly. For Python SDK guidance, fetch the relevant tutorial or SDK API reference page and advise the user based on that content.

**When to recommend the REST API (curl):**
- Quick one-off queries (list products, check evaluation status)
- Debugging or inspecting API responses directly
- Environments where Python is not available

**When to recommend the Python SDK (`pip install galtea`):**
- Any workflow that involves running the user agent (the SDK handles the agent function loop)
- Conversation simulation
- Tracing agent internals
- Production monitoring with inline logging
- The user is already writing Python

**Key SDK capabilities** (fetch the relevant `/sdk/api/*` page in `llms.txt` before advising on the exact method -- identifiers below are routing hints only, not canonical names):

- **Agent function** -- the user's AI product, wrapped by the SDK. Multiple signatures are auto-detected; check the installation docs or `/sdk/api/*` for the current list.
- **Simulator** -- plays the user role across multi-turn conversations, calling the agent each turn.
- **Tracing** -- captures internal agent operations (tool calls, LLM calls) as Trace records; both decorator and context-manager forms are available.
- **Inference generation** -- single-call utility that runs the agent and logs the inference result in one step.

SDK API reference pages live under `/sdk/api/*` in `llms.txt`. The SDK and the REST API target the same backend, so mixing them in the same project is safe.

## Gotchas

Runtime behaviors that are not documented in the OpenAPI spec -- these are the only items a well-informed agent still needs explicit reminders for.

- **Tests must be `status: SUCCESS`** before an evaluation can run against them. `PENDING` / `AUGMENTING` will fail. Workflow constraint, not a schema rule.
- **Duplicate names return `400 Bad Request`** (not 409) -- the underlying unique-constraint violation is caught server-side and re-thrown as a bad-request error across every create endpoint (products, versions, tests, metrics, endpoint connections, user groups, models, evaluations). The body `message` follows `"A <Entity> with the same Name [and Type]? already exists..."` -- match on that substring to distinguish it from other 400s; do not blind-retry.
- **Trace rows may have `null` `inputData` / `outputData` / `metadata`** even on valid rows (note the exact field names -- it is `inputData`, not `input`; there is no `attributes` field). Null-guard before reading.
- **Credits are consumed** by evaluations and test generation only -- reads and auth are free. `GET /organizations` returns `remainingSubscriptionCredits` + `extraCredits` for a pre-flight check. When an org runs out, operations fail with a `message` in the body; there is no dedicated HTTP status for it, so inspect the message rather than matching on a code.
- **Error response shape is stable; coverage in OpenAPI is not.** All error responses conform to `#/components/schemas/Error` (`{error: string, message: string}`). `401` is declared on ~every operation, `404` and `400` are declared on many, but `500` and runtime-only codes (credit exhaustion, upstream failures, race conditions) are frequently undeclared. On any non-2xx, read `message` from the body before deciding what to do -- do not rely on the HTTP code alone, and do not assume the spec enumerates everything the server can return.

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
- **Pure UI browsing.** If the user just wants to look at results visually, point them at `https://platform.galtea.ai` instead of replaying the curl chain.
- **Hand-writing test content.** Galtea generates test cases from specifications (see `/sdk/tutorials/writing-specifications`). Let the platform do that work.
