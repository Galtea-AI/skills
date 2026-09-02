---
name: galtea-loop
description: Run the full Galtea improvement loop for an AI product -- define product specifications, generate test cases from them, evaluate the product against those datasets, then iterate the product (and the specs) based on which test cases failed. Use when the user wants to measurably improve an AI agent/product with Galtea end to end, set up a repeatable eval-and-iterate cycle, or asks "how do I make my agent better with Galtea" rather than a single one-off Galtea call. Vendor-neutral: works when driven by Claude Code, Codex, Cursor, or any Agent-Skills-compatible coding agent, using either the `galtea` CLI or the Python SDK.
---

# Galtea Loop

This skill guides a coding agent through the **full Galtea improvement loop** for a user's AI product: a repeatable cycle that turns "I built an agent and I'm not sure how good it is" into "here are the behaviors it fails, and here are concrete changes to fix them." You orchestrate the whole loop so the user never has to stitch the SDK/CLI calls together by hand.

The loop has four stages that feed back into each other:

```
  ┌────────────────────────────────────────────────────────────┐
  │                                                              │
  ▼                                                              │
1. Define specs  →  2. Generate datasets →  3. Evaluate  →  4. Iterate
   (what the           (test cases per        (run the        (fix the product
    product should      specification,         product,        and/or the specs,
    do / must not do)   async job)             score it)       then loop)
```

This skill is **vendor-neutral** and **surface-neutral**: it must work whether it is driven by Claude Code, Codex, Cursor, or another Agent-Skills host, and whether the agent drives Galtea through the `galtea` CLI or the Python SDK. It relies only on capabilities every such host has (running shell commands, reading/writing files) and never assumes a specific host's tools.

## Relationship to the `galtea` skill

This skill covers the **orchestration** of the loop. The mechanics of talking to Galtea -- installing the CLI, authenticating, discovering the exact command/method shapes, and the runtime gotchas -- live in the companion **`galtea`** skill. Follow it for:

- **Install & auth:** get `galtea --version` and `galtea whoami` (or `pip install galtea` + `GALTEA_API_KEY`) working before stage 1.
- **Command/method discovery:** the authoritative argument shapes come from `galtea <noun> <verb> --help` and the docs (`https://docs.galtea.ai/llms.txt`, any page + `.md`). **Everything shown inline in this skill is illustrative, not authoritative** -- Galtea ships often, so verify enum values and field names against `--help`/docs before relying on them.
- **Gotchas:** non-TTY stdin hangs on body-taking CLI commands (`</dev/null`), async polling, `--ids` filter pitfalls, duplicate-name `400`s, credit consumption. Do not re-derive these -- read them from the `galtea` skill.

If the `galtea` skill is not available in the host, fall back to `https://docs.galtea.ai/cli/installation.md`, `https://docs.galtea.ai/cli/usage.md`, and `https://docs.galtea.ai/sdk/installation.md`.

## Choosing the surface: CLI or SDK

Decide once, up front, from the user's environment -- then keep the loop on that surface (mixing is safe, but one surface is simpler to follow):

- **Python SDK (`pip install galtea`)** when the user is in a Python project **and** you will run their AI product from Python. Stage 3 is the deciding factor: the SDK runs the agent against the test cases and scores it for you (see Stage 3). Prefer the SDK whenever the product itself is Python-callable.
- **`galtea` CLI** when Python is unavailable/unwanted, when the product is reached over HTTP via an `EndpointConnection` (so Galtea calls it for you and you never run it locally), or when the host agent should drive everything through shell commands. With an endpoint connection, `galtea evaluations create-from-version` cascades specs → metrics → datasets → evaluations in one call.

If unsure, ask the user whether their product is callable from Python or reachable over an HTTP endpoint, and pick accordingly. See the `galtea` skill's `references/cli-vs-sdk.md` for the full decision framework.

---

## Stage 1 — Define specs

Goal: a `Product` and a set of `Specification`s describing what it should do. Specifications are the backbone of the whole loop -- datasets, metrics, and evaluations all hang off them.

Specification types (verify current enum via `--help`/docs):

- **`CAPABILITY`** — something the product *should* do (e.g. "answers billing questions accurately").
- **`INABILITY`** — something the product *must not* do (e.g. "never gives medical advice").
- **`POLICY`** — a rule/constraint it must follow (e.g. "always cites a source").

