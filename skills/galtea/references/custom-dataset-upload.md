# Upload a dataset the user already has

Use this when the user has test content and wants it on the platform, instead of asking Galtea
to generate it. Two paths, and they compose:

| The user has | Path | Surface |
|---|---|---|
| A CSV of rows | Upload the CSV as the dataset file | SDK, dashboard |
| Files (a document, an audio clip) to attach to rows | Presign, PUT the bytes, reference the URI in the row | SDK, or CLI plus `curl` |
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
- **A dataset created from a file skips generation entirely.** No credit check, no
  `max_test_cases` ceiling, and the dataset is created already `SUCCESS` with every row
  persisted before the call returns.

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

**Dataset type names differ per surface.** The SDK takes `ACCURACY` / `SECURITY` / `BEHAVIOR`;
the CLI and raw API take `QUALITY` / `RED_TEAMING` / `SCENARIOS`. Same three things.

### Columns

Required, and this is the whole list:

| Dataset type | Required columns |
|---|---|
| `ACCURACY` (`QUALITY`) | `input` |
| `SECURITY` (`RED_TEAMING`) | `input` |
| `BEHAVIOR` (`SCENARIOS`) | `user_persona`, `scenario`, `goal` -- all three |

Optional columns, accepted for **every** type with no per-type gating:

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
**On any failure the dataset row itself is deleted**, so the user is not left with a
half-populated dataset. Fix the CSV and call again.

Two things to warn a user about before they read the error:

- **The row number counts parsed non-empty rows, not file lines.** Blank lines are skipped before
  numbering, so `Row 12` may not be line 12 of their file. It also excludes the header.
- **A CSV mistake comes back as HTTP 500, not 400.** The row-level message is raised as a plain
  error, so it lands in the server-error branch. Read the `message` field, do not judge by the
  status code. `Row 3 is missing required fields: input` is a user error wearing a 500.

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
`mime_type`.

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

Within one `datasets.create` call the CSV path **does** deduplicate by absolute path, so the same
document referenced by twenty rows uploads once. Every row is validated before the first upload,
so a bad path costs no transfer. Row errors are prefixed with the row number.

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

## What a file-carrying test case cannot do

- **Single turn only.** A file satisfies the first turn, but a multi-turn simulation is refused.
- **Platform-run inference is refused.** Galtea will not call the user's endpoint with a file
  input, because the request shape a document pipeline expects is not defined. The user runs
  their own pipeline and uploads the result.
- **Editing must keep the structure.** Sending `input` as a plain string on a test case that has
  files is refused, because a bare string would drop every attachment. Send the object with the
  `content` array holding the files to keep. Remember that editing a test case's content forks a
  new revision and retires the old row, so the returned id is new.
- Creating a test case with files requires an authenticated caller, which an API key satisfies.

## Sending bytes from the terminal

The two-step upload, for when the SDK is not an option:

```bash
# 1. Ask for an upload URL. `key` is the FILE NAME, not a storage path.
galtea storage generate-put-url --key "rental-contract.pdf" --file-type file

# 2. PUT the bytes to the uploadPresignedUrl from that response.
curl -X PUT --upload-file ./rental-contract.pdf \
  -H "Content-Type: application/octet-stream" \
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

On Azure storage a presigned PUT also needs `-H "x-ms-blob-type: BlockBlob"`. S3 ignores it, so
sending it is safe either way.

**The CLI cannot upload a file itself.** Its command tree is generated from the API spec and only
sends JSON bodies, so `field: @/path/to/file` inlines the file's *text* as a string rather than
uploading it. Use the SDK, or the `curl` step above. Do not present any other CLI invocation as
an upload; a command that appears to accept a path will silently send the wrong thing.
