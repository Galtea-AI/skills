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
- `filename` sets the source-file label; `confidence_score` is a synonym of `confidence`.
- `gender` must be `MALE` or `FEMALE`, case-insensitive.
- Only `input` is required for accuracy datasets. `expected_output` is optional, even though a
  golden answer is what makes the dataset useful -- so ask for it rather than assuming the
  platform will.

### One bad row rejects the whole file, and deletes the dataset

Validation is all-or-nothing at three levels: the required-column check stops at the first bad
row, per-row value validation stops at the first bad value, and the insert is one transaction.
The first two run in memory before the transaction opens. **On any failure the dataset row itself
is deleted**, so the user is not left with a half-populated dataset. Fix the CSV and call again.

Two things to warn a user about before they read the error:

- **The row number counts parsed non-empty rows, not file lines.** Blank lines are skipped before
  numbering, so `Row 12` may not be line 12 of their file. It also excludes the header.
- **A missing required column comes back as HTTP 500, not 400.** That message is raised as a
  plain error, so it lands in the server-error branch: `Row 3 is missing required fields: input`
  is a user error wearing a 500. Read the `message` field rather than judging by the status.
  An **invalid value** in a row is different: an unrecognised `gender` or `language` throws a
  bad-request error and arrives as a normal `400`, so do not report it as a server fault.

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

> **Check availability before you advise any of this.** Everything from here to the end of the
> file needs two things, and they move independently: the platform release that added uploaded
> test case input files, and an SDK that can send them. Check both, because a deployment can
> carry the API half while the installed SDK cannot reach it.
>
> ```bash
> # The API half: an older deployment answers "Invalid file type".
> galtea storage generate-put-url --key probe.pdf --file-type file
> # The SDK half: ImportError on a version that predates the feature.
> python -c "from galtea import InputFile"
> ```
>
> Every snippet below uses the SDK, so the second check is the one that decides whether the
> examples run. On an older SDK the failure reads like a broken library rather than a version
> floor: `unexpected keyword argument 'input_file_paths'`, or no `upload_input_file` attribute.
> On an older deployment the file type is refused and a test case carries no `input_files`.
> If either check fails, say which half is missing and route the user to the CSV path above
> instead of working around it.

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

`uri` is the only required field on a part. `filename` and `mimeType` are optional and the
platform fills them in from the stored object when it can, but send them -- they are what the
user sees, and auto-fill does not work on every storage backend. A file-only test case is valid:
omit `user_message` and send only `content`.

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
**not** deduplicate: the same path passed to two calls uploads twice.

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
at several pipeline stages. A reference also counts against neither the local size total nor the
extension check, because the API owns that object and validates it on write.

Within one `datasets.create` call the CSV path **does** deduplicate by absolute path, so the same
document referenced by twenty rows uploads once. Every row is validated before the first upload,
so a bad path costs no transfer. Row errors are prefixed with the row number.

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
over three files, is refused.

## Metrics skip on an input they cannot read

A judge cannot read an uploaded file. So **every metric that reads the input is skipped, not
scored**, with the reason `Evaluators cannot read input files yet.` That covers any metric
declaring `input` or `conversation_turns` among its evaluation parameters, and it applies whether
or not there is text beside the file. A skip costs no credits.

This is the single most important thing to tell a user before they build a document dataset.
The workflow that does work: run the document through their own pipeline, upload the output, and
score the output with output-only metrics such as the JSON field match family. Self-hosted
metrics, where the user computes the score, are never skipped.

**The user cannot write their own output-only judge.** `POST /metrics` refuses an AI-evaluated
metric whose `evaluationParams` omits `input` (`"evaluationParams" must include "input" -- the
evaluator always provides these to the judge`), so every custom judge reads the input and every custom judge is
therefore skipped on a file-carrying input. Output-only scoring means the platform's built-in
deterministic metrics (JSON Field Match, JSON Field Match (Normalized), Text Match, Text
Similarity, URL Validation, ROUGE, BLEU, METEOR, IOU, Spatial Match, Tool Correctness) or a
self-hosted metric. Offer those by name instead of suggesting a custom rubric.

## Running your agent on a file-carrying test case

The platform does not run the inference for you, and neither runner delivers the file to the
agent. Say this before the user wires anything up:

- **An endpoint connection** renders the raw input envelope into the request template. The
  endpoint receives the stored `s3://` reference, which it cannot fetch, and no download link is
  ever exposed to the template.
- **The SDK agent callback** (`evaluations.run` with an `Agent`) gets the text in
  `message.content` and the file parts under `message.metadata["content"]`, still as stored
  references. Nothing fetches them for you; the callback has to download them itself.

The loop that works today is manual. It is the same one the docs tutorial "Evaluate Document
Inputs" (`/sdk/tutorials/evaluate-document-inputs`) walks end to end, so fetch that page for the
full runnable version:

```python
# include_legacy=False, or an edited test case comes back once per revision and you
# download the same document again for each one.
for tc in galtea.test_cases.list(dataset_id=dataset.id, include_legacy=False):
    # 1. Fetch each attached file, saved under its uploaded name. On an older SDK this
    #    is a raw request instead, see "Getting the bytes back".
    paths = [galtea.storage.download(f, output_directory=workdir) for f in tc.input_files]
    # 2. Call the user's own pipeline with the text and the local files.
    answer = my_agent(question=tc.input, document_paths=paths)
    # 3. Record the answer against the test case and score it in one call.
    session = galtea.sessions.create(version_id=version.id, test_case_id=tc.id)
    galtea.traces.create_and_evaluate(
        session_id=session.id, output=answer, metrics=[{"name": "JSON Field Match"}]
    )
```

`tc.input` is the `user_message` text as a plain string, and it is `None` when the row carries
files and no text. The full envelope is `tc.input_data` and the file parts are `tc.input_files`,
so send your pipeline `tc.input` for the text and the fetched files separately. The trace needs
no `input`: the session is linked to the test case, and the evaluation reads the input, file
parts included, from there. Wrap the download in `try`/`except` if one unreadable document
should not stop the rest.

Every metric that declares `input` is still skipped on that evaluation; score the output with
output-only or self-hosted metrics as the section above says.

## Getting the bytes back

A stored `uri` is a reference, not a link. From the SDK, one call saves the file:

```python
# Takes an InputFile straight from a test case, or any uri this organization uploaded.
path = galtea.storage.download(test_case.input_files[0], output_directory="./docs")
# The upload side is public too: returns the stored uri.
uri = galtea.storage.upload("./rental-contract.pdf")
```

**`galtea.storage` is public only on a newer SDK**, where both methods arrive together. On an
older one the service exists but is private, so `hasattr(galtea, "storage")` is the probe, and
the two commands below are the fallback. The SDK reference for both methods is
`/sdk/api/storage/service` on the docs site, with `/sdk/api/storage/download` and
`/sdk/api/storage/upload` underneath.

`download` saves under the `InputFile`'s `filename`, or under `filename=` when you pass one, and
otherwise under the random storage id. It raises rather than returning `None`: `ValueError` when
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
  reply or a log. Store the `uri`, mint a link when you need one.
- **Another organization's file answers `404 File not found`**, never `403`, so the platform does
  not confirm the file exists. A platform admin key is exempt and can read any organization's
  file; do not mistake that for the rule.
- **`galtea.datasets.download(dataset, output_directory)`** fetches a dataset's own uploaded CSV
  and saves it under the random storage id, not the name the user uploaded. On a newer SDK it
  shares the code path above and raises on failure; on an older one it prints and returns `None`,
  so check the return value when you cannot rely on the version.
- **Many files at once:** `POST /storage/generate-get-presigned-urls` with `{"uris": [...]}`
  returns `{downloadPresignedUrls: {<uri>: <link>}}` and silently omits any uri the caller does
  not own or that does not exist. It has no CLI verb, so it is a raw call. Compare the keys you
  get back against the ones you sent before assuming every file resolved.

## What a file-carrying test case cannot do

- **Single turn only.** A file satisfies the first turn, but a multi-turn simulation is refused.
- **Platform-run inference is refused.** Galtea will not call the user's endpoint with a file
  input, because the request shape a document pipeline expects is not defined. The user runs
  their own pipeline and uploads the result.
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

Then create the test case with the `downloadPresignedUrl` as the part's `uri`.

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
sends JSON bodies, so `field: @/path/to/file` inlines the file's *text* as a string rather than
uploading it. Use the SDK, or the `curl` step above. Do not present any other CLI invocation as
an upload; a command that appears to accept a path will silently send the wrong thing.
