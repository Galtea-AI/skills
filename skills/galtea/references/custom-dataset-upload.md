# Upload a dataset the user already has

Use this when the user has test content and wants it on the platform, instead of asking Galtea
to generate it. Two paths, and they compose:

| The user has | Path | Surface |
|---|---|---|
| A CSV of rows | Upload the CSV as the dataset file | SDK, dashboard |
| Documents, images, or text and data files to attach to rows | Presign, PUT the bytes, reference the URI in the row | SDK, or CLI plus `curl` |
| Both | Upload the files first, then reference them from the CSV | SDK does both in one call |

**Prefer the Python SDK for any upload.** The CLI can mint an upload URL and create a dataset,
but it cannot send file bytes -- see "Sending bytes from the terminal" at the end.

## Why the CSV path, and not a loop of `test-cases create`

Three reasons to state to a user who is about to write a loop:

- **`test-cases create` is one HTTP request per row.** There is no batch create endpoint. Three
  hundred test cases is three hundred round trips, against one PUT plus one POST for the CSV.
- **There is no empty-dataset mode.** Every dataset creation *without* a file goes down the
  generation branch and calls a generator, which **consumes credits**. You cannot create a bare
  dataset and fill it by hand for free.
- **A dataset created from a file skips generation entirely.** No generation credit check, no
  `max_test_cases` ceiling, and the dataset is created already `SUCCESS` with every row
  persisted before the call returns. Skipping generation is not the same as a free import: a
  `credits_used` column in the CSV is still charged, as the column list below says.

## The CSV path

```python
from galtea import Galtea

galtea = Galtea(api_key="gsk_...")

dataset = galtea.datasets.create(
    name="contracts-golden-300",
    product_id="<productId>",
    type="ACCURACY",                      # or "SECURITY" / "BEHAVIOR"
    dataset_file_path="./contracts.csv",  # a local path
)
```

The SDK reads the file, asks the API for an upload URL, PUTs the bytes to object storage, then
creates the dataset pointing at the stored object. Rows are parsed server-side.

`dataset_file_path` replaced `test_file_path`. The old name still works and emits a deprecation
warning; the new name wins if both are given.

**This needs `galtea>=5.0`.** The `datasets` service and `dataset_file_path` arrived in 5.0.0.
On a 4.x install neither exists: `galtea.datasets` raises `AttributeError` and the call is
`galtea.tests.create(test_file_path=...)`. Check with `pip show galtea` before you conclude the
platform is at fault, and upgrade rather than writing to the old surface.

**`Galtea()` takes no host argument.** It reads `GALTEA_API_URL` from the environment and
defaults to production, so a dev key with that variable unset fails with a bare `401` that names
nothing. Export it before the first call when the user is not on production.

**Dataset type names differ per surface.** The SDK takes `ACCURACY` / `SECURITY` / `BEHAVIOR`;
the CLI and raw API take `QUALITY` / `RED_TEAMING` / `SCENARIOS`. Same three things.

### Columns

Required, and this is the whole list:

| Dataset type | Required columns |
|---|---|
| `ACCURACY` (`QUALITY`) | `input` |
| `SECURITY` (`RED_TEAMING`) | `input` |
| `BEHAVIOR` (`SCENARIOS`) | `user_persona`, `scenario`, `goal` -- all three |

Every column below is read for **every** type, with no per-type gating. They are optional except
where the table above makes one required: `user_persona`, `scenario` and `goal` appear here and
are required for `BEHAVIOR`. The table above decides what a type requires.

`expected_output`, `expected_tools`, `context`, `tag`, `strategy`, `scenario`, `user_persona`,
`goal`, `archetype`, `spec_relevance`, `initial_prompt`, `stopping_criterias`, `max_iterations`,
`filename`, `source`, `confidence`, `confidence_score`, `confidence_reason`, `language`,
`gender`, `credits_used`, `source_test_case_id`

Unknown columns are ignored, so an extra id column or an unnamed index column is harmless.

Semantics that are not guessable from the names:

- **`tag` sets the test case's variant.** A column literally named `variant` is silently ignored
  -- use `tag`.
- **`credits_used` is charged to the organization on import.** A value a user copied from an
  export spends real credits. Leave it out unless the user means it.
