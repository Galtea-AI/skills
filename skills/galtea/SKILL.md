---
name: galtea
description: Help users interact with the Galtea platform — the AI product testing and evaluation platform for AI/LLM products. Covers authentication, managing products/versions/specifications/tests/metrics/endpoint-connections/evaluations/sessions/inference-results/traces, and wiring an AI product into Galtea for automated testing.
when_to_use: Invoke when the user mentions Galtea, asks to run or inspect an evaluation, wants to create a product/version/test/metric, needs to debug a failed session or inference, or is trying to connect their AI product to Galtea's testing platform. Trigger phrases include "galtea", "run evaluation", "gsk_...", "testing my AI product", "list my products", "create a test".
allowed-tools: WebFetch Bash(curl *) Bash(jq *) Bash(grep *) Bash(cat *) Bash(ls *) Bash(stat *) Bash(mkdir *) Bash(chmod *) Bash(test *) Bash(date *) Bash(find *) Bash(rm *) Bash(echo *) Bash(printf *) Bash(tee *) Bash(env *) Bash(awk *) Bash(sed *) Bash(head *) Bash(tail *) Bash(wc *) Bash(sort *) Bash(uniq *) Bash(xmllint *)
---

# Galtea

Galtea is an AI product testing and evaluation platform. Teams use it to define behavioral specifications, generate test datasets, run evaluations (AI-as-judge, deterministic, or human), and iterate their AI products toward production with confidence. Entities follow a hierarchy — `Product → Version → Session → Inference Result → Trace` — with `Test` / `TestCase`, `Metric`, `Specification`, and `EndpointConnection` as companion resources attached to a Product.

This skill helps the agent drive the Galtea REST API on behalf of the user: authenticate, discover the right docs and endpoints, then run or inspect evaluations. Source-of-truth details live in the OpenAPI spec and the docs; this file points at them rather than duplicating them.

If the user is new to Galtea, send them through `$GALTEA_DOCS_URL/quickstart`, then `/sdk/tutorials/writing-specifications`, then `/sdk/tutorials/run-test-based-evaluations` — the shortest zero-to-evaluation path.

## Core Rules

1. **Authenticate before any API call.** If `$GALTEA_API_KEY` is unset and no key is cached at `~/.galtea/api-key`, run the Authentication flow. Never hit the API without a key resolved.
2. **Use the docs sitemap to find pages — do not guess URLs.** The sitemap at `$GALTEA_DOCS_URL/sitemap.xml` lists every docs page. When the user asks about a concept, a workflow, or an endpoint, fetch the sitemap once per session, grep it for the relevant prefix (`/concepts/*`, `/sdk/tutorials/*`, `/api-reference/*`), and fetch only the pages you actually need.
3. **The OpenAPI spec is the source of truth for endpoint shapes.** Fetch `$GALTEA_API_URL/openapi.json` (OpenAPI 3.0, ~900 KB, security scheme `bearerAuth`) and consult it for exact paths, request bodies, response schemas, enums, and validation constraints. `jq` into the slice you need rather than loading the whole file into context.
4. **Rely on the docs for workflows and concepts.** For end-to-end playbooks (setting up a product, simulating conversations, tracing an agent, human evaluation, production monitoring), read a page under `/sdk/tutorials/*`. For entity definitions and the hierarchy between them, read `/concepts/*`. Treat the docs as canonical over anything inlined here.
5. **Filter query params are usually plural** (`productIds`, `versionIds`, `testIds`, `metricIds`, `inferenceResultIds`), though a few endpoints accept singular. When in doubt, check `parameters` for the endpoint in `openapi.json` before guessing.
6. **Evaluations are async.** After `POST /evaluations`, poll `GET /evaluations/{id}` until `status` leaves `PENDING`.
7. **Soft deletes.** Deleted rows have `deletedAt` set; list endpoints exclude them by default.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `GALTEA_API_URL` | `https://api.galtea.ai` | Base URL for API calls and the OpenAPI spec. Override to `https://dev.api.galtea.ai` for Galtea's development environment. |
| `GALTEA_DOCS_URL` | `https://docs.galtea.ai` | Base URL for docs, the sitemap, and the changelog. |
| `GALTEA_API_KEY` | *(unset — see Authentication)* | `gsk_*` bearer token scoped to the user's Galtea organization. |

The changelog at `$GALTEA_DOCS_URL/changelog` lists every new metric, endpoint, and feature by date — consult it when the user asks about something recent.

**Shell assumption.** The snippets in this skill target a POSIX shell (macOS, Linux, WSL, Git Bash). They rely on `jq`, `grep`, `stat`, `chmod`, and Bash substitutions that don't work in native PowerShell or `cmd`. Windows users on native PowerShell should install WSL or Git Bash, or switch to the Python SDK — install instructions at **https://docs.galtea.ai/sdk/installation** — which is fully cross-platform.

