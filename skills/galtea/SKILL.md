---
name: galtea
description: Interact with the Galtea API. Use when needing to (1) read or write Galtea resources programmatically — products, versions, endpoint connections, tests, metrics, evaluations, sessions, traces, and more, (2) trigger and monitor evaluation runs, or (3) set up a new product version with its endpoint connection.
---

# Galtea

This skill helps you interact with the Galtea Platform API directly via `curl`. Use it to manage AI product evaluation workflows, inspect results, and set up product versions.

## Core Principles

1. **Always resolve IDs first.** Most create/list operations require IDs (productId, versionId, testId, metricId). Use list endpoints to discover them before acting.
2. **Evaluations are async.** After triggering, poll `GET /evaluations/{id}` until `status` is `SUCCESS` or `FAILED`.
3. **Endpoint connections must be tested before use.** Use `POST /endpointConnections/testConnection` to validate before linking to a version.
4. **Soft deletes everywhere.** Deleted resources have `deletedAt` set — they are not permanently removed. List endpoints exclude them by default.

## Auth & Base URL

```bash
export GALTEA_API_KEY=gsk_...      # found in Galtea dashboard → Settings → API Keys
export GALTEA_BASE_URL=https://api.galtea.ai
```

All requests use Bearer auth:

```bash
-H "Authorization: Bearer $GALTEA_API_KEY" \
-H "Content-Type: application/json"
```

---

## Common Workflows

### 1. Run an evaluation

```bash
# Step 1 — find your product
curl -s "$GALTEA_BASE_URL/products" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, name}'

# Step 2 — find the version to evaluate
curl -s "$GALTEA_BASE_URL/versions?productIds=<productId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, name}'

# Step 3 — find a test dataset
curl -s "$GALTEA_BASE_URL/tests?productIds=<productId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, name, status, type}'

# Step 4 — find metrics
curl -s "$GALTEA_BASE_URL/metrics" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, name, source}'

# Step 5 — trigger evaluation (one POST per metric)
curl -s -X POST "$GALTEA_BASE_URL/evaluations" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "versionId": "<versionId>",
    "testId": "<testId>",
    "metricId": "<metricId>"
  }' | jq '{id, status}'

# Step 6 — poll until done
curl -s "$GALTEA_BASE_URL/evaluations/<evalId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '{id, status, score, reason}'
```

### 2. Create a new product version with an endpoint connection

```bash
# Step 1 — create the version
curl -s -X POST "$GALTEA_BASE_URL/versions" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "productId": "<productId>",
    "name": "v1.2.0",
    "description": "Optional description",
    "systemPrompt": "You are a helpful assistant."
  }' | jq '{id, name}'

# Step 2 — create the endpoint connection
curl -s -X POST "$GALTEA_BASE_URL/endpointConnections" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "productId": "<productId>",
    "name": "My API",
    "url": "https://your-api.com/v1/chat",
    "httpMethod": "POST",
    "authType": "BEARER",
    "authToken": "<your-api-token>",
    "type": "CONVERSATION",
    "inputTemplate": "{\"messages\": [{\"role\": \"user\", \"content\": \"{{ input.user_message }}\"}]}",
    "outputMapping": { "output": "$.choices[0].message.content" }
  }' | jq '{id, name}'

# Step 3 — test the connection before linking
curl -s -X POST "$GALTEA_BASE_URL/endpointConnections/testConnection" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "endpointConnectionId": "<connectionId>",
    "input": { "user_message": "Hello, are you working?" }
  }' | jq '.'

# Step 4 — link connection to version
curl -s -X PATCH "$GALTEA_BASE_URL/versions/<versionId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "conversationEndpointConnectionId": "<connectionId>" }' | jq '{id, name}'
```

### 3. Debug a failed evaluation

There are two distinct failure types — check both:
- **Metric failure**: evaluation ran successfully but `score=0`. Filter evaluations by `score` after listing.
- **Execution failure**: evaluation could not run at all. Look for `status=FAILED` on sessions/inferenceResults.