- `expected_tools` and `stopping_criterias` hold several values, split on `;` or `|`.
- `input` and `context` accept a JSON object in the cell and it is stored as sent. This is how a
  row carries attached files.
- `filename` sets the source-file label; `confidence_score` is a fallback for `confidence`.
- `gender` must be `MALE` or `FEMALE`, case-insensitive. `language` is validated against a list
  and capped at 35 characters; every other column is unbounded text.
- **`initial_prompt` does not satisfy the `input` requirement.** The mapper falls back to it
  when `input` is empty, but the required-column check reads `input` only, so a CSV whose
  question column is named `initial_prompt` fails on an `ACCURACY` or `SECURITY` dataset.
  Rename the column to `input`.
- Only `input` is required for accuracy datasets. `expected_output` is optional, even though a
  golden answer is what makes the dataset useful -- so ask for it rather than assuming the
  platform will.

### One bad row rejects the whole file, and deletes the dataset

Validation is all-or-nothing at three levels: the required-column check stops at the first bad
row, per-row value validation stops at the first bad value, and the insert is one transaction.
The first two run in memory before the transaction opens. **On any failure the dataset row itself
is deleted**, so the user is not left with a half-populated dataset. Fix the CSV and call again.

Two things to warn a user about before they read the error:

- **A missing required column comes back as a bare HTTP 500, not 400.** That message is raised
  as a plain error, so it lands in the server-error branch, and since release 5.3.0 the 500 body
  carries only `Something went wrong`: the row text (`Row 3 is missing required fields: input`)
  exists only in the API log. Every other server fault on this route answers with that same
  body, so the header is the first thing to check, not the proven cause. When the header holds
  every required column, treat the 500 as a real platform error and report it.
- **The row number in that log line counts parsed non-empty rows, not file lines.** Blank lines
  are skipped before numbering, so `Row 12` may not be line 12 of their file. It also excludes
  the header.
- **An invalid value in a row is a normal `400`, and it names the value, not the row.** An
  unrecognised `gender` or `language` throws a bad-request error from a loop that carries no
  index, so the body reads `gender must be one of MALE, FEMALE, got "m"`. Do not send the user
  looking for a row number that the server never sent.

A header-only CSV does **not** error. It creates a dataset with zero test cases.

### Limits

| Limit | Value | Enforced |
|---|---|---|
| Dataset CSV size | 10 MB | Client side only, by the SDK and the dashboard |
| Extension | `.csv` | Client side, plus a server-side check on the URI suffix |
| Row count | none | `max_test_cases` does not apply to an uploaded file |
| Cell length | none | The columns are unbounded text |

The 10 MB cap lives in the client, so state it to the user before they pick a file rather than
letting a large upload fail late. Numbers published elsewhere in the docs (100 MB, 50 MB) belong
to the knowledge-base file and the behavior data catalog, not to this CSV.

## Attaching files to a row

> **Galtea Cloud has the whole feature since release 5.3.0 (2026-09-14), and the matching SDK is
> `galtea>=5.3.0` on PyPI.** Everything from here to the end of the file works on production
> with that pair. Two things still decide whether a snippet runs:
>
> - **The installed SDK.** `pip show galtea` must say `5.3.0` or later. `5.2.0` can upload,
>   attach and download, but its agent callback never sees a file and it has no
>   `galtea.storage.read`. On an SDK older than 5.2.0 the failure reads like a broken library
>   rather than a version floor: `unexpected keyword argument 'input_file_paths'`, no
>   `upload_input_file` attribute, or `ImportError` on `from galtea import InputFile`. Upgrade
>   instead of working around it.
> - **A self-hosted or on-premise deployment**, which can lag the release. `galtea build-info`
>   returns the deployed `version`; attachments need 5.2.0 and delivery to the endpoint or the
>   callback needs 5.3.0. It is restricted to admins and enterprise organizations, so when it
>   answers 403 fall back to the probe `galtea storage generate-put-url --key probe.pdf
>   --file-type file`, which answers `Invalid file type` on a deployment older than 5.2.0. That
>   probe cannot tell 5.2 from 5.3.
>
> If the deployment fails that probe, say so and route the user to the CSV path above.

A test case input can carry uploaded files alongside optional text. Bytes go to object storage
and the input holds a reference. Audio for voice products already works this way; documents and
images use the same envelope.

### The envelope

