# Algorithms, decisions, and reproducibility

These are transparent local heuristics, not trained quality models or proprietary
curation algorithms. The implementation lives in `src/data_curator/pipeline.py`;
the manifest records engine and algorithm versions. All thresholds below can be
inspected in the default recipe printed by `data-curator recipe`.

## Input order and text preservation

Original records are copied before derivation. Text bytes after UTF-8 decoding are
preserved; code case, indentation, tabs, and line endings are not collapsed.
Imports remove an optional UTF-8 byte-order mark, while the original upload remains
available separately. Derived record IDs combine dataset and source-record identity.

The engine sorts sources by dataset ID and source-record ID before screening. The
first retained duplicate representative wins in that order, including duplicates
across source datasets. Changing record or dataset identities can change which
representative survives. Repeat runs against the same stored inputs are stable.

Text feature extraction uses Unicode NFC normalization. CJK, Japanese, and Hangul
characters become individual lexical units; other letter/number runs, punctuation,
symbols, and underscores remain tokens. Case folding is used for relevance and
held-out overlap, but not near-duplicate features. Feature normalization never
rewrites the record's exported text.

## Clean

Checks run in this order for each source record:

1. Reject empty/whitespace-only content.
2. Enforce configured character length: defaults 24 through 200,000 characters.
3. Reject Unicode control characters other than newline, carriage return, and tab.
4. Screen held-out overlap.
5. Screen exact duplicates.
6. Screen near duplicates for text records.

Each rejection has a stage, reason, record ID, dataset ID, and relevant details.
Originals remain intact. A kept record can still be rejected at a later stage.

### Exact duplicates

The key is SHA-256 of `kind + NUL + exact UTF-8 text`. This keeps text and code
separate and does not fold case, collapse whitespace, or normalize Unicode. Exact
matching is global across selected source datasets. Code uses **only exact
deduplication**: small lexical edits or indentation changes can change behavior,
so this engine does not label semantically different code as a near duplicate.

### Near duplicates

Text records use sets of three-token shingles. For a verified pair with shingle
sets `A` and `B`, similarity is `|A ∩ B| / |A ∪ B|`. A pair is rejected only when
this actual Jaccard value reaches `clean.dedup_threshold` (default 0.9). Short
records use the smaller available shingle width.

While at most 512 retained text records are indexed, candidate comparison is
exhaustive. Larger indexes use an original deterministic MinHash/LSH heuristic:

- SHA-256 hashes of canonical token tuples, reduced modulo `2^61 - 1`.
- 32 affine permutations with SHA-256-derived coefficients, scheme version 1.
- Eight LSH bands of four signature values.
- At most the lowest 8,192 shingle hashes are used to build each signature.
- At most 256 candidate records are verified, in stable index order.

A shingle-count ratio rejects impossible matches before computing intersections.
Every candidate that causes a rejection has its Jaccard similarity checked; a band
collision alone is never a reason to discard a record. Decisions include the
retained duplicate ID, similarity, and threshold.

Candidate generation and caps can miss duplicates in larger collections. The
method is lexical, not semantic; paraphrases and translations are not reliably
detected. There is no persisted MinHash cache or `datasketch` dependency. The
manifest records the complete signature scheme and caps for future compatibility.

### Held-out overlap

Only datasets with role `holdout` populate the exclusion index. Case-folded Unicode
token n-gram sets use `clean.holdout_ngram` (default 8); a shorter reference uses
its available token count. For each matching reference, overlap is:

```text
shared unique n-grams / min(source unique n-grams, reference unique n-grams)
```

Reject when overlap reaches `clean.holdout_overlap` (default 0.2). Using the smaller
denominator catches a held-out passage embedded in a larger document. Details
record the reference ID, matched count, window, fraction, and threshold, without
copying held-out text into the decision log.

An empty reference contributes no n-grams. A source shorter than a reference's
window cannot match that window. Common phrases can create false positives;
paraphrases, translations, and shorter fragments can escape the screen. Review
the thresholds and keep evaluation design separate from this lexical filter.

## Calibrate

Quality and relevance are independent scores in `[0, 1]`. Their thresholds default
to 0.35 and 0.0. Per-record decisions retain both scores and the quality components.

### Unicode-aware quality

Let `C` be non-whitespace characters and `T` be feature tokens. Components are:

| Component | Calculation |
| --- | --- |
| Meaningful `M` | Fraction of `C` in Unicode letter, number, or symbol categories |
| Printable `P` | Fraction of all characters printable or newline/carriage return/tab |
| Length `L` | `min(1, log2(1 + len(C)) / 8)` |
| Diversity `D` | `min(1, unique(C) / min(24, len(C)))` |
| Repetition `R` | For at least eight tokens, `min(1, (1 - most_common_token_fraction) / 0.6)`; otherwise 1 |

