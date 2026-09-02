# Entity revisions and versioning

Galtea keeps history on three entities: `Version`, `TestCase`, and `Metric`. Each one works
differently, and the word "revision" means something weaker on `Version` than on the other two.
Read the table below before you tell a user what the platform can do -- the wrong answer here is
common, because the three mechanisms do not share a vocabulary.

## What has history, and what does not

| Entity | How a new one is made | Does the old one retire? | Family key |
|---|---|---|---|
| `Version` | Create a **new** version with `parentVersionId` | **No.** Both stay active and runnable | none |
| `TestCase` | **Edit it.** A content edit forks automatically | Yes, the old row is marked legacy | `testCaseGroupId` |
| `Metric` | Create a **new** metric with `parentMetricId` | Yes, the parent is flipped to legacy | `metricGroupId` |

**`Dataset` itself has no revisions.** This is the most common wrong answer. A `Dataset` (the
`/tests` wire path) carries no lineage and no legacy marker. Its `sourceTestId` records that it
was copied or extended from another dataset, which is provenance, not versioning. Only the
individual `TestCase` rows inside it revision. So when a user asks for "dataset versioning",
the honest answer is: you version each test case, and the dataset is the container those
revisions live in. Do not claim a dataset can be rolled back to an earlier state.

Nothing else on the platform has revisions. `Product`, `Specification`, `Session`, `Trace`,
`Span`, and `Evaluation` have none.

## There is no revision number

No entity carries a revision counter. Nothing answers "which revision is this" or "how many
exist in this family". You cannot render `revision 3 of 5`, because neither number exists. Order
within a family is only inferrable from `createdAt` plus walking the parent links one hop at a
time. Never invent a revision number in output you show a user.

## Reading history: expect one hop, not a tree

**No endpoint returns an entity's revision history.** There is no `GET /<entity>/:id/revisions`,
no ancestor query, and no filter on the family key -- `GET /metrics` accepts no `metricGroupIds`
and `GET /testCases` accepts no `testCaseGroupIds`. The family keys are labels you can read off a
row, with no matching lookup to search by.

So to reconstruct a family you must list broadly and group client-side:

```bash
# Every revision of every test case in one dataset, then group by testCaseGroupId yourself.
# Superseded rows come back by default, so no flag is needed to see them.
galtea test-cases list --test-ids <datasetId>
```

`Version` is the one exception, and only downward by one hop:

```bash
# The versions created directly from this one. Children only, one level.
galtea versions list --parent-version-ids <versionId>
```

## `--include-legacy` includes superseded rows by default

Both list endpoints **include** superseded revisions unless you say otherwise:

| Command | Default | So a bare list gives you |
|---|---|---|
| `galtea test-cases list` | include | every revision, superseded ones mixed in |
| `galtea metrics list` | include | every revision, superseded ones mixed in |

So a bare `galtea test-cases list` returns rows the user thinks they replaced, and counting them
overstates the dataset size. The same is true of a metric list. Whenever you report a count or
show the user "what is in here now", pass the flag off.

**To turn a boolean flag off you must use the `=` form.** Write `--include-legacy=false`, never
`--include-legacy false` -- these are bare flags, so the space form leaves the flag true and
`false` is parsed as a positional argument.

The test-case parameter is documented as "include superseded revisions", but the metric one is
documented as "include legacy/deprecated metrics" and states no default. That wording invites two
mistakes: assuming metrics behave the opposite way, and telling a user Galtea deprecated their
metric when in fact their own revision superseded it.

## Per entity

### Version -- provenance only

A version is never forked and never retired. `PATCH /versions/:id` edits in place, so an edit
loses the previous state with no history kept. Lineage is a label saying where this version came
from.

```bash
# A "revision" is just a new version that names its parent.
galtea versions create productId: <id> name: "v2" parentVersionId: <parentId> </dev/null
```