The `input` cell holds a JSON object with a `content` array. Text is optional and independent:

```json
{
  "user_message": "Summarize this contract",
  "content": [
    {
      "type": "file",
      "uri": "s3://<bucket>/files/<organizationId>/<id>.pdf",
      "filename": "rental-contract.pdf",
      "mimeType": "application/pdf"
    }
  ]
}
```

`type: "file"` and `uri` are the two required fields on a part. **A part with no `type` is
silently ignored**: the API stops treating the array as content parts, so nothing is validated
or canonicalised, and `input_files` never lists the file. `filename` and `mimeType` are
optional, but send both. The auto-fill is weak: Galtea Cloud recovers the uploaded `filename`
(a self-hosted Azure Blob deployment cannot), and `mimeType` is read from the stored object,
which a presigned upload leaves as `application/octet-stream`. A `sizeBytes` field comes back
on every stored file part, measured from the object; a value you send for it is ignored. A
file-only test case is valid: omit `user_message` and send only `content`.

### The SDK does the upload for you

```python
# One test case, files uploaded from local paths.
galtea.test_cases.create(
    dataset_id="<datasetId>",
    input="Summarize this contract",
    input_file_paths=["./rental-contract.pdf"],
)

# Upload once, reference from many test cases.
uploaded = galtea.test_cases.upload_input_file("./rental-contract.pdf")
```

Reading back, `test_case.input_files` lists the attached files with their `uri`, `filename`, and
`mime_type`. That attribute is derived by the SDK from the input itself. **The wire response has
no `inputFiles` field at all**, so an agent checking the raw API or the CLI must read
`input.content` instead; reading a missing field and finding nothing looks like a failed upload.

**The stored `uri` is not the one you sent.** A freshly minted upload URL carries a signature and
expires, and the API replaces it with the canonical signature-free `s3://<bucket>/<key>` form when
the test case is written. Persist that one, and ask the API for a link when you need the bytes
(see "Getting the bytes back").

**`upload_input_file` is the right call when several test cases share one document.** A common
shape is one dataset per pipeline stage, all pointing at the same file. `test_cases.create` does
**not** deduplicate: the same path passed to two calls, or twice in one call, uploads twice.

### The CSV column

Add an `input_file_paths` column. The SDK resolves it before uploading the CSV: it uploads each
local file, rewrites the row's `input` cell into the envelope above, and drops the column. Several
paths in one cell are split on `;` or `|`.

```csv
input,expected_output,input_file_paths
Summarize this contract,"{""holder"": ""M. Ruiz""}",./docs/contract-a.pdf
,"{""holder"": ""J. Casas""}",./docs/contract-b.pdf
```

The second row has no text, only a file. That is accepted.

**A cell may also name a file already in storage.** A value starting with `s3://`, `http://`, or
`https://` is treated as a reference, not a local path: nothing is read from this machine and
nothing is uploaded, and the URI is written into the row as sent. This is how a second dataset
points at a document the first one uploaded, which is the normal shape when one document is scored
at several pipeline stages. A reference does not count against the local size total, but the SDK
still checks its extension, read from the uri, and refuses a uri whose path has no `/files/`
segment (an `audio/` object or a dataset CSV) before anything is uploaded.

Within one `datasets.create` call the CSV path **does** deduplicate by absolute path, so the same
document referenced by twenty rows uploads once. Every row is validated before the first upload,
so a bad path costs no transfer. Row errors are prefixed with the row number. A CSV that carries
this column must be UTF-8.

### Accepted file types

The platform validates the extension against a fixed list before it accepts the file:

| Group | Extensions |
|---|---|
| Documents | `pdf`, `docx`, `xlsx`, `pptx`, `rtf` |
| Images | `png`, `jpg`, `jpeg`, `tiff`, `tif`, `bmp`, `webp`, `heic`, `heif`, `gif` |
| Text and data | `txt`, `csv`, `md`, `html`, `xml`, `json`, `eml` |

Anything else is refused, and the error names the accepted list. **Audio is not on it** -- a voice
clip is its own part type, not a file part. Archives, video, and executables are refused too.

State the list to the user *before* they pick a file. The SDK checks the extension locally before
uploading anything, so a wrong type costs no transfer. On the API side the check uses the
`filename` you send, falling back to the stored object's own name, so omitting `filename` does not
slip a type past the list.

