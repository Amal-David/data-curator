# Capability map and next validation steps

Data Curator provides a working, local, inspectable implementation of a useful
curation workflow. It is not a claim to reproduce proprietary algorithms or
research outcomes. This map separates working functionality from future work.

## Working today

| Area | Implemented behavior | Important limit |
| --- | --- | --- |
| Ingest | Atomic bounded JSONL/JSON/CSV/text/code imports, immutable originals, declared source licenses | No cloud bucket connector, archive ingestion, or format repair inference |
| Clean | Exact deduplication, deterministic MinHash candidates with Jaccard verification, Unicode-aware checks, held-out lexical overlap exclusion | Near matching is bounded; paraphrases and translations can escape overlap checks |
| Calibrate | Inspectable quality components and TF-IDF task/target relevance, isolated holdouts | No learned quality model, semantic embedding or multilingual quality guarantee |
| Create | Explicitly enabled local OpenAI-compatible generation, source lineage, generated-output re-screening | No bundled model or quality guarantee; actual model inference still needs separate validation |
| Compose | Seeded weighted sampling without replacement, capacity redistribution, shortages | Record budgets only; no tokenizer-backed budget, optimizer, or proxy-model training |
| Inspect and export | Shared CLI/API/UI, original demo, rejection details, reusable recipes, manifests and canonical JSONL | Single-user local workflow; no multiuser authorization or public deployment mode |
| Execute | Persisted jobs with cancellation, retry and interrupted-worker recovery | In-memory processing with bounded inputs; no distributed executor or checkpoint resume |

## How the reference differs

The [official launch article](https://www.datologyai.com/blog/introducing-datology-curation-studio)
describes proprietary, learned curation methods, enterprise-scale orchestration,
VPC deployment, and empirically developed mixtures. The
[multimodal technical post](https://www.datologyai.com/blog/productionized-multimodal-data-curation-at-the-billion-sample-scale)
also discusses learned image-text filtering, embeddings, and caption generation.
Those capabilities are not implemented here. The four-stage workflow alone does
not establish algorithmic, quality, modality, privacy-deployment, or scale parity.

## Useful next steps, before stronger claims

1. Evaluate a user-selected installed local model with authorized, non-sensitive
   source data. Measure groundedness, leakage and failure behavior; do not equate
   a successful HTTP response with good synthetic data.
2. Build a reviewed corpus with labeled duplicates, contamination and retention
   decisions across representative languages and domains. Measure false positives
   and false negatives rather than only fixture success.
3. If token budgets are needed, add an explicit tokenizer interface and versioned
   counting, then test weighted selection against variable record lengths.
4. If scale is needed, benchmark memory and latency first, then design an optional
   streaming/distributed adapter with the same provenance contracts. No DataTrove
   or NeMo Curator integration currently exists.
5. Establish downstream training effects only with matched models, training
   budgets, held-out evaluations, multiple seeds and reported uncertainty. No such
   experiment was performed for this release.

These are future work, not scheduled jobs, installed dependencies or hidden model
downloads. The demo remains offline and requires no inference service.
