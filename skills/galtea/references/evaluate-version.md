---
name: galtea-evaluate-version
description: End-to-end curl walkthrough for running evaluations against a Galtea product version via POST /evaluations/fromVersion. Use when the user asks to run a full evaluation pass on a version, kick off evaluations, or needs to understand the async evaluation lifecycle concretely.
---

# Worked example -- evaluate a product version

End-to-end flow for `POST /evaluations/fromVersion` -- the one-shot path that cascades across the product's specifications, their linked metrics, and their linked tests. Use this when the user wants to "run the full eval pass" for a version.

## Before you start

Run the resolver block from `SKILL.md`'s Authentication section at the top of every bash call below. Many agents reset shell state between bash invocations, so `$GALTEA_API_URL`, `$GALTEA_DOCS_URL`, and `$GALTEA_API_KEY` must be re-populated each time:

```bash
GALTEA_API_URL="${GALTEA_API_URL:-https://api.galtea.ai}"
GALTEA_DOCS_URL="${GALTEA_DOCS_URL:-https://docs.galtea.ai}"
GALTEA_API_KEY="${GALTEA_API_KEY:-$(cat ~/.galtea/api-key 2>/dev/null)}"
```

## 5-step flow

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

## Why step 4 is needed

`POST /evaluations/fromVersion` returns `202 Accepted` with no body because the evaluation jobs are created asynchronously -- typically one per (Specification x Metric x TestCase) combination. The caller has to list the freshly-created evaluations (status `PENDING`, filtered by `versionId`) to learn their IDs, then poll each one until `status` reaches a terminal state.

Terminal states for the poll:

- `SUCCESS`, `FAILED`, `SKIPPED` -- stop polling and report to the user.
- `PENDING_HUMAN` -- evaluation is waiting for a human reviewer. Stop polling and surface that state to the user; this evaluation will never reach SUCCESS on its own.

## Common pitfalls

- **Tests must be `status: SUCCESS`** before `fromVersion` will create evaluations against them. `PENDING` / `AUGMENTING` tests are skipped silently. Check `GET /tests?productIds=<productId>` first if step 4 returns fewer evaluations than expected.
- **Credits are consumed** by the newly-created evaluations. Pre-flight via `GET /organizations` (`remainingSubscriptionCredits` + `extraCredits`) if the user is close to a budget. If an org runs out mid-run, evaluations fail with a `message` in the body -- no dedicated HTTP status code, so inspect the message rather than matching on a code.
- **Duplicate names** on related resources (products, versions, tests, metrics) return `400 Bad Request` with a body `message` starting with `"A <Entity> with the same Name..."`. Do not blind-retry on any 400; parse the message first.

## Alternative creation paths

`fromVersion` is only one entry point. For `fromSession`, `fromInferenceResult`, `singleTurn`, `batch`, and `retry`, see the "Evaluation creation paths" routing table in `SKILL.md`.
