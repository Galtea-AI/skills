---
name: galtea
description: Help users interact with the Galtea platform — the AI product testing and evaluation platform for AI/LLM products. Covers authentication, managing products/versions/specifications/tests/metrics/endpoint-connections/evaluations/sessions/inference-results/traces, and wiring an AI product into Galtea for automated testing.
when_to_use: Invoke when the user mentions Galtea, asks to run or inspect an evaluation, wants to create a product/version/test/metric, needs to debug a failed session or inference, or is trying to connect their AI product to Galtea's testing platform. Trigger phrases include "galtea", "run evaluation", "gsk_...", "testing my AI product", "list my products", "create a test".
allowed-tools: WebFetch WebSearch Bash(curl *) Bash(jq *) Bash(grep *) Bash(cat *) Bash(ls *) Bash(stat *) Bash(mkdir *) Bash(chmod *) Bash(test *) Bash(date *) Bash(find *) Bash(rm *) Bash(echo *) Bash(printf *) Bash(tee *) Bash(env *) Bash(awk *) Bash(sed *) Bash(head *) Bash(tail *) Bash(wc *) Bash(sort *) Bash(uniq *) Bash(xmllint *) Bash(gh *)
---

# Galtea

Galtea is an AI product testing and evaluation platform. Teams use it to define behavioral specifications, generate test datasets, run evaluations (AI-as-judge, deterministic, or human), and iterate their AI products toward production with confidence. Entities follow a hierarchy — `Product → Version → Session → Inference Result → Trace` — with `Test` / `TestCase`, `Metric`, `Specification`, and `EndpointConnection` as companion resources attached to a Product.

This skill helps the agent drive the Galtea REST API on behalf of the user: authenticate, discover the right docs and endpoints, then run or inspect evaluations. Source-of-truth details live in the OpenAPI spec and the docs; this file points at them rather than duplicating them.

If the user is new to Galtea, send them through `https://docs.galtea.ai/quickstart`, then `/sdk/tutorials/writing-specifications`, then `/sdk/tutorials/run-test-based-evaluations` — the shortest zero-to-evaluation path.

## Core Rules

1. **Authenticate before any API call.** If `$GALTEA_API_KEY` is unset and no key is cached at `~/.galtea/api-key`, run the Authentication flow. Never hit the API without a key resolved.
2. **Documentation first — never implement from memory.** Galtea ships frequently; endpoints, metrics, and SDK APIs change. Before you advise on an endpoint, workflow, concept, or metric, fetch the relevant docs page (§Discover) or the exact slice of the OpenAPI spec (§Discover). Examples inlined here are illustrative, not authoritative.
3. **Discover docs via `llms.txt`, then fetch pages as markdown.** The index at `https://docs.galtea.ai/llms.txt` lists every docs page with title, URL, and one-line description. Grep it for the path prefix you need (`/sdk/tutorials/`, `/concepts/`, `/api-reference/`), then fetch the specific page — every URL works with a `.md` suffix (`Content-Type: text/markdown`) for clean content. Do not guess URLs; do not page through `sitemap.xml` when `llms.txt` is available.
4. **OpenAPI is the source of truth for endpoint shapes.** Fetch `https://api.galtea.ai/openapi.json` (OpenAPI 3.0, ~180 KB, security scheme `bearerAuth` — both `gsk_*` and `gsk-*` keys accepted) for exact paths, request bodies, response schemas, enums, and validation constraints. `jq` into the slice you need rather than loading the whole file into context.
5. **Filter query params are usually plural** (`productIds`, `versionIds`, `testIds`, `metricIds`, `inferenceResultIds`), though a few endpoints accept singular. When in doubt, check `parameters` for the endpoint in `openapi.json` before guessing.
6. **Evaluations are async.** Trigger via `POST /evaluations/from{Version,Session,InferenceResult}` (returns `202` with no body); list `/evaluations?...&statuses=PENDING` to fetch the created IDs, then poll `GET /evaluations/{id}` until `status` reaches `SUCCESS`, `FAILED`, or `SKIPPED`. `PENDING_HUMAN` means the evaluation is waiting for a human reviewer — stop polling and surface it to the user.
7. **Soft deletes.** Deleted rows have `deletedAt` set; list endpoints exclude them by default.

