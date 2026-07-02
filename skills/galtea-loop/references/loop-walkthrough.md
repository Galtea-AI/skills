---
name: galtea-loop-walkthrough
description: End-to-end worked example of the full Galtea improvement loop (define specs -> generate tests -> evaluate -> iterate), shown once as a Python SDK script and once as an equivalent `galtea` CLI session. Use when the user wants to see the loop run concretely rather than stage by stage.
---

# Worked example — one full pass through the loop

This walks a single product from zero to a first set of pass/fail outcomes and the iteration decision. It centers on the path verified against the API — a `POLICY` spec with `SCENARIOS` tests and a linked metric — because that generates test cases directly from the spec with no extra data files. Verify every method/command shape against `galtea <noun> <verb> --help` and the docs (`https://docs.galtea.ai/llms.txt`, any page + `.md`) before relying on it. Get install & auth working first (see the `galtea` skill); for a non-prod host, set `GALTEA_API_URL` (SDK) or `galtea login --host …` (CLI).

## Key API facts (verified against SDK 4.33.0 / the API)

- **Product creation requires a `description`, and the SDK has no `products.create`.** Create the product via the CLI (`galtea products create name: … , description: …`) or the platform, then fetch it in the SDK with `client.products.get_by_name(...)`.
- **`TestType` is `QUALITY` / `RED_TEAMING` / `SCENARIOS`** — not `ACCURACY`/`SECURITY`/`BEHAVIOR`. `SCENARIOS` generates from the spec alone; `QUALITY` generation needs an uploaded test/ground-truth file or a source test.
- **`SpecificationType` is `CAPABILITY` / `INABILITY` / `POLICY`.** `POLICY` specs require a `test_type` at creation (plus a `test_variant` for `QUALITY`/`RED_TEAMING`) and are the **only** spec type you can link metrics to. `CAPABILITY`/`INABILITY` specs omit `test_type` and derive their tests/metrics through the spec-driven flow (`/sdk/tutorials/specification-driven-evaluations`).
- **The agent-callback entry point is `client.evaluations.run(version_id, agent, specification_ids)`** — not `inference_results.create_and_evaluate` (which logs one pre-computed output to a session).
- **`tests.create` returns no job id.** Generation is async — poll `client.tests.get(id).status` until `SUCCESS`/`FAILED` (`TestStatus`: `PENDING`/`SUCCESS`/`FAILED`/`AUGMENTING`).

## Python SDK

```python
import time
from galtea import Galtea

client = Galtea(api_key="gsk_...")   # or Galtea() if GALTEA_API_KEY is exported

# ── Stage 1: define a spec ──────────────────────────────────────────────
# The SDK has no products.create — create it first via the CLI (name AND
# description are both required), then fetch it here:
#   galtea products create name: "Support Agent", description: "..." </dev/null
product = client.products.get_by_name("Support Agent")

spec = client.specifications.create(
    product_id=product.id, type="POLICY", test_type="SCENARIOS",
    description="Always states the billing cycle when asked about billing")

# ── Metric linked to the (POLICY) spec — needed for evaluation to score ──
metric = client.metrics.create(
    name="billing-accuracy", source="PARTIAL_PROMPT",
    evaluator_model_name="GPT-4.1-mini",
    judge_prompt="Score 1 if the answer states the billing cycle, else 0.",
    evaluation_params=["input", "actual_output"])
client.specifications.link_metrics(spec.id, [metric.id])

# ── Stage 2: generate test cases (async; SCENARIOS generates from the spec) ──
test = client.tests.create(product_id=product.id, specification_id=spec.id,
                          type="SCENARIOS", name="billing-behavior", max_test_cases=3)
# tests.create returns NO job id — poll the test's own status to a terminal state.
while not str(client.tests.get(test.id).status).endswith(("SUCCESS", "FAILED")):
    time.sleep(5)
if str(client.tests.get(test.id).status).endswith("FAILED"):
    raise RuntimeError("test generation failed")
cases = client.test_cases.list(test_id=test.id)      # inspect the generated cases

# ── Stage 3: run the product and evaluate ───────────────────────────────
def my_agent(messages: list[dict]) -> str:      # annotate the first param deliberately
    return "Your billing cycle is monthly; charges occur on the 1st."   # your AI product

version = client.versions.create(product_id=product.id, name="v1")
# evaluations.run drives the agent against the spec's tests (via the conversation
# simulator for SCENARIOS) and scores each one with the linked metric.
client.evaluations.run(version_id=version.id, agent=my_agent,
                       specification_ids=[spec.id])

# ── Read outcomes ───────────────────────────────────────────────────────
for e in client.evaluations.list(version_id=version.id):
    print(e.status, e.score, e.reason)
```

