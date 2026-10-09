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

The launch article describes a guided four-stage workflow, role-separated data,
task calibration, source-grounded generation, and configurable composition. It
also describes enterprise infrastructure and proprietary research that this
local implementation does not reproduce. See the [capability map](CAPABILITIES.md)
for the implemented boundary and explicit gaps.

Two further primary sources informed the boundaries of this project:

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

Initial inspection on a separate machine was blocked by HTTP 403. During the
independent Linux VM review on 2026-10-09, all four already-available image files
were opened and their pixels inspected. They show launch messaging, benchmark
claims, small summary illustrations, and the four-stage workflow, rather than a
complete interface specification. The official launch article was also retrieved
and reviewed during that pass.

The interface remains an original design. The supplied images establish the
requested workflow, not implementation details or evidence that this project
achieves any vendor result. Reference image files are not committed to this
repository.