## Environment

| Variable | Purpose |
|---|---|
| `GALTEA_API_KEY` | `gsk_*` bearer token scoped to the user's Galtea organization. Unset by default — see Authentication. |

The changelog at `https://docs.galtea.ai/changelog` lists every new metric, endpoint, and feature by date — consult it when the user asks about something recent.

**Shell assumption.** The snippets in this skill target a POSIX shell (macOS, Linux, WSL, Git Bash). They rely on `jq`, `grep`, `stat`, `chmod`, and Bash substitutions that don't work in native PowerShell or `cmd`. Windows users on native PowerShell should install WSL or Git Bash, or switch to the Python SDK — install instructions at **https://docs.galtea.ai/sdk/installation** — which is fully cross-platform.

## Authentication

Galtea uses bearer-token auth. Every request includes `-H "Authorization: Bearer $GALTEA_API_KEY"`.

**Whenever you're about to make a Galtea API call**, start the bash call with this resolver block — it sets up the URL helpers and loads the cached key, so the rest of the call can use `$GALTEA_API_URL`, `$GALTEA_DOCS_URL`, and `$GALTEA_API_KEY` freely:

```bash
GALTEA_API_URL="${GALTEA_API_URL:-https://api.galtea.ai}"
GALTEA_DOCS_URL="${GALTEA_DOCS_URL:-https://docs.galtea.ai}"
GALTEA_API_KEY="${GALTEA_API_KEY:-$(cat ~/.galtea/api-key 2>/dev/null)}"
```

If that leaves `$GALTEA_API_KEY` empty, run the paste-and-validate flow below. Claude Code's Bash tool resets shell state between calls, so you must run this resolver at the top of every Galtea bash call — do not assume a prior `export` persists.

### First-time paste flow

When no key is available:

1. Tell the user: *"Open https://platform.galtea.ai → **Settings** → **API Key** section. Copy your existing key (starts with `gsk_`), or click **Generate API Key** if you don't have one. Each account has a single key — regenerating permanently replaces it. Paste it here as plain text."*
2. Receive the pasted value as sensitive free-text. Do **not** use `AskUserQuestion` for this — pasting secrets into option metadata leaks them into logs.
3. Cache the key at `~/.galtea/api-key` with file mode `600` (readable only by your OS user):
   ```bash
   mkdir -p ~/.galtea && printf '%s' "$PASTED_KEY" > ~/.galtea/api-key && chmod 600 ~/.galtea/api-key
   ```
4. Validate by hitting the credit-free `/organizations` endpoint (reads never consume credits) — run the resolver block (above), then:
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

Both `llms.txt` and the OpenAPI spec are large; cache them under `/tmp` with a 24-hour TTL so you don't re-download each turn. Prepend the resolver block from Authentication to every bash call below.

**Tool preference for doc fetching.** If your host agent provides `WebFetch` / `WebSearch` (Claude Code, Cursor, etc.), prefer them over `curl` — they handle summarization, caching, and large-page trimming for free. Use `curl` when you need raw bytes for a `jq` pipeline, when caching to `/tmp`, or when no native fetch tool is available.

### Docs index (`llms.txt`)

```bash
# Refresh if missing or older than 24h
if [ ! -f /tmp/galtea-llms.txt ] || \
   [ $(( $(date +%s) - $(stat -c %Y /tmp/galtea-llms.txt 2>/dev/null || echo 0) )) -gt 86400 ]; then
  curl -s "$GALTEA_DOCS_URL/llms.txt" > /tmp/galtea-llms.txt
fi

# Find the entries you need — the index is a markdown list of
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
# Refresh if missing or older than 24h
if [ ! -f /tmp/galtea-openapi.json ] || \
   [ $(( $(date +%s) - $(stat -c %Y /tmp/galtea-openapi.json 2>/dev/null || echo 0) )) -gt 86400 ]; then
  curl -s "$GALTEA_API_URL/openapi.json" > /tmp/galtea-openapi.json
fi

jq '.paths | keys[]'                           /tmp/galtea-openapi.json   # every endpoint path
jq '.paths."/evaluations/fromVersion".post'    /tmp/galtea-openapi.json   # one operation spec
jq '.components.schemas.Evaluation'            /tmp/galtea-openapi.json   # reusable schema
jq '.components.securitySchemes'               /tmp/galtea-openapi.json   # auth schemes
```

