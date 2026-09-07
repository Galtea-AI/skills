---
name: galtea-loop-walkthrough
description: End-to-end worked example of the full Galtea improvement loop (define specs -> generate datasets -> evaluate -> iterate), shown once as a Python SDK script and once as an equivalent `galtea` CLI session. Use when the user wants to see the loop run concretely rather than stage by stage.
---

# Worked example — one full pass through the loop

This walks a single product from zero to a first set of pass/fail outcomes and the iteration decision. It centers on the path verified against the API — a `POLICY` spec with a Behavior dataset and a linked metric — because that generates test cases directly from the spec with no extra data files. Verify every method/command shape against `galtea <noun> <verb> --help` and the docs (`https://docs.galtea.ai/llms.txt`, any page + `.md`) before relying on it. Get install & auth working first (see the `galtea` skill); for a non-prod host, set `GALTEA_API_URL` (SDK) or `galtea login --host …` (CLI).

## Key API facts (verified against a recent SDK / the current API)

Mechanics the `galtea` skill already owns — install/auth, the `</dev/null` stdin rule, async polling, and the "datasets must reach `SUCCESS`; `PENDING`/`AUGMENTING` are skipped" gotcha — are not repeated here; read them there. What follows is only what this loop adds on top or corrects.

- **Product creation requires a `description`, and the SDK has no `products.create`.** Create the product via the CLI (`galtea products create name: … , description: …`) or the platform, then fetch it in the SDK with `client.products.get_by_name(...)`.
- **`DatasetType` reads differently per surface** — SDK `ACCURACY`/`SECURITY`/`BEHAVIOR`, CLI and raw API `QUALITY`/`RED_TEAMING`/`SCENARIOS`. The mapping table is owned by the `galtea` skill. Loop-relevant difference: Behavior generates from the spec alone, while Accuracy generation needs an uploaded dataset/ground-truth file or a source dataset. Uploading a dataset the user already has is owned by the `galtea` skill (`references/custom-dataset-upload.md`).
- **`SpecificationType` is `CAPABILITY` / `INABILITY` / `POLICY`.** `POLICY` specs require a `dataset_type` at creation (plus a `dataset_variant` for Accuracy/Security). `POLICY` and `CAPABILITY` specs can both link metrics; `INABILITY` cannot. `CAPABILITY`/`INABILITY` specs omit `dataset_type` and derive their datasets/metrics through the spec-driven flow (`/sdk/tutorials/specification-driven-evaluations`).
- **The type parameter name split in the rename.** The wire field stays `testType`, so the CLI keeps `testType: SCENARIOS`. The SDK parameter is now `dataset_type` (and `dataset_variant`), with `test_type` / `test_variant` accepted as deprecated aliases that warn.
- **`specifications.create` requires a `name`.** It is a required positional argument in the SDK and a 400 without it on the CLI, even though the published schema does not mark it required.
- **The agent-callback entry point is `client.evaluations.run(version_id, agent, specification_ids)`** — not `traces.create_and_evaluate` (which logs one pre-computed output to a session).
- **`datasets.create` returns no job id.** Generation is async — poll `client.datasets.get(id).status` until `SUCCESS`/`FAILED` (`DatasetStatus`: `PENDING`/`SUCCESS`/`FAILED`/`AUGMENTING`).

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

# A spec needs a name as well; the spec schema does not mark it required.
spec = client.specifications.create(
    product_id=product.id, name="billing-cycle-disclosure",
    type="POLICY", dataset_type="BEHAVIOR",
    description="Always states the billing cycle when asked about billing")

# ── Metric linked to the (POLICY) spec — needed for evaluation to score ──
metric = client.metrics.create(
    name="billing-accuracy", source="PARTIAL_PROMPT",
    evaluator_model_name="GPT-4.1-mini",
    judge_prompt="Score 1 if the answer states the billing cycle, else 0.",
    evaluation_params=["input", "actual_output"])
client.specifications.link_metrics(spec.id, [metric.id])

# ── Stage 2: generate the dataset (async; Behavior generates from the spec) ──
dataset = client.datasets.create(product_id=product.id, specification_id=spec.id,
                                 type="BEHAVIOR", name="billing-behavior", max_test_cases=3)