## `galtea` CLI

Use the CLI when Python is unavailable or the product is reached over HTTP via an `EndpointConnection` (Galtea calls the product for you, so there is no local agent callback). `</dev/null` on body-taking commands is required in non-TTY contexts — see the `galtea` skill's Gotchas. Verify every command with `--help`.

```bash
# ── Stage 1: define a product (name AND description are required) + a spec ──
PID=$(galtea products create name: "Support Agent", \
      description: "Customer support agent for billing questions" \
      -o json </dev/null | jq -r .id)
SID=$(galtea specifications create productId: "$PID", type: POLICY, \
      testType: SCENARIOS, \
      description: "Always states the billing cycle when asked about billing" \
      -o json </dev/null | jq -r .id)

# Create a metric and link it to the POLICY spec (only POLICY specs take metrics).
MID=$(galtea metrics create name: "billing-accuracy", source: PARTIAL_PROMPT, \
      evaluatorModelName: "GPT-4.1-mini", \
      judgePrompt: "Score 1 if the answer states the billing cycle, else 0.", \
      evaluationParams: [input, actual_output] -o json </dev/null | jq -r .id)
echo "{\"metricIds\":[\"$MID\"]}" | galtea specifications link-metrics "$SID"

# ── Stage 2: generate a SCENARIOS test from the spec, poll the test status ──
TID=$(galtea tests create productId: "$PID", specificationId: "$SID", \
      type: SCENARIOS, name: "billing-behavior" -o json </dev/null | jq -r .id)
# tests.create is async; poll the TEST status (PENDING/AUGMENTING are skipped later).
while true; do
  STATUS=$(galtea tests get "$TID" -o json | jq -r .status)
  [ "$STATUS" = "SUCCESS" ] && break
  [ "$STATUS" = "FAILED" ] && { echo "test generation failed for $SID" >&2; exit 1; }
  sleep 5
done

# ── Stage 3: evaluate a version ─────────────────────────────────────────
# The CLI can't call a Python agent, so reach the product via the version's
# EndpointConnection (wire one first — see the galtea skill), then run the
# whole version. create-from-version cascades specs -> metrics -> tests -> evaluations.
VID=$(galtea versions create productId: "$PID", name: "v1" -o json </dev/null | jq -r .id)
galtea evaluations create-from-version versionId: "$VID" </dev/null   # see galtea skill's evaluate-version.md
while [ "$(galtea evaluations list --version-ids "$VID" --statuses PENDING -o json | jq 'length')" -gt 0 ]; do
  sleep 5
done

# ── Read outcomes ──────────────────────────────────────────────────────
galtea evaluations list --version-ids "$VID" -o json \
  | jq '.[] | {id, specificationId, status, score, reason}'
```

## Stage 4 — iterate (both surfaces)

With the evaluations in hand, group failures by specification and decide:

- **Failing `CAPABILITY`/`POLICY` cases** → propose product changes (prompt, tools, retrieval) that address the evaluator's `reason`.
- **Failing `INABILITY` cases** → the product did something it must not; propose a guardrail change.
- **A "failure" that is actually acceptable behavior** → the spec is wrong/ambiguous; propose editing the spec, not the product.
- **`SKIPPED`/errored evaluations** → usually a wiring or transient-infra issue (a non-`SUCCESS` test, the agent crashed, a simulator hiccup), not a real product failure — fix the wiring and re-run those.

Then create a **new version** and re-run stage 3 (or stages 1–2 if specs/tests changed) so iterations are comparable, and report the pass-rate delta against the previous version.