**Draft specs from the user's codebase, don't invent them.** If the host has an internal spec-drafting skill (e.g. `generate-ai-spec`), compose it to read the product's code/prompts and propose a spec set for the user to approve. Otherwise, read the product's system prompts, tools, and docs yourself and propose the list. Either way, get the user's sign-off on the spec list before creating them -- specs steer everything downstream. Read `https://docs.galtea.ai/sdk/tutorials/writing-specifications.md` for what makes a good spec.

Create the product, then each approved spec. **Product creation requires a `description`, and the SDK has no `products.create`** — create the product via the CLI (or platform), then reference it from the SDK with `client.products.get_by_name(...)`.

```bash
# CLI (see galtea skill for the </dev/null stdin gotcha). name AND description required.
galtea products create name: "Support Agent", \
  description: "Customer support agent for billing questions" </dev/null
# A specification needs a name too; the spec schema does not mark it required.
galtea specifications create productId: <productId>, name: "billing-questions", \
  type: CAPABILITY, \
  description: "Answers billing questions using the customer's plan data" </dev/null
```

```python
# SDK -- product must already exist (created via CLI/platform); fetch it.
product = client.products.get_by_name("Support Agent")
spec = client.specifications.create(
    product_id=product.id,
    name="billing-questions",   # required, and the spec schema does not say so
    type="CAPABILITY",       # CAPABILITY / INABILITY need no dataset_type; POLICY does (see Stage 2)
    description="Answers billing questions using the customer's plan data",
)
```

Verify with `galtea specifications list --product-ids <productId>` / `client.specifications.list(...)`.

## Stage 2 — Generate datasets

Goal: for each specification, a `Dataset` whose generated `TestCase`s probe whether the product upholds that spec. Generation is **asynchronous** — poll until the dataset is ready.

`DatasetType` reads differently per surface: the SDK prefers `ACCURACY` / `SECURITY` / `BEHAVIOR` and also accepts the wire values, while the CLI and raw API take only the wire values `QUALITY` / `RED_TEAMING` / `SCENARIOS`. The `galtea` skill's dataset-types table is the reference. What the loop adds on top is how each type generates and how it wires to a spec — the spine that is easiest to get silently wrong:

- **Behavior** (`BEHAVIOR` in the SDK, `SCENARIOS` on the CLI) generates test cases **directly from the spec**, no extra input — the simplest starting point. Accuracy generation instead needs an uploaded dataset/ground-truth file or a source dataset, and Accuracy/Security require a `dataset_variant`. To upload a file the user already has, rather than generate, the `galtea` skill's `references/custom-dataset-upload.md` holds the CSV columns and the upload path.
- **`POLICY`** specs take a `dataset_type` at creation. **`POLICY` and `CAPABILITY`** specs can both link a metric (`specifications.link_metrics`) — and a linked metric is what makes an evaluation produce a score; `INABILITY` cannot. **`CAPABILITY`/`INABILITY`** specs omit `dataset_type` and derive their datasets/metrics through the spec-driven flow (`https://docs.galtea.ai/sdk/tutorials/specification-driven-evaluations.md`).
- **The parameter name split in the rename.** The wire field stays `testType` (and `testVariant`), so the CLI keeps `testType: SCENARIOS`. The SDK parameters are now `dataset_type` and `dataset_variant`; `test_type` and `test_variant` still work as deprecated aliases and emit a warning.

```bash
# CLI: create a Behavior dataset linked to a spec, then poll the DATASET status.
# The CLI takes the wire value SCENARIOS here, not BEHAVIOR.
galtea datasets create productId: <productId>, specificationId: <specId>, \
  type: SCENARIOS, name: "Billing behavior" </dev/null
# datasets create is async and returns no job id — poll the dataset's own status.
galtea datasets get <datasetId> -f body.status   # wait for SUCCESS
```

```python
# SDK
import time
dataset = client.datasets.create(
    product_id=product.id, specification_id=spec.id,
    type="BEHAVIOR", name="Billing behavior", max_test_cases=3,
)
# datasets.create returns NO job id — poll the dataset's own status to a terminal state.
while not str(client.datasets.get(dataset.id).status).endswith(("SUCCESS", "FAILED")):
    time.sleep(5)
```