**OpenAPI 3.0 in one paragraph.** `.paths.<path>.<method>` describes one operation (its `parameters`, `requestBody`, `responses`). Request/response shapes use `$ref` pointers into `.components.schemas.<Name>`. Resolve references with `jq` incrementally rather than loading the whole spec into context.

## Worked example — evaluate a product version

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
#    Returns 202 with no body — jobs are created asynchronously.
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

Alternative creation endpoints: `POST /evaluations/fromSession` (needs `sessionId`, optional `metrics`, `specificationIds`), `POST /evaluations/fromInferenceResult` (needs `inferenceResultId`, same optionals), `POST /evaluations/singleTurn`, `POST /evaluations/batch`, `POST /evaluations/retry` (re-run failed). Check the OpenAPI body schema before calling.

Other end-to-end flows — creating a product, linking an endpoint connection, simulating conversations, tracing an agentic system, human evaluation, production monitoring — are covered by tutorials under `https://docs.galtea.ai/sdk/tutorials/*`. Fetch the specific one via `llms.txt` rather than reinventing it here.

## Python alternative

Galtea ships an official Python SDK that wraps the same API. If the user prefers Python (or is on a shell where the Bash snippets above don't run), install it with `pip install galtea` and follow the installation guide at **https://docs.galtea.ai/sdk/installation** plus the tutorials under `https://docs.galtea.ai/sdk/tutorials/*`. The SDK and the REST API target the same endpoints, so mixing them in the same project is safe.

## Gotchas

Runtime behaviors that aren't documented in the OpenAPI spec — these are the only items a well-informed agent still needs explicit reminders for.

- **Tests must be `status: SUCCESS`** before an evaluation can run against them. `PENDING` / `AUGMENTING` will fail. Workflow constraint, not a schema rule.
- **Duplicate names return `400 Bad Request`** (not 409) — the underlying unique-constraint violation is caught server-side and re-thrown as a bad-request error across every create endpoint (products, versions, tests, metrics, endpoint connections, user groups, models, evaluations). The body's `message` follows `"A <Entity> with the same Name [and Type]? already exists..."` — match on that substring to distinguish it from other 400s; don't blind-retry.
- **Trace rows may have `null` `inputData` / `outputData` / `metadata`** even on valid rows (note the exact field names — it's `inputData`, not `input`; there is no `attributes` field). Null-guard before reading.
- **Credits are consumed** by evaluations and test generation only — reads and auth are free. `GET /organizations` returns `remainingSubscriptionCredits` + `extraCredits` for a pre-flight check. When an org runs out, operations fail with a `message` in the body; there's no dedicated HTTP status for it, so inspect the message rather than matching on a code.
- **Error response shape is stable; coverage in OpenAPI isn't.** All error responses conform to `#/components/schemas/Error` (`{error: string, message: string}`). `401` is declared on ~every operation, `404` and `400` are declared on many, but `500` and runtime-only codes (credit exhaustion, upstream failures, race conditions) are frequently undeclared. On any non-2xx, read `message` from the body before deciding what to do — don't rely on the HTTP code alone, and don't assume the spec enumerates everything the server can return.

## Skill Feedback

When the user expresses that something about this skill is not working as expected, gives incorrect guidance, is missing information, or could be improved — offer to submit feedback to the skill maintainers. This includes when:

- The skill gave wrong or outdated instructions
- A workflow didn't produce the expected result
- The user wishes the skill covered something it doesn't
- The user explicitly says something like "this should work differently" or "this is wrong"

**Do NOT trigger this** for issues with the Galtea product itself — only for issues with this skill's instructions and behavior. For product issues, direct the user to `support@galtea.ai`.

When triggered, follow the process in [references/skill-feedback.md](references/skill-feedback.md).

## When not to use this skill

- **Building the AI product itself.** This skill is for *evaluating* products, not authoring them.
- **Pure UI browsing.** If the user just wants to look at results visually, point them at `https://platform.galtea.ai` instead of replaying the curl chain.
- **Hand-writing test content.** Galtea generates test cases from specifications (see `/sdk/tutorials/writing-specifications`). Let the platform do that work.