### Limits on attached files

| Limit | Value |
|---|---|
| Files per input | 20 |
| Total size per input | 20 MB |

**The size cap is a total across the input, not per file.** Twenty-one files, or 21 MB spread
over three files, is refused. The same uri listed twice counts once toward the size and twice
toward the count.

## Metrics skip on an input they cannot read

A judge cannot read an uploaded file. The judge is sent the session's **turns** with every file
part stripped out, never the test case input, so the turns decide what is skipped:

- **A metric that declares `input` is skipped** when no turn in the session carries readable
  text, with the reason `Evaluators cannot read input files yet.` A turn is readable when it
  yields text and carries **no** file part. Text sent beside the document does not count: it is
  usually a prompt about the document, so scoring it would give a confident number about a file
  the judge never read. A file test case runs as a single turn, so a platform run or an
  `evaluations.run` over one **always** skips those metrics. A skip costs no credits.
- **A metric that declares `conversation_turns` is never skipped** for a file. The turns are
  scored as logged, blank first message included.
- **One readable turn is enough.** An attachment on one turn of a long monitored session leaves
  the rest of the session scorable.

Two ways to get a score on a document, and say them before the user builds the dataset:

1. **Log the extracted text as the turn input, without the file part.** In the manual loop below,
   pass your pipeline's extracted text as `input=` to `traces.create_and_evaluate`, and the
   `input` metrics score that text. `evaluations.run()` cannot override the input at all: it
   writes the file envelope as the turn input, so it keeps skipping those metrics for a document
   row.
2. **Score only the output**, with a built-in deterministic metric such as the JSON Field Match
   family. On Galtea Cloud since release 5.2.0, a JSON metric on an output that is not valid JSON
   is `SKIPPED`, not `FAILED`, so a pipeline that answered prose does not read as a wrong answer. Self-hosted
   metrics, where the user computes the score, are never skipped.

The dashboard's Run Evaluation dialog greys out every metric the run would skip and shows the
reason under its name, and a scored evaluation whose judged turns carried a file warns that the
attachment was not sent to the judge.

**The skip reason is in the evaluation's `error` field, which the SDK does not expose.** A
`SKIPPED` evaluation read through `galtea.evaluations.get` has `reason=None` and no `error`
attribute, so a script cannot tell a file skip from any other skip. Read the row with
`galtea evaluations get <id>` (the `error` field) or in the dashboard.

**The user cannot write their own output-only judge.** `POST /metrics` refuses an AI-evaluated
metric whose `evaluationParams` omits `input` or `actual_output` (`"evaluationParams" must
include "input" — the evaluator always provides these to the judge.`), so every custom judge
reads the input and every custom judge is therefore skipped on a file-carrying input. Output-only scoring means the platform's built-in
deterministic metrics (JSON Field Match, JSON Field Match (Normalized), Text Match, Text
Similarity, URL Validation, ROUGE, BLEU, METEOR, IOU, Spatial Match, Tool Correctness) or a
self-hosted metric. Offer those by name instead of suggesting a custom rubric.

## Running your agent on a file-carrying test case

Three runners deliver the file. Pick by where the user's product runs:

| The product is | Runner | What delivers the file |
|---|---|---|
| Reachable over HTTP | Platform run (dashboard run button, `evaluations create-from-version`, `evaluations.run` without an agent) | `input_files` variables in the endpoint connection template |
| A Python callable | `evaluations.run(agent=...)`, `simulator.simulate`, `traces.generate` | `input_data.input_files` on an `AgentInput` callback |
| Not callable from Python and not reachable over HTTP | A manual loop | `galtea.storage.download` per test case |

All three write the file envelope as the turn input, so the `input` metrics skip as the section
above says. Only the manual loop can log the extracted text instead.

### Platform run: name the file in the template

The endpoint connection template gets an `input_files` collection, one entry per attachment in
`content[]` order, audio parts included. Each entry has exactly four properties, and a template
reading any other one is refused when the connection is saved:

| Property | Value |
|---|---|
| `url` | A presigned download link, valid **15 minutes**, minted fresh on every request attempt |
| `base64` | The file bytes, base64. Read only when the template names it. The 20 MB cap is on the raw bytes, so the encoded body can reach about 27 MB |
| `filename` | The part's `filename`, else the last segment of the storage key (a random id) |
| `mime_type` | The part's `mimeType`, else a type derived from the key's extension, else `application/octet-stream` |

