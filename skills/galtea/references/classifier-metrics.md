# Classifier metrics

Read `/concepts/metric/classifier.md` before creating one. The shape that differs from a judge metric:

- Send `source: CLASSIFIER` and a `classifierQuestion` -- never a `judgePrompt` or `judgeGenerationSettings` (both refused). `classifierQuestion` is refused on every other source.
- `classifierQuestion.type` is `yes_no` (with a required `goodAnswer: yes|no` and optional `criteria{yes, no}`) or `scale` (with `levels[{description, score?}]`, worst first, 2-10 levels (2-9 with `doesNotApply`), and an optional `doesNotApply` text). `score` is all or nothing: give a score on every level or on none (the scores are then spaced evenly), and a level's score must not be lower than the one before it.
- `instructions` must name at least one `{{ param }}` placeholder (`input`, `actual_output`, `expected_output`, `context`, `retrieval_context`, `goal`, `product_description`, `expected_tools`, `tools_used`, `traces`). `evaluationParams` is derived from those placeholders: omit it, or send exactly the derived list.
- `evaluatorModelName` can be omitted only while the deployment has exactly one selectable classifier model; with several, the API answers `400` and lists the names to choose from.
- SDK: `metrics.create(name=..., source="classifier", classifier_question=ClassifierYesNoQuestion(instructions=..., good_answer="no"))`, or `ClassifierScaleQuestion(...)`; a plain dict also works.
- Preview a question before saving with `galtea metrics test-draft` (saves nothing, spends no credits). `galtea metrics propose-classifier-conversion <id>` proposes classifier questions from an existing judge metric.
- Scoring: yes/no scores the probability of the good answer; scale scores the probability-weighted level scores. A "does not apply" answer lands as `SKIPPED`. The `reason` is a fixed sentence (the model writes none); the detail is in the evaluation's `scoreBreakdown`. One credit per evaluation.
- Limits: text only (attached files are not sent), questions in English, long inputs are skipped or flagged "Long input" and left out of pass rates.