The parent stays fully active: it still runs, still evaluates, and still appears in a bare list.
The parent must belong to the same product or the write is refused.

Python SDK: `versions.create(..., parent_version_id=...)`, `versions.list(parent_version_id=...)`,
and `parent_version_id` on the returned model. This is the best-covered of the three in the SDK.

### TestCase -- forks on edit, silently

**A content edit does not update a test case. It creates a new revision and retires the old
row.** Tell the user this before they edit, because nothing in the command output says it
happened.

A fork is triggered only by a change to evaluation content: `input`, `expectedOutput`, `context`,
`expectedTools`, `scenario`, `userPersona`, `goal`, `stoppingCriterias`, `maxIterations`,
`languageCode`, or `gender`. Everything else edits in place, including the annotation fields
(`userScore`, `userScoreReason`, `reviewedById`) and `variant`. Re-sending an unchanged value is a no-op and
forks nothing.

```bash
# This forks. The old row stays, marked legacy, and this returns a NEW id.
galtea test-cases update <testCaseId> input: "the corrected question" </dev/null
```

Two consequences worth stating to a user:

- **The returned id is new.** Anything holding the old id now points at a superseded row. Compare
  the id in the response to the one you sent to detect that a fork happened.
- **Human annotations do not carry over.** The new revision resets `userScore`,
  `userScoreReason`, and `reviewedById`, because those scores were given to the old content. A
  user who has spent review effort on a test case loses it on a content edit. Say so first.

A family always has exactly one active revision. Only a row that is neither legacy nor deleted
can be edited; editing a superseded revision is refused.

Python SDK: **`test_cases` has no `update()`**, so an SDK-only user cannot create a test-case
revision at all. Route them to the CLI or the dashboard. The SDK can read the lineage fields
(`legacy_at`, `test_case_group_id`, `parent_test_case_id`) and filter with
`test_cases.list(include_legacy=...)`.

### Metric -- a revision is created, not edited

`PATCH /metrics/:id` **never** forks, and it accepts only `name`, `description`, and `tags`.
Anything else is refused. So a judge prompt, an evaluator model, or a scoring schema cannot be
edited at all -- changing one means creating a revision.

```bash
# Creating with a parent joins its family and flips the parent to legacy.
galtea metrics create name: "answer-relevancy-v2" parentMetricId: <parentId> </dev/null
```

Optimization is the same mechanism run by the platform: `galtea metrics optimize <id>` creates a
revision in the source's family and only retires the source once the run succeeds, so a failed
optimization leaves the original usable.

Python SDK: the weakest coverage of the three, and worth warning a user about.
**`metrics.create()` takes no `parent_metric_id`**, so the SDK cannot create a metric revision.
The returned model also has **no** `parent_metric_id` field, and unknown fields are ignored
rather than surfaced, so the parent link the API sends is dropped without an error. An SDK user
therefore cannot read a metric's parent either. Use the CLI for anything involving metric
lineage.

## Re-running after a revision

A new revision does not re-score anything by itself. Existing evaluations still belong to the
revision they ran against. Two commands replay onto the new revision:

```bash
galtea test-cases get-replay-candidate-count <testCaseId>
galtea metrics get-replay-candidate-count <metricId>
```

Then `galtea evaluations replay-from-test-cases` or `galtea evaluations replay-from-metrics`.
Both return `202` and cost credits, so surface the candidate count to the user and get their
agreement before replaying.

## Answering "can I version X?"

- **A product version:** yes, as provenance. The old version keeps working.
- **A test case:** yes, and it happens automatically when the content changes.
- **A judge or metric:** yes, by creating a revision. Not by editing it.
- **A whole dataset:** no. Version the test cases inside it.
- **Anything else:** no.

For the current argument shapes always run `galtea <noun> <verb> --help`, and read
`https://docs.galtea.ai/concepts/product/dataset/test-case.md` for the test-case revision rules
as the platform documents them.