```jinja2
{"question": "{{ input.user_message }}", "document_url": "{{ input_files[0].url }}"}
```

Loop for several attachments, with the usual trailing-comma guard:

```jinja2
{"documents": [{% for file in input_files %}{"url": "{{ file.url }}", "name": "{{ file.filename }}"}{% if not loop.last %},{% endif %}{% endfor %}]}
```

Rules that change what you tell the user:

- **A template that names no `input_files` variable refuses a file-only test case** before the
  run starts, with a `400`: `Test case <id> attaches files and nothing else, but the endpoint
  connection template names no {{ input_files }} variable. Add {{ input_files[0].url }} (or
  .base64) to the template, or run these test cases through your own pipeline.` A test case that
  also carries text **runs on the text alone**, the `202` response carries a `warnings` list
  (`Test case <id> attaches files that are not delivered: ... so only the text is sent.`), and
  the dashboard shows it. Check that list, or a document run silently scores an endpoint that
  never saw the document. `galtea traces generate` (body `{"versionId": ..., "testCaseIds":
  [...]}` as JSON on stdin or as the bracket shorthand `testCaseIds: [a, b]`) and
  `evaluations create-from-version` both go through this gate.
- **Only `url`, `base64`, `filename` and `mime_type` exist.** Saving a template that reads
  another property is refused with `Invalid iterator property access: "input_files.size". Valid
  properties for input_files items are: url, base64, filename, mime_type.`
- **`{{ input.content[0].uri }}` renders nothing.** No unsigned storage URI reaches the endpoint
  any more. `input_files` is the only way to read a file; an audio transcript still arrives in
  `{{ input.user_message }}`.
- **A test case with no attachment is not refused** by a file-aware template: `input_files[0].url`
  renders empty and a loop renders nothing, so one template serves mixed datasets.
- **A run that attaches files against a file-aware template skips the pre-run health check.**
  The probe cannot stand in for a real file, so a broken endpoint shows up as failed turns, not
  as a refused run; every other run is probed as before. The dashboard's Test connection button
  sends a placeholder attachment whose URL points at nothing, so a failed download there is
  expected.
- **A failed fetch on the endpoint's side is the endpoint's error**, recorded on that turn, and
  a failed turn fails its session. A file Galtea itself cannot sign or read fails the turn the
  same way; Galtea never sends the request without the attachment.
- **Import from cURL knows `input_files`.** Paste a request that carries a document, image, or
  audio payload, and the generated template writes the file slot next to `{{ input.user_message }}`.

### SDK callback: annotate the first parameter as `AgentInput`

```python
from galtea import AgentInput, AgentResponse

def my_agent(input_data: AgentInput) -> AgentResponse:
    # The same InputFile objects test_case.input_files returns: uri, filename, mime_type.
    document_bytes = [galtea.storage.read(f) for f in input_data.input_files]
    answer = my_pipeline(question=input_data.last_user_message_str(), documents=document_bytes)
    # `content` is a str: json.dumps() a dict so JSON Field Match can parse it.
    return AgentResponse(content=answer)

galtea.evaluations.run(version_id=version.id, agent=my_agent)
```

- **Only the `AgentInput` signature (or an `Agent` subclass) receives files.** A `(str)` or
  `(list[dict])` agent carries text only. With text beside the files the SDK **warns once per
  agent** and runs it on the text. With files and no text it raises `FileOnlyTextAgentException`
  before calling the agent, marks that trace `FAILED` with a message naming the fix, and each
  runner then stops differently: `evaluations.run` continues with the next test case,
  `simulator.simulate` ends that conversation with a stopping reason, and `traces.generate`
  re-raises to the caller. A silent empty input is never delivered.
- `input_data.input_files` is the last user message's files, which is the current turn during a
  run. Each `ConversationMessage` keeps its own `input_files`, so an earlier turn's document
  stays reachable through `messages`. `ConversationMessage.metadata` is unchanged, so a voice
  agent reading `message.metadata["content"]` keeps working.
- `galtea.storage.read(file)` returns the bytes without touching disk, for a multimodal request or
  an in-memory parser; `galtea.storage.download(file, output_directory=...)` writes a file for a
  library that needs a path. Both take an `InputFile` or a plain uri.
