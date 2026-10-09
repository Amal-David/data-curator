# Security and privacy model

Data Curator runs on a trusted single-user workstation. It stores datasets and
results locally and sends no telemetry. It does not execute imported code, follow
links in records, download datasets or models, or contact a hosted inference API.
Its optional inference adapter sends selected source text only to a configured
literal loopback HTTP endpoint.

## Local server boundary

The API binds to loopback and rejects non-loopback binds. Host and Origin checks
reduce DNS-rebinding and cross-origin browser access; there is no wildcard CORS.
The built UI is served on the API origin. The development frontend uses the
configured loopback Vite origin and proxy.

This is not an authentication system. Other processes and users with access to
the same workstation may be able to reach the local API or read the workspace.
There is no account separation, encryption at rest, access-control list, public
deployment mode, or tenant isolation. Do not expose it through a tunnel or reverse
proxy as a public service. The data directory should have the same access controls
as the original data.

## Untrusted imports

- Only bounded UTF-8 text formats are parsed. There is no archive extraction,
  deserialization of executable objects, or evaluation of imported Python/JavaScript.
- Upload names are parser hints and labels. Internal resource IDs determine storage
  paths; imported filenames and user IDs never select arbitrary write locations.
- Size, record-count, per-record character, metadata-depth, and metadata-size limits
  constrain ingestion. Invalid input fails before the dataset is published.
- JSON non-finite numbers, invalid schemas, malformed CSV, and invalid Unicode are
  rejected. Code case and indentation remain data, not normalization targets.
- Browser inspectors render content as text. A document containing HTML or script
  tags is inspected as a document, not inserted as active markup.
- Static file and artifact routes restrict traversal and allowed resource names.

Bounds lower resource risk but do not make every large or adversarial document
cheap to score. The application keeps indexes in memory. Treat input limits as
upper bounds, start with small batches, and use cancellation when a workload is
too expensive for the machine.

## Optional inference boundary

Generation is disabled unless explicitly enabled with a model name. The endpoint
must be HTTP on a literal loopback IP with an accepted chat-completions path.
Hostnames, non-loopback addresses, URL credentials, query strings, and fragments
are rejected. The client uses direct connections, ignores environment proxy
configuration, and does not follow redirects. Responses and timeouts are bounded;
transient retry attempts are limited. Errors are recorded as failures rather than
substituting fabricated output.

The local model service is separately administered. The application cannot control
whether that service logs prompts, forwards requests, downloads weights, or has
its own plugins. Configure and audit it before enabling Create. For the web API,
the endpoint is fixed by `serve --model-endpoint`, `DATA_CURATOR_MODEL_ENDPOINT`,
or the loopback default; a recipe must match this server setting. The trusted local
CLI can choose another valid loopback endpoint in its recipe. There is no multiuser
permission boundary in this version.

Source text is untrusted, including instructions embedded in a document. Prompts
tell the model to treat it as data, and no model tool calls are executed. This does
not guarantee prompt-injection resistance, factual correctness, or semantic
equivalence of rewritten code. Generated outputs require review and are screened
again for overlap, duplicates, and configured quality thresholds.

## Dataset-role separation

Source records are screened against held-out references before generation. Only
retained source records reach the model. Held-out contents are not supplied to
calibration, task prompts, generation prompts, or training exports. Generated
content is screened against the holdout again before composition.

Lexical n-gram exclusion is not a semantic leakage detector. Paraphrases,
translations, short fragments below the configured window, and independently
reconstructed answers can escape it. Keep evaluation data under separate access
controls and assess leakage beyond these checks for high-stakes experiments.

## Persistence and sharing

Originals, dataset metadata, input snapshots, decisions, rejected records, and
generated text may contain sensitive material. Deleting or sharing only a curated
export does not remove or share the entire workspace. The repository ignores local
workspace data; verify your own paths before committing files or publishing
artifacts. Hashes provide integrity checks, not encryption or anonymization.

Declared dataset licenses are provenance fields supplied by the importer. They do
not grant rights, prove attribution completeness, or change third-party model and
dataset terms. No automatic license inference is performed.

To report a security issue, contact the repository maintainers privately through
GitHub's security reporting interface if enabled. Avoid including private data in
public issues; provide a minimal synthetic reproduction instead.
