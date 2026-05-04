---
name: galtea-evaluate-version
description: End-to-end CLI walkthrough for running evaluations against a Galtea product version via `galtea evaluations create-from-version`. Use when the user asks to run a full evaluation pass on a version, kick off evaluations, or needs to understand the async evaluation lifecycle concretely.
---

# Worked example -- evaluate a product version

End-to-end flow for `galtea evaluations create-from-version` -- the one-shot path that cascades across the product's specifications, their linked metrics, and their linked tests. Use this when the user wants to "run the full eval pass" for a version.

## Before you start

`galtea --version` should return a version, and `galtea whoami` should report `authenticated`. If either fails, fix it first via the CLI Installation and Authentication sections in `SKILL.md`.

## 5-step flow

```bash
# 1. Find the product
galtea products list -o json | jq '.[] | {id, name}'

# 2. Find the version to evaluate
galtea versions list --product-ids <productId> -o json | jq '.[] | {id, name}'

# 3. Kick off evaluations for the whole version. Galtea resolves the product's
#    specifications, their linked metrics, and their linked tests automatically.
#    Returns 202 with a jobId -- the actual evaluations are created asynchronously.
galtea evaluations create-from-version versionId: <versionId>

# 4. List the freshly-created evaluations to grab their IDs (the create call only returned a job id)
galtea evaluations list \
  --version-ids <versionId> \
  --statuses PENDING \
  --sort -createdAt \
  --limit 20 \
  -o json | jq '.[] | {id, metricId, status}'

# 5. Poll one until it settles (SUCCESS / FAILED / SKIPPED)
galtea evaluations get <evalId> -o json | jq '{id, status, score, reason}'
```

For body fields on the create call (`versionId`, optional `specificationIds`), the CLI uses Restish's inline shorthand: `key: value` pairs, comma-separated, with arrays in `[a, b, c]` form. To pass multiple specifications:

```bash
galtea evaluations create-from-version versionId: <versionId>, specificationIds: [<spec1>, <spec2>]
```

If you prefer JSON-on-stdin (cleaner for complex bodies):

```bash
echo '{"versionId":"<versionId>","specificationIds":["<spec1>","<spec2>"]}' \
  | galtea evaluations create-from-version
```

Run `galtea evaluations create-from-version --help` for the canonical request schema and the `EXAMPLES` block.

## Why step 4 is needed

`create-from-version` returns `202 Accepted` with a `jobId` because the evaluation jobs are created asynchronously -- typically one per (Specification x Metric x TestCase) combination. The caller has to list the freshly-created evaluations (status `PENDING`, filtered by `versionId`) to learn their IDs, then poll each one until `status` reaches a terminal state.

Terminal states for the poll:

- `SUCCESS`, `FAILED`, `SKIPPED` -- stop polling and report to the user.
- `PENDING_HUMAN` -- evaluation is waiting for a human reviewer. Stop polling and surface that state to the user; this evaluation will never reach SUCCESS on its own.

## Common pitfalls

- **Tests must be `status: SUCCESS`** before `create-from-version` will create evaluations against them. `PENDING` / `AUGMENTING` tests are skipped silently. Check `galtea tests list --product-ids <productId>` first if step 4 returns fewer evaluations than expected.
- **Credits are consumed** by the newly-created evaluations. Pre-flight by resolving the org id (`galtea auth get-current-user -f body.organizationId`), then `galtea organizations get-credit-status <organizationId>` (run `--help` for the exact arg shape) to inspect `totalCredits` / `usedCredits` / `remainingCredits`. If an org runs out mid-run, evaluations fail with a `message` in the body -- no dedicated HTTP status code, so inspect the message rather than matching on a code.
- **Duplicate names** on related resources (products, versions, tests, metrics) return `400 Bad Request` with a body `message` starting with `"A <Entity> with the same Name..."`. Do not blind-retry on any 400; parse the message first.
- **Stale local spec**. If `galtea evaluations create-from-version` errors with "unknown command" or a flag the docs say exists is missing, run `galtea sync` to refresh the OpenAPI command tree.

## Alternative creation paths

`create-from-version` is only one entry point. For `create-from-session`, `create-from-inference-result`, `create-single-turn`, `retry`, and `replay-from-metrics`, see the "Evaluation creation paths" routing table in `SKILL.md`. Each one is a sibling under `galtea evaluations`; run `galtea evaluations --help` to see the full verb list.