Confirm the dataset reaches `status: SUCCESS` before stage 3 (a non-`SUCCESS` dataset is skipped silently during evaluation — see the `galtea` skill's Gotchas). Inspect the generated cases with `client.test_cases.list(dataset_id=dataset.id)`. If the host has a test-authoring skill (e.g. `create-quality-tests`, `create-test`), compose it here for richer test-case content. Dataset generation consumes credits — see the `galtea` skill's credit-check note.

## Stage 3 — Evaluate

Goal: run the product against the generated test cases and score each one. This is the stage that decides CLI vs SDK.

**SDK (product is Python-callable):** run the agent and evaluate in one pass via `evaluations.run`. Annotate the agent's first parameter deliberately — `def my_agent(messages: list[dict]) -> str` is the safe default. The SDK infers the input shape from that annotation, and getting it wrong is a known footgun; the `galtea` skill's `references/cli-vs-sdk.md` has the full annotation→shape mapping.

```python
def my_agent(messages: list[dict]) -> str:
    return my_product.respond(messages)   # the user's AI product

version = client.versions.create(product_id=product.id, name="v1")   # comparable across iterations
# evaluations.run is the agent-callback entry point: it drives the agent against
# the spec's datasets (via the conversation simulator for Behavior) and scores each.
# (Not traces.create_and_evaluate — that logs one pre-computed output.)
client.evaluations.run(
    version_id=version.id,
    agent=my_agent,
    specification_ids=[spec.id],
)
```

**CLI (product reached over an HTTP `EndpointConnection`):** create a `Version` with an endpoint connection so Galtea calls the product for you, then evaluate the whole version at once:

```bash
galtea evaluations create-from-version versionId: <versionId> </dev/null
```

This is the exact flow walked end-to-end in the `galtea` skill's `references/evaluate-version.md` (find product → version → create → list PENDING → poll). Follow it for the async lifecycle.

**Read the outcomes** (either surface): `galtea evaluations list --version-ids <versionId>` / `client.evaluations.list(version_id=...)`, capturing `status`, `score`, and `reason` per evaluation. Evaluations consume credits.

## Stage 4 — Iterate

Goal: turn results into concrete next actions, then loop. This is where the skill earns its keep — don't just dump scores.

1. **Surface what failed, grouped by specification.** For each spec, list its failing test cases with their `score` and the evaluator's `reason`. Make it obvious *which behaviors* are weak, not just an aggregate number. Treat `FAILED`/`SKIPPED` and low-score `SUCCESS` evaluations distinctly (a `SKIPPED` often means a wiring problem, e.g. a `PENDING` dataset or a crashed agent, not a real product failure).
2. **Propose concrete changes**, split into two buckets:
   - **Product changes** — specific edits to the agent's prompt, tools, retrieval, or logic that would fix the failing cases. Point at the offending behavior and the reason.
   - **Spec changes** — if a "failure" is actually a mis-stated or missing specification (the spec is wrong, ambiguous, or the product's behavior is acceptable), propose editing the spec instead. Specs are hypotheses too.
3. **Re-run the loop.** After the user applies changes, create a new `Version` (so results are comparable across iterations) and go back to stage 3 — or to stage 1/2 if specs or datasets changed. Report the delta against the previous iteration (which specs improved, which regressed).

Repeat until the failing set is empty or the user is satisfied with the pass rate.

---

## Worked example

For a complete, runnable pass through all four stages — a Python SDK script and the equivalent CLI session — read [references/loop-walkthrough.md](references/loop-walkthrough.md). Fetch it whenever the user wants to see the loop concretely rather than stage by stage.

## When not to use this skill

- **A single Galtea action** (list products, kick off one evaluation, look up a concept) — use the `galtea` skill directly; the loop is overkill.
- **Building the AI product itself** — this skill evaluates and iterates a product, it doesn't author the product's core logic.
- **Pure UI browsing** — point the user at `https://platform.galtea.ai` to view results visually.

## Skill feedback

If this skill's loop guidance is wrong, outdated, or missing something, offer to file feedback with the maintainers — follow the process in the `galtea` skill's `references/skill-feedback.md`. For issues with the Galtea product itself (not this skill), direct the user to `support@galtea.ai`.