# datasets.create returns NO job id — poll the dataset's own status to a terminal state.
while not str(client.datasets.get(dataset.id).status).endswith(("SUCCESS", "FAILED")):
    time.sleep(5)
if str(client.datasets.get(dataset.id).status).endswith("FAILED"):
    raise RuntimeError("dataset generation failed")
cases = client.test_cases.list(dataset_id=dataset.id)   # inspect the generated cases

# ── Stage 3: run the product and evaluate ───────────────────────────────
def my_agent(messages: list[dict]) -> str:      # annotate the first param deliberately
    return "Your billing cycle is monthly; charges occur on the 1st."   # your AI product

version = client.versions.create(product_id=product.id, name="v1")
# evaluations.run drives the agent against the spec's datasets (via the conversation
# simulator for Behavior) and scores each one with the linked metric.
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
SID=$(galtea specifications create productId: "$PID", \
      name: "billing-cycle-disclosure", type: POLICY, \
      testType: SCENARIOS, \
      description: "Always states the billing cycle when asked about billing" \
      -o json </dev/null | jq -r .id)

# Create a metric and link it to the POLICY spec (only POLICY specs take metrics).
# Pipe JSON on stdin: a comma inside the judgePrompt value misparses in the
# colon-shorthand form (Restish reads it as a field separator) — verified to
# fail against the live CLI. JSON-on-stdin is the robust form for any value
# containing commas or spaces.
MID=$(echo '{"name":"billing-accuracy","source":"PARTIAL_PROMPT","evaluatorModelName":"GPT-4.1-mini","judgePrompt":"Score 1 if the answer states the billing cycle, else 0.","evaluationParams":["input","actual_output"]}' \
      | galtea metrics create -o json | jq -r .id)
echo "{\"metricIds\":[\"$MID\"]}" | galtea specifications link-metrics "$SID"

# ── Stage 2: generate a Behavior dataset from the spec, poll the dataset status ──
# On the CLI the type is the wire value SCENARIOS, not the SDK's BEHAVIOR.
DID=$(galtea datasets create productId: "$PID", specificationId: "$SID", \
      type: SCENARIOS, name: "billing-behavior" -o json </dev/null | jq -r .id)
# datasets create is async; poll the DATASET status (PENDING/AUGMENTING are skipped later).
while true; do
  STATUS=$(galtea datasets get "$DID" -o json | jq -r .status)
  [ "$STATUS" = "SUCCESS" ] && break
  [ "$STATUS" = "FAILED" ] && { echo "dataset generation failed for $SID" >&2; exit 1; }
  sleep 5
done

# ── Stage 3: evaluate a version ─────────────────────────────────────────
# The CLI can't call a Python agent, so reach the product via the version's
# EndpointConnection (wire one first — see the galtea skill), then run the
# whole version. create-from-version cascades specs -> metrics -> datasets -> evaluations.
VID=$(galtea versions create productId: "$PID", name: "v1" -o json </dev/null | jq -r .id)
galtea evaluations create-from-version versionId: "$VID" </dev/null   # see galtea skill's evaluate-version.md
while [ "$(galtea evaluations list --version-ids "$VID" --statuses PENDING -o json | jq 'length')" -gt 0 ]; do
  sleep 5
done

# ── Read outcomes ──────────────────────────────────────────────────────
# An Evaluation carries metricId, not specificationId: the link to a spec runs
# through the metric. To group by spec, filter one spec at a time.
galtea evaluations list --version-ids "$VID" --specification-ids "$SID" -o json \
  | jq '.[] | {id, metricId, status, score, reason}'
```

## Stage 4 — iterate (both surfaces)

With the evaluations in hand, group failures by specification and decide:

- **Failing `CAPABILITY`/`POLICY` cases** → propose product changes (prompt, tools, retrieval) that address the evaluator's `reason`.
- **Failing `INABILITY` cases** → the product did something it must not; propose a guardrail change.
- **A "failure" that is actually acceptable behavior** → the spec is wrong/ambiguous; propose editing the spec, not the product.
- **`SKIPPED`/errored evaluations** → usually a wiring or transient-infra issue (a non-`SUCCESS` dataset, the agent crashed, a simulator hiccup), not a real product failure — fix the wiring and re-run those.

Then create a **new version** and re-run stage 3 (or stages 1–2 if specs/datasets changed) so iterations are comparable, and report the pass-rate delta against the previous version.
