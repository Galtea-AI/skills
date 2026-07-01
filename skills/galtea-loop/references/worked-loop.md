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
tests = []
for spec in specs:
    t = client.tests.create(product_id=product.id, specification_id=spec.id,
                            type="ACCURACY", name=f"tests for {spec.id}")
    tests.append(t)

for t in tests:                       # poll each generation job to completion
    while True:
        status = client.jobs.get_status(t.job_id)   # verify field/method via docs
        if status in ("SUCCESS", "FAILED"):
            break
        time.sleep(5)

# ── Stage 3: run the product and evaluate ───────────────────────────────
def my_agent(messages: list[dict]) -> str:      # annotate the first param deliberately
    return my_product.respond(messages)          # the user's AI product

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
# Link each test to its spec; poll the returned job until terminal.
for SID in $(galtea specifications list --product-ids "$PID" -o json | jq -r '.[].id'); do
  JOB=$(galtea tests create productId: "$PID", specificationId: "$SID", \
        type: ACCURACY, name: "tests for $SID" -o json </dev/null | jq -r .jobId)
  while [ "$(galtea jobs get-status "$JOB" -o json | jq -r .status)" = "PENDING" ]; do
    sleep 5
  done
done

# ── Stage 3: evaluate the whole version (needs an EndpointConnection on the version) ──
# create-from-version cascades specs -> metrics -> tests -> evaluations.
# See the galtea skill's references/evaluate-version.md for the full async lifecycle.
galtea evaluations create-from-version versionId: <versionId> </dev/null
while [ "$(galtea evaluations list --version-ids <versionId> --statuses PENDING -o json | jq 'length')" -gt 0 ]; do
  sleep 5
done

# ── Read outcomes ──────────────────────────────────────────────────────
galtea evaluations list --version-ids <versionId> -o json \
  | jq '.[] | {id, specificationId, status, score, reason}'
```

## Stage 4 — iterate (both surfaces)

With `results` in hand, group failures by specification and decide:

- **Failing `CAPABILITY`/`POLICY` cases** → propose product changes (prompt, tools, retrieval) that address the evaluator's `reason`.
- **Failing `INABILITY` cases** → the product did something it must not; propose a guardrail change.
- **A "failure" that is actually acceptable behavior** → the spec is wrong/ambiguous; propose editing the spec, not the product.
- **`SKIPPED` evaluations** → usually a wiring issue (a non-`SUCCESS` test, or the agent crashed), not a real product failure — fix the wiring and re-run.

Then create a **new version** and re-run stage 3 (or stages 1–2 if specs/tests changed) so iterations are comparable, and report the pass-rate delta against the previous version.
