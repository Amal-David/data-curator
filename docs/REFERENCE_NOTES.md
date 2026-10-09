# Public references and independent implementation

Data Curator is an original, local text and code curation application. It is not
affiliated with, endorsed by, or an implementation of DatologyAI's proprietary
systems. Its code, interface, fixtures, and heuristic algorithms were written
independently. No vendor logo, product screenshot, dataset, or research result is
distributed as part of this application.

## Scope of the inspiration

The product brief identified four useful workflow stages: **Clean**, **Calibrate**,
**Create**, and **Compose**, with separate source, calibration target, and held-out
dataset roles. The [official Curation Studio launch page](https://www.datologyai.com/blog/introducing-datology-curation-studio)
is the product reference supplied for that brief. These stage names describe the
workflow; they do not imply access to the vendor's underlying implementation.

Two accessible primary sources informed the boundaries of this project:

- [BeyondWeb: Lessons from Scaling Synthetic Data for Trillion-scale Pretraining](https://www.datologyai.com/blog/beyondweb)
  discusses source-grounded synthetic rephrasing and reports experiments conducted
  by DatologyAI. It motivates making generation an explicit, traceable step. Data
  Curator uses a small optional local-model adapter; it does not reproduce that
  research pipeline or its experiments.
- [Technical Deep-Dive: Image-Text Data Curation at the Billion-Sample Scale](https://www.datologyai.com/blog/productionized-multimodal-data-curation-at-the-billion-sample-scale)
  describes algorithm families including deduplication, filtering, target
  distribution matching, and synthetic captions. Its main results concern
  multimodal image-text systems. It is not a disclosure of the exact internals of
  a text curation product, and this project does not implement its learned models
  or distributed infrastructure.

## Claims this project does not make

Vendor compute-efficiency multipliers, benchmark charts, and training-quality
claims are reference material, not Data Curator results. This repository does not
claim sixfold efficiency, proprietary parity, improved downstream model accuracy,
or billion-record scale. Removing duplicates or raising a heuristic quality
average does not establish a training improvement.

The included fixtures and tests check pipeline behavior, isolation, reproducibility,
and local application flows. Establishing a model-quality benefit requires a
separate experiment with matched models, training budgets, evaluation controls,
multiple seeds, and reported uncertainty.

## Visual reference inspection record

Four user-supplied Library image references were requested for local inspection:

| File | Intended reference |
| --- | --- |
| `IMG_5185.png` | Four-stage workflow |
| `IMG_5184.png` | Benchmark claims |
| `IMG_5183.png` | Dataset comparison |
| `IMG_5182.png` | Launch |

On 2026-10-09, the current Library materialization helper failed for each file
with HTTP 403 after one bounded supported retry. No image bytes were available
locally, and no pixel inspection was possible. The launch article also could not
be opened by the research browser on that date. Neither limitation was bypassed.
The visual design is therefore original and based on the functional brief, rather
than a claimed reproduction of those screenshots. Reference image files are not
committed to this repository.