## Authentication

Galtea uses bearer-token auth. Every request includes `-H "Authorization: Bearer $GALTEA_API_KEY"`.

**Whenever you're about to make a Galtea API call**, start the bash call with this resolver block — it fills in defaults for the URL vars and loads the cached key, so the rest of the call can use `$GALTEA_API_URL`, `$GALTEA_DOCS_URL`, and `$GALTEA_API_KEY` freely:

```bash
GALTEA_API_URL="${GALTEA_API_URL:-https://api.galtea.ai}"
GALTEA_DOCS_URL="${GALTEA_DOCS_URL:-https://docs.galtea.ai}"
GALTEA_API_KEY="${GALTEA_API_KEY:-$(cat ~/.galtea/api-key 2>/dev/null)}"
```

If that leaves `$GALTEA_API_KEY` empty, run the paste-and-validate flow below. Claude Code's Bash tool resets shell state between calls, so you must run this resolver at the top of every Galtea bash call — do not assume a prior `export` persists.

### First-time paste flow

When no key is available:

1. Tell the user: *"Open the Galtea Dashboard at https://platform.galtea.ai → **Settings → API Keys** → create (or copy) a key starting with `gsk_`. Paste it in the chat as plain text."*
2. Receive the pasted value as sensitive free-text. Do **not** use `AskUserQuestion` for this — pasting secrets into option metadata leaks them into logs.
3. Persist the key to `~/.galtea/api-key` with owner-only permissions (standard credential-file pattern):
   ```bash
   mkdir -p ~/.galtea && printf '%s' "$PASTED_KEY" > ~/.galtea/api-key && chmod 600 ~/.galtea/api-key
   ```
4. Validate by hitting the credit-free `/organizations` endpoint (reads never consume credits) — run the resolver block (above), then:
   ```bash
   curl -s -H "Authorization: Bearer $GALTEA_API_KEY" "$GALTEA_API_URL/organizations" \
     | jq '.data[0] | {id, name, remainingSubscriptionCredits, extraCredits}'
   ```
5. On success, report the organization name and remaining credits to the user. On `401`, tell them the key is invalid and loop back to step 1 (up to 3 tries before giving up).

### On 401 from a previously cached key

The key was rotated or revoked. Clear the cache and re-run the paste flow:

```bash
rm -f ~/.galtea/api-key
```

## Discover docs and endpoints

Both the sitemap and the OpenAPI spec are large; cache them under `/tmp` with a 24-hour TTL so you don't re-download each turn. Prepend the resolver block from Authentication to every bash call below (auth key is not strictly required for these unauthenticated fetches, but the URL defaults are).

### Docs sitemap

```bash
GALTEA_DOCS_URL="${GALTEA_DOCS_URL:-https://docs.galtea.ai}"

# Refresh if missing or older than 24h
if [ ! -f /tmp/galtea-sitemap.xml ] || \
   [ $(( $(date +%s) - $(stat -c %Y /tmp/galtea-sitemap.xml 2>/dev/null || echo 0) )) -gt 86400 ]; then
  curl -s "$GALTEA_DOCS_URL/sitemap.xml" > /tmp/galtea-sitemap.xml
fi

# Find the URL set you need, then pick one
grep -oE 'https://[^<]+/sdk/tutorials/[^<]+'  /tmp/galtea-sitemap.xml   # 13 tutorials
grep -oE 'https://[^<]+/concepts/[^<]+'       /tmp/galtea-sitemap.xml   # concept pages
grep -oE 'https://[^<]+/api-reference/[^<]+'  /tmp/galtea-sitemap.xml   # per-endpoint docs

# Fetch one specific page
curl -s "$GALTEA_DOCS_URL/sdk/tutorials/run-test-based-evaluations"
```

### OpenAPI spec

```bash
GALTEA_API_URL="${GALTEA_API_URL:-https://api.galtea.ai}"

# Refresh if missing or older than 24h
if [ ! -f /tmp/galtea-openapi.json ] || \
   [ $(( $(date +%s) - $(stat -c %Y /tmp/galtea-openapi.json 2>/dev/null || echo 0) )) -gt 86400 ]; then
  curl -s "$GALTEA_API_URL/openapi.json" > /tmp/galtea-openapi.json
fi

jq '.paths | keys[]'                 /tmp/galtea-openapi.json   # every endpoint path
jq '.paths."/evaluations".post'      /tmp/galtea-openapi.json   # full POST /evaluations spec
jq '.components.schemas.Evaluation'  /tmp/galtea-openapi.json   # reusable schema
jq '.components.securitySchemes'     /tmp/galtea-openapi.json   # auth schemes
```