```bash
# Find metric failures for a version (status=SUCCESS but score=0)
curl -s "$GALTEA_BASE_URL/evaluations?versionIds=<versionId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | select(.score == 0) | {id, score, reason, sessionId}'

# List sessions for a version (find execution failures)
curl -s "$GALTEA_BASE_URL/sessions?versionId=<versionId>&status=FAILED" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.data[] | {id, status, error}'

# Get inference results for a session
curl -s "$GALTEA_BASE_URL/inferenceResults?sessionId=<sessionId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.data[] | {id, status, actualOutput, error}'

# Get traces for an inference result (tool calls, RAG spans, LLM generations)
# NOTE: use plural inferenceResultIds (singular is rejected)
# NOTE: traces return a plain array [], not {data: []}
# NOTE: trace input/output/attributes may be null even when the trace exists
curl -s "$GALTEA_BASE_URL/traces?inferenceResultIds=<inferenceResultId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, type, name, error, latencyMs}'
```

---

## Resource Reference

### Products

```bash
# List
curl -s "$GALTEA_BASE_URL/products" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, name}'

# Get
curl -s "$GALTEA_BASE_URL/products/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.'

# Create
curl -s -X POST "$GALTEA_BASE_URL/products" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My AI Product",
    "description": "What the product does, its capabilities and limitations."
  }' | jq '{id, name}'

# Update
curl -s -X PATCH "$GALTEA_BASE_URL/products/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "description": "Updated description" }' | jq '{id, name}'
```

### Versions

```bash
# List (filter by product)
curl -s "$GALTEA_BASE_URL/versions?productIds=<productId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, name, createdAt}'

# Get
curl -s "$GALTEA_BASE_URL/versions/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.'

# Create
curl -s -X POST "$GALTEA_BASE_URL/versions" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "productId": "<productId>",
    "name": "v1.0.0",
    "description": "Initial release",
    "systemPrompt": "You are a helpful assistant."
  }' | jq '{id, name}'

# Update
curl -s -X PATCH "$GALTEA_BASE_URL/versions/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "conversationEndpointConnectionId": "<connectionId>" }' | jq '{id}'
```

Key fields: `productId` (required), `name` (required), `systemPrompt`, `description`, `conversationEndpointConnectionId`, `initializationEndpointConnectionId`, `finalizationEndpointConnectionId`.

### Endpoint Connections

```bash
# List (filter by product)
curl -s "$GALTEA_BASE_URL/endpointConnections?productId=<productId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.data[] | {id, name, url, type}'

# Get
curl -s "$GALTEA_BASE_URL/endpointConnections/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.'

# Create
curl -s -X POST "$GALTEA_BASE_URL/endpointConnections" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "productId": "<productId>",
    "name": "Production API",
    "url": "https://your-api.com/chat",
    "httpMethod": "POST",
    "authType": "BEARER",
    "authToken": "<token>",
    "type": "CONVERSATION",
    "inputTemplate": "{\"messages\": [{\"role\": \"user\", \"content\": \"{{ input.user_message }}\"}]}",
    "outputMapping": { "output": "$.choices[0].message.content" },
    "timeout": 60
  }' | jq '{id, name}'

# Test connection
curl -s -X POST "$GALTEA_BASE_URL/endpointConnections/testConnection" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "endpointConnectionId": "<id>",
    "input": { "user_message": "ping" }
  }' | jq '.'

# Update
curl -s -X PATCH "$GALTEA_BASE_URL/endpointConnections/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "url": "https://new-url.com/chat" }' | jq '{id, name}'
```

`authType` values: `NONE`, `BEARER`, `API_KEY`, `BASIC`, `OAUTH2`.
`type` values: `INITIALIZATION`, `CONVERSATION`, `FINALIZATION`.
`inputTemplate` must contain `{{ input.user_message }}`.

### Tests

```bash
# List (filter by product)
curl -s "$GALTEA_BASE_URL/tests?productIds=<productId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, name, type, status}'

# Get with test cases
curl -s "$GALTEA_BASE_URL/tests/<id>/testCases" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.data[] | {id, input, expectedOutput}'

# Create (Galtea generates test cases automatically)
curl -s -X POST "$GALTEA_BASE_URL/tests" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "productId": "<productId>",
    "name": "Quality test",
    "type": "QUALITY",
    "variants": ["rag"],
    "maxTestCases": 20
  }' | jq '{id, name, status}'
```