- `simulator.simulate` runs the agent on a file-only turn (the API opens it with an empty message
  and the files on the trace), but `max_turns>1` is refused: see "What a file-carrying test case
  cannot do".

### Manual loop: the only path that scores the document's text

The docs tutorial "Evaluate Document Inputs" (`/sdk/tutorials/evaluate-document-inputs`) walks
this end to end, so fetch that page for the full runnable version:

```python
# include_legacy=False, or an edited test case comes back once per revision and you
# download the same document again for each one.
for tc in galtea.test_cases.list(dataset_id=dataset.id, include_legacy=False):
    # 1. Fetch each attached file, saved under its uploaded name.
    paths = [galtea.storage.download(f, output_directory=workdir) for f in tc.input_files]
    # 2. Call the user's own pipeline with the text and the local files.
    extracted_text, answer = my_agent(question=tc.input, document_paths=paths)
    # 3. Record the answer against the test case and score it in one call. Logging the
    #    extracted text as `input`, with no file part, is what lets `input` metrics score.
    session = galtea.sessions.create(version_id=version.id, test_case_id=tc.id)
    galtea.traces.create_and_evaluate(
        session_id=session.id,
        input=extracted_text,
        output=answer,
        metrics=[{"name": "JSON Field Match"}],
    )
```

`tc.input` is the `user_message` text as a plain string, and it is `None` when the row carries
files and no text. The full envelope is `tc.input_data` and the file parts are `tc.input_files`,
so send your pipeline `tc.input` for the text and the fetched files separately. Leave `input` out
of the trace and the evaluation reads the test case input, file parts included, and the `input`
metrics skip. Wrap the download in `try`/`except` if one unreadable document should not stop the
rest.

## Getting the bytes back

A stored `uri` is a reference, not a link. From the SDK, one call saves the file:

```python
# Takes an InputFile straight from a test case, or any uri this organization uploaded.
path = galtea.storage.download(test_case.input_files[0], output_directory="./docs")
# The bytes in memory, no file written (galtea>=5.3.0).
data = galtea.storage.read(test_case.input_files[0])
# The upload side is public too: returns the stored uri.
uri = galtea.storage.upload("./rental-contract.pdf")
```

**`galtea.storage` is public since SDK 5.2.0** (`upload` and `download`); `read` arrived in
5.3.0. A 5.1 or older SDK has no storage service at all, so `hasattr(galtea, "storage")` is the
probe and the two commands below are the fallback. The SDK reference is
`/sdk/api/storage/service` on the docs site, with `/sdk/api/storage/download`,
`/sdk/api/storage/read` and `/sdk/api/storage/upload` underneath.

`download` saves under `filename=` when you pass one, else under the `InputFile`'s own
`filename`, else under the random storage id. It raises rather than returning `None`: `ValueError` when
no uri was given or the value names a file on this machine (the mistake of passing `download` the
path meant for `upload`), and a plain exception for a refused uri or a failed transfer. The error
never contains the presigned URL. A fresh uri straight from an upload still carries its signature;
`download` strips it before asking for a link, so both forms name the same object.

Without that SDK, or from a terminal, ask the API for a link and fetch it:

```bash
# Works for an attached file (input.content[].uri) and for a dataset's own uri alike.
galtea storage generate-get-url --uri "s3://<bucket>/files/<organizationId>/<id>.pdf"
curl -sL -o rental-contract.pdf "<downloadPresignedUrl>"
```

- **The response is `{downloadPresignedUrl}`.** The `--help` and OpenAPI text say `{url}`, the same
  spec error as the upload call. Do not read `url`.
- **The link lives 24 hours** and is a read capability for that object: do not paste it into a
  reply or a log. Store the `uri`, mint a link when you need one. The `input_files[].url` a
  platform run hands the endpoint is a different mint and lives 15 minutes.
- **Another organization's file answers `404 File not found`**, never `403`, so the platform does
  not confirm the file exists. A platform admin key is exempt and can read any organization's
  file; do not mistake that for the rule.
- **`galtea.datasets.download(dataset, output_directory)`** fetches a dataset's own uploaded CSV
  and saves it under the random storage id, not the name the user uploaded. On a newer SDK it
  shares the code path above and raises on failure; on an older one it prints and returns `None`,
  so check the return value when you cannot rely on the version.