**OpenAPI 3.0 in one paragraph.** `.paths.<path>.<method>` describes one operation (its `parameters`, `requestBody`, `responses`). Request/response shapes use `$ref` pointers into `.components.schemas.<Name>`. Resolve references with `jq` incrementally — the full spec is ~900 KB and loading it all blows your context.

## Worked example — run a quality evaluation

End-to-end flow. Each numbered step runs in its own bash call, so start every call with the resolver block from the Authentication section (omitted below for brevity):

```bash
# Resolver (run this at the top of every Galtea bash call)
GALTEA_API_URL="${GALTEA_API_URL:-https://api.galtea.ai}"
GALTEA_DOCS_URL="${GALTEA_DOCS_URL:-https://docs.galtea.ai}"
GALTEA_API_KEY="${GALTEA_API_KEY:-$(cat ~/.galtea/api-key)}"
```

```bash
# 1. Find the product
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/products" | jq '.[] | {id, name}'

# 2. Find the version to evaluate
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/versions?productIds=<productId>" | jq '.[] | {id, name}'

# 3. Find a ready test dataset (status must be SUCCESS)
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/tests?productIds=<productId>" | jq '.[] | {id, name, type, status}'

# 4. Find a metric
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/metrics" | jq '.[] | {id, name, source}'

# 5. Trigger the evaluation (one POST per metric × version × test combo)
curl -s -X POST -H "Authorization: Bearer $GALTEA_API_KEY" -H "Content-Type: application/json" \
  "$GALTEA_API_URL/evaluations" \
  -d '{"versionId":"<vId>","testId":"<tId>","metricId":"<mId>"}' \
  | jq '{id, status}'

# 6. Poll until complete (status leaves PENDING)
curl -s -H "Authorization: Bearer $GALTEA_API_KEY" \
  "$GALTEA_API_URL/evaluations/<evalId>" | jq '{id, status, score, reason}'
```

Other end-to-end flows — creating a product, linking an endpoint connection, simulating conversations, tracing an agentic system, human evaluation, production monitoring — are covered by tutorials under `$GALTEA_DOCS_URL/sdk/tutorials/*`. Fetch the specific one via the sitemap rather than reinventing it here.

## Python alternative

Galtea ships an official Python SDK that wraps the same API. If the user prefers Python (or is on a shell where the Bash snippets above don't run), install it with `pip install galtea` and follow the installation guide at **https://docs.galtea.ai/sdk/installation** plus the tutorials under `$GALTEA_DOCS_URL/sdk/tutorials/*`. The SDK and the REST API target the same endpoints, so mixing them in the same project is safe.

## Gotchas

True quirks — things you won't find by reading `openapi.json` alone.

- **Response shape is inconsistent.** `/products`, `/tests`, `/versions`, `/evaluations`, `/metrics`, `/traces` return a plain array (`jq '.[]'`). `/sessions`, `/inferenceResults`, `/endpointConnections`, `/organizations` return `{ data, total, page, limit }` (`jq '.data[]'`).
- **Paginated list endpoints** (the `{data,total,page,limit}` ones) default to a limit that may hide results — pass `page` and `limit` query params when you need completeness.
- **Duplicate names return `409 Conflict`** (version names scoped by product, metric/test names scoped by org). Don't blind-retry on 409 — either rename or fetch the existing row.
- **Tests must be `status: SUCCESS`** before an evaluation can run against them. `PENDING` / `AUGMENTING` will fail.
- **`inputTemplate` uses Nunjucks/Jinja2** and must contain `{{ input.user_message }}` for `CONVERSATION` endpoint connections.
- **`outputMapping` uses JSONPath** and must contain an `output` key.
- **Traces** return a plain array (`jq '.[]'`), and `input` / `output` / `attributes` fields may be `null` even on valid rows. Filter by `inferenceResultIds` (plural) when listing.
- **Common error codes**: `401` invalid/revoked key (run auth), `402` out of credits (check `GET /organizations`), `409` duplicate name (rename), `422` validation error (inspect response body, cross-check OpenAPI for the constraint).
- **Credits are consumed** by evaluations and test generation only — not by reads or auth. `GET /organizations` returns `remainingSubscriptionCredits` + `extraCredits` for a pre-flight sanity check before a large batch. Informational, not mandatory.

## When not to use this skill

- **Building the AI product itself.** This skill is for *evaluating* products, not authoring them.
- **Pure UI browsing.** If the user just wants to look at results visually, point them at `https://platform.galtea.ai` instead of replaying the curl chain.
- **Hand-writing test content.** Galtea generates test cases from specifications (see `/sdk/tutorials/writing-specifications`). Let the platform do that work.