`type` values: `QUALITY`, `RED_TEAMING`, `SCENARIOS`.
`status` values: `PENDING`, `SUCCESS`, `FAILED`, `AUGMENTING`. Wait for `SUCCESS` before running evaluations.

### Metrics

```bash
# List all available metrics
curl -s "$GALTEA_BASE_URL/metrics" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, name, source, tags, evaluationParams}'

# Get
curl -s "$GALTEA_BASE_URL/metrics/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.'
```

`source` values: `DEEPEVAL`, `GEVAL`, `FULL_PROMPT`, `PARTIAL_PROMPT`, `HUMAN_EVALUATION`, `DETERMINISTIC`, `SELF_HOSTED`.

### Evaluations

```bash
# List (filter by version or test)
curl -s "$GALTEA_BASE_URL/evaluations?versionIds=<versionId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, status, score, metricId}'

# Get
curl -s "$GALTEA_BASE_URL/evaluations/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '{id, status, score, reason, error}'

# Create (trigger)
curl -s -X POST "$GALTEA_BASE_URL/evaluations" \
  -H "Authorization: Bearer $GALTEA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "versionId": "<versionId>",
    "testId": "<testId>",
    "metricId": "<metricId>"
  }' | jq '{id, status}'
```

`status` values: `PENDING`, `PENDING_HUMAN`, `SUCCESS`, `FAILED`, `SKIPPED`.
Poll `GET /evaluations/<id>` until status is no longer `PENDING`.

### Sessions

```bash
# List (filter by version)
curl -s "$GALTEA_BASE_URL/sessions?versionId=<versionId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.data[] | {id, status, error, createdAt}'

# Get
curl -s "$GALTEA_BASE_URL/sessions/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.'
```

### Inference Results

```bash
# List (filter by session)
curl -s "$GALTEA_BASE_URL/inferenceResults?sessionId=<sessionId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.data[] | {id, status, actualOutput, latency, error}'

# Get
curl -s "$GALTEA_BASE_URL/inferenceResults/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.'
```

### Traces

```bash
# List (filter by inferenceResultId)
# NOTE: use plural inferenceResultIds — singular is rejected with a filter error
# NOTE: response is a plain array [], not {data: []}
# NOTE: input/output/attributes fields may be null even on valid traces
curl -s "$GALTEA_BASE_URL/traces?inferenceResultIds=<inferenceResultId>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.[] | {id, type, name, latencyMs, error}'

# Get
curl -s "$GALTEA_BASE_URL/traces/<id>" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.'
```

`type` values: `SPAN`, `GENERATION`, `TOOL`, `RETRIEVER`, `CHAIN`, `AGENT`, `EVALUATOR`, `EMBEDDING`, `GUARDRAIL`, `EVENT`.

### Organizations

```bash
# Get current org info (includes remaining credits)
curl -s "$GALTEA_BASE_URL/organizations" \
  -H "Authorization: Bearer $GALTEA_API_KEY" | jq '.data[0] | {id, name, remainingSubscriptionCredits, extraCredits}'
```

---

## Tips

- Filter query params always use the **plural** form: `productIds`, `versionIds`, `testIds`, `metricIds`, etc. Singular forms (e.g. `productId`) are rejected with an error listing accepted params.
- List endpoints have inconsistent response shapes. `/products`, `/tests`, `/versions`, `/evaluations`, `/metrics`, and `/traces` return a plain array `[...]` — use `.[]` with jq. Other endpoints (e.g. sessions, inferenceResults, endpointConnections, organizations) return `{ data: [...], total, page, limit }` — use `.data[]` for those.
- Use `jq` to extract specific fields and avoid large response payloads.
- `inputTemplate` uses Nunjucks/Jinja2 syntax. The placeholder `{{ input.user_message }}` is required for conversation connections.
- `outputMapping` uses JSONPath expressions. The `output` key is required.
- Tests with `status: PENDING` or `AUGMENTING` are not ready for evaluation — wait for `SUCCESS`.
- Evaluations consume credits. Check `GET /organizations` for remaining balance before running large batches.
