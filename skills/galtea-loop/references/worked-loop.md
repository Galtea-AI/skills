---
name: galtea-loop-worked-example
description: End-to-end worked example of the full Galtea improvement loop (define specs -> generate tests -> evaluate -> iterate), shown once as a Python SDK script and once as an equivalent `galtea` CLI session. Use when the user wants to see the loop run concretely rather than stage by stage.
---

# Worked example — one full pass through the loop

This walks a single product from zero to a first set of pass/fail outcomes and the iteration decision, on both surfaces. **Illustrative only** — verify every method/command shape and enum value against the docs (`https://docs.galtea.ai/llms.txt`, any page + `.md`) and `galtea <noun> <verb> --help` before relying on it, per the `galtea` skill's "documentation first" rule. Get install & auth working first (see the `galtea` skill).

## Python SDK

Best when the product is callable from Python. One script runs all four stages; the agent is run and scored in stage 3.

```python
import time
from galtea import Galtea

client = Galtea()  # reads GALTEA_API_KEY from the environment

# ── Stage 1: define specs ───────────────────────────────────────────────
product = client.products.create(name="Support Agent")
specs = [
    client.specifications.create(product_id=product.id, type="CAPABILITY",
        description="Answers billing questions using the customer's plan data"),
    client.specifications.create(product_id=product.id, type="INABILITY",
        description="Never provides legal or medical advice"),
    client.specifications.create(product_id=product.id, type="POLICY",
        description="Always cites the knowledge-base article it used"),
]

# ── Stage 2: generate test cases (async job per test) ───────────────────
# Pick the test type from the spec's intent (verify enums via docs/--help).
TEST_TYPE = {"CAPABILITY": "ACCURACY", "INABILITY": "SECURITY", "POLICY": "BEHAVIOR"}
tests = []
for spec in specs:
    t = client.tests.create(product_id=product.id, specification_id=spec.id,
                            type=TEST_TYPE.get(spec.type, "ACCURACY"),
                            name=f"tests for {spec.id}")
    tests.append(t)

for t in tests:                       # poll each generation job to a terminal state
    while True:
        status = client.jobs.get_status(t.job_id)   # verify field/method via docs
        if status == "FAILED":
            raise RuntimeError(f"test generation failed for test {t.id}")
        if status == "SUCCESS":
            break
        time.sleep(5)

# ── Stage 3: run the product and evaluate ───────────────────────────────
def my_agent(messages: list[dict]) -> str:      # annotate the first param deliberately
    return "Mock response"                       # replace with a call to your AI product

version = client.versions.create(product_id=product.id, name="v1")
for t in tests:
    for case in client.tests.list_cases(t.id):   # verify method name via docs
        client.inference_results.create_and_evaluate(
            version_id=version.id, test_case_id=case.id, agent=my_agent)

# ── Read outcomes ───────────────────────────────────────────────────────
results = client.evaluations.list(version_id=version.id)
for e in results:
    print(e.status, e.score, e.reason)
```

## `galtea` CLI

Best when the product is reached over HTTP via an `EndpointConnection` (Galtea calls the product for you) or Python is unavailable. `</dev/null` on body-taking commands is required in non-TTY contexts — see the `galtea` skill's Gotchas.

```bash
# ── Stage 1: define specs ──────────────────────────────────────────────
PID=$(galtea products create name: "Support Agent" -o json </dev/null | jq -r .id)
galtea specifications create productId: "$PID", type: CAPABILITY, \
  description: "Answers billing questions using the customer's plan data" </dev/null
galtea specifications create productId: "$PID", type: INABILITY, \
  description: "Never provides legal or medical advice" </dev/null

# ── Stage 2: generate test cases (one async job per test) ──────────────
# Link each test to its spec, picking the test type from the spec's intent
# (verify enums via docs/--help). Poll each returned job to a terminal state.
for ROW in $(galtea specifications list --product-ids "$PID" -o json | jq -r '.[] | "\(.id):\(.type)"'); do
  SID="${ROW%%:*}"; STYPE="${ROW##*:}"
  case "$STYPE" in
    INABILITY) TTYPE=SECURITY ;;
    POLICY)    TTYPE=BEHAVIOR ;;
    *)         TTYPE=ACCURACY ;;   # CAPABILITY and anything else
  esac
  JOB=$(galtea tests create productId: "$PID", specificationId: "$SID", \
        type: "$TTYPE", name: "tests for $SID" -o json </dev/null | jq -r .jobId)
  while true; do
    STATUS=$(galtea jobs get-status "$JOB" -o json | jq -r .status)
    [ "$STATUS" = "SUCCESS" ] && break
    [ "$STATUS" = "FAILED" ] && { echo "test generation failed for spec $SID" >&2; exit 1; }
    sleep 5
  done
done

# ── Stage 3: evaluate the whole version ─────────────────────────────────
# Create a version to evaluate. create-from-version reaches the product via
# the version's EndpointConnection, so wire one first (see the galtea skill);
# a version without it produces no evaluations.
VID=$(galtea versions create productId: "$PID", name: "v1" -o json </dev/null | jq -r .id)

# create-from-version cascades specs -> metrics -> tests -> evaluations.
# See the galtea skill's references/evaluate-version.md for the full async lifecycle.
galtea evaluations create-from-version versionId: "$VID" </dev/null
while [ "$(galtea evaluations list --version-ids "$VID" --statuses PENDING -o json | jq 'length')" -gt 0 ]; do
  sleep 5
done

# ── Read outcomes ──────────────────────────────────────────────────────
galtea evaluations list --version-ids "$VID" -o json \
  | jq '.[] | {id, specificationId, status, score, reason}'
```

## Stage 4 — iterate (both surfaces)

With `results` in hand, group failures by specification and decide:

- **Failing `CAPABILITY`/`POLICY` cases** → propose product changes (prompt, tools, retrieval) that address the evaluator's `reason`.
- **Failing `INABILITY` cases** → the product did something it must not; propose a guardrail change.
- **A "failure" that is actually acceptable behavior** → the spec is wrong/ambiguous; propose editing the spec, not the product.
- **`SKIPPED` evaluations** → usually a wiring issue (a non-`SUCCESS` test, or the agent crashed), not a real product failure — fix the wiring and re-run.

Then create a **new version** and re-run stage 3 (or stages 1–2 if specs/tests changed) so iterations are comparable, and report the pass-rate delta against the previous version.