- **Many files at once:** `POST /storage/generate-get-presigned-urls` with `{"uris": [...]}`
  returns `{downloadPresignedUrls: {<uri>: <link>}}` and silently omits any uri the caller does
  not own or that does not exist. It has no CLI verb, so it is a raw call. At most 100 uris per
  call, and a uri with leading or trailing whitespace makes the whole call a `400`. Compare the
  keys you get back against the ones you sent before assuming every file resolved.

## What a file-carrying test case cannot do

- **Single turn only.** A file satisfies the first turn, and a run that asks for more is refused:
  `Test case <id> attaches files to its input, which the platform supports for single-turn runs
  only. Remove the files, or run this test case as a single turn.` That covers a `BEHAVIOR`
  scenario and `simulator.simulate(max_turns>1)`; neither leaves a turn behind.
- **The judge never sees the file.** Vision judging is deferred. The `input` metrics skip unless a
  turn carries file-free text, as "Metrics skip on an input they cannot read" says.
- **Augment is refused** for a dataset whose test cases carry file or audio parts, with a `400`
  before any credit is reserved; the dashboard disables the button with the same sentence. Extend
  has no media guard, but it re-runs the dataset's original generator, so a dataset built from an
  uploaded CSV is refused too (`This test cannot be extended: it was not generated from a reusable
  source`). More document test cases means uploading more rows. Without the augment guard every
  augmented row pointed at one attachment with invented text about it.
- **An edit can never leave the test case with no file.** Two separate refusals enforce it:
  sending `input` as a plain string is refused, because a bare string would drop every
  attachment; and sending a structured `input` whose `content` keeps no file part is refused too.
  Add, replace, and remove are all fine while at least one file remains -- always include the
  files you want to keep. If the user really wants a text-only test case, delete this one and
  create it fresh. Remember that editing a test case's content forks a new revision and retires
  the old row, so the returned id is new.
- Creating a test case with files requires an authenticated caller, which an API key satisfies.

## Sending bytes from the terminal

The two-step upload, for when the SDK is not an option:

```bash
# 1. Ask for an upload URL. `key` is the FILE NAME, not a storage path.
galtea storage generate-put-url --key "rental-contract.pdf" --file-type file

# 2. PUT the bytes to the uploadPresignedUrl from that response.
curl -X PUT --upload-file ./rental-contract.pdf \
  -H "Content-Type: application/octet-stream" \
  -H "x-ms-blob-type: BlockBlob" \
  "<uploadPresignedUrl>"
```

Then create the test case with the `downloadPresignedUrl` as the part's `uri`. The body goes as
JSON on stdin, and the dataset field is the wire name `testId`:

```bash
cat <<EOF | galtea test-cases create
{"testId": "<datasetId>",
 "input": {"user_message": "Summarize this contract",
           "content": [{"type": "file", "uri": "<downloadPresignedUrl>",
                        "filename": "rental-contract.pdf", "mimeType": "application/pdf"}]}}
EOF
```

Four things to get right here:

- **The response is `{downloadPresignedUrl, uploadPresignedUrl}`.** The OpenAPI description for
  this operation says the body is `{url}`, which is wrong. Do not read `url`; it does not exist.
- **`key` is the file name.** The storage key is minted server-side under the organization's own
  folder. The caller cannot choose where the object lands, and a URI outside that folder is
  refused when the test case is created.
- **`--file-type file`** for a test-case input file. `audio` is for voice, `testFile` is for a
  dataset CSV.
- **Both URLs expire in 24 hours.** An upload URL is a write capability for that object while it
  lives, so treat it as a secret: never echo one into a log, a chat reply, or a bug report.

The `x-ms-blob-type` header is what Azure storage requires on a presigned PUT. S3 ignores it, so
the one command above works on either backend.

**The CLI cannot upload a file itself.** Its command tree is generated from the API spec and only
sends JSON bodies. `field: @/path/to/file` reads the file locally and inlines it into the body: a
`.json` file is parsed as an object, a UTF-8 file becomes a string, and a binary file such as a
PDF becomes a base64 string. None of these is an upload. Use the SDK, or the `curl` step above.
Do not present any other CLI invocation as an upload; a command that appears to accept a path
will silently send the wrong thing.