If at least 12 non-whitespace characters contain only one or two distinct values,
`R` is capped at 0.15. Empty content scores zero. The final score, rounded to six
decimals, is the clamped base score multiplied by `0.25 + 0.75R`:

```text
text base = 0.35M + 0.20P + 0.25L + 0.20D
code base = 0.20M + 0.30P + 0.25L + 0.25D
```

This avoids using ASCII-letter counts or whitespace word counts as universal
quality criteria. CJK text, math symbols, and code punctuation remain meaningful
inputs. The score still reflects subjective heuristics: it does not certify
truthfulness, originality, educational value, language coverage, or safe content.

### Task relevance

The relevance corpus consists of cleaned sources, calibration targets, and the
optional task description. Held-out records never enter this corpus. For token
document frequency `df` among `N` documents, smooth IDF is
`log((1 + N) / (1 + df)) + 1`; term weight is `(1 + log(count)) × IDF`.

The reference vector combines token counts from the task and target records.
Reference and candidate vectors are L2-normalized; their cosine similarity is the
relevance score, rounded to six decimals. This is lexical TF-IDF similarity, not
an embedding model. It can miss synonyms and overvalue shared vocabulary.

Without a task or target, relevance is inactive, scores are 1, and decisions state
that it is unconfigured. Target examples are not added to the training output. The
same fitted relevance model scores later generated records.

## Create

Create is disabled by default and records an explicit skipped stage. Enabling it
requires a named installed model. Eligible source records have already passed
cleaning, decontamination, quality, and relevance checks. No target or held-out
text is inserted into model prompts.

The adapter sends a source-grounded rephrasing instruction and the source text to
an explicitly configured loopback chat-completions server. It does not execute
model tools. Each candidate retains its source dataset/record IDs, parent IDs,
model name, endpoint, prompt SHA-256, source/output hashes, generation parameters,
and attempt count. Truncated, invalid, empty, tool-call, or refused responses fail
explicitly; transient connection/server failures have bounded retries.

Generated candidates pass the same length, control-character, held-out, duplicate,
quality, and relevance checks before composition. All candidate outputs are stored
in `generated.jsonl`, including rejected and unselected candidates, so a later
review does not depend on regenerating them. Retry can call the model again and
produce different text. A seed and temperature zero do not guarantee identical
generation; reuse preserved candidate outputs as data when replaying an experiment.

## Compose

The current budget is explicitly **records**, via `compose.max_records`. There is
no token-budget sampler. Token totals in summaries are the estimate
`ceil(UTF-8 bytes / 4)` per record, not a model tokenizer result.

With empty weights, each selected source has equal weight. With explicit weights,
omitted sources receive zero. Keys must identify selected source datasets; values
must be finite and nonnegative, with at least one positive value. Negative,
all-zero, non-finite, and unknown-source weight configurations are rejected.

Largest-remainder apportionment produces nominal source quotas. Quotas are capped
by available records, and unmet allocation is redistributed among positive-weight
sources with remaining capacity. Stable source-ID ordering breaks quota ties.
Candidates within a source are ordered by SHA-256 of `[seed, scope, record_id]`;
selected records get a separate seeded export order. No record ID is sampled more
than once. Eligible synthetic records remain in their parent source's bucket.

If the requested budget is impossible, the export contains all available records
with positive weights and reports the shortage; it does not silently sample with
replacement. A source can have a shortage even when redistribution fills the total
budget. Requested weights/fractions/records, available counts, realized
counts/fractions, and source shortages are recorded. Unselected records have
`zero_source_weight` or `outside_record_budget` decisions.

## Artifact reconciliation

Canonical JSON uses UTF-8, sorted keys, compact separators, and rejects non-finite
numbers. Every JSONL record has a final newline; an empty export is zero bytes.
SHA-256 checksums cover these exact bytes.

The manifest includes schema/engine versions, seed, recipe hash, per-input roles
and hashes, algorithm descriptions, stage counts, output/rejected/decision/generated
hashes, mixture, shortages, skipped stages, token estimation method, and limits.
It omits operational timestamps and run IDs. Fixed input identities, contents,
configuration, and engine version produce byte-identical deterministic artifacts.

`input_records` counts source records only. `synthetic_candidates` counts all model
outputs, `synthetic_records` counts those that pass screens, and
`synthetic_output_records` counts generated records selected for export. Rejected
records include cleaning/calibration failures, rejected generated candidates, and
eligible records outside the composition budget. Stage keep events are not unique
record counts; use the summary and exported IDs to reconcile totals.

These measurements describe the curation process. They do not measure model
training accuracy, generalization, compute efficiency, or production throughput.
