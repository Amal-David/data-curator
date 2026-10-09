# Verification evidence

## Independent Linux VM verification, 2026-10-09

The existing branch at `ac80a63eaf6636f66304685f6cc3da2e0620f401` was
cloned into a separate Linux x86_64 VM. Its original 189 Python tests passed there
before hardening. The engine, persistence, HTTP boundary, and frontend were then
independently reviewed. The VM uses Python 3.12.14 and Node 24.19.0.

| Check | VM result |
| --- | --- |
| `ruff check .` | Passed |
| `mypy` | Passed, all nine application modules |
| `pytest --cov=data_curator` | 253 passed, 90% statement coverage |
| `npm --prefix web run lint` | Passed |
| `npm --prefix web run build` | Passed, strict TypeScript and production build |
| `python scripts/audit_demo.py` | Four planted rejections matched; all six repeated artifact representations matched |
| Isolated wheel install, CLI demo/run/export | Passed; 13 retained records, four stage hashes, five exported files |
| `npm audit --omit=dev` | Zero reported production dependency vulnerabilities at verification time |
| `git diff --check` | Passed |
| Chromium browser workflows in this VM | Blocked before test bodies ran; details below |

The 64 additional regressions cover supplementary-plane CJK leakage before and
after generation, collision-free derived IDs, generated-parent consistency,
malformed model responses, stage-hash reconstruction, artifact tampering and
legacy compatibility, dead-worker recovery, failed thread startup, concurrent
admission and shutdown, export collision safety, and malformed Host/Origin values.

The broader Python suite also covers Unicode/math/code behavior, exact and near
duplicates, role isolation, cross-process hash-seed stability, mixture shortages,
immutable input checks, atomic uploads, traversal and symlink containment, schema
and size limits, and mock-model redirect, timeout, cancellation, and retry paths.
Mock inference uses temporary loopback servers; no model download or paid service
is used. A passing mock response does not evaluate real model quality.

## Browser evidence and environment limitation

Five real Chromium workflows passed during the initial implementation on macOS
arm64 with Python 3.13.11, Node 22.22.3, and Chromium 156 (Playwright 1.64). GitHub
CI for the original `ac80a63` head also completed successfully on Ubuntu.

The independent VM could not execute browser test bodies:

1. Playwright's expected downloaded Chromium was absent.
2. Installed Chromium 154 could not create its Unix singleton socket (`EPERM`),
   including during an approved escalated test execution.
3. The supported cloud browser could not reach the shell's test server
   (`ERR_CONNECTION_REFUSED`).
4. The official Playwright headless-shell CDN returned a small “Site Unavailable”
   HTML page rather than the requested ZIP. The installation route was stopped.

No security settings were changed, no desktop fallback was used during the VM
review, and the temporary executable override was removed. Frontend code and
existing screenshots are unchanged by this review. The VM's unrun browser bodies
are not represented as passes.

The committed CI workflow runs Python 3.11/3.13 and Chromium on Ubuntu. Check
[PR #1](https://github.com/Amal-David/data-curator/pull/1/checks) for the result on
the exact current head, especially after later edits.

The browser suite uses the actual HTTP handler, SQLite store, and pipeline with
throwaway data. It covers desktop and 390px layout, keyboard typing/focus,
idempotent demo loading, saved recipes, explicit generation skip, decisions,
manifest/export SHA reconciliation, repeat/retry/reload, malformed and invalid-UTF-8
uploads, code preservation, inert HTML, invalid configuration, and cancellation
followed by retry on 1,800 synthetic fixture records. The demo workflow checks for
unexpected external requests. It is not an exhaustive accessibility audit.

The following earlier screenshots were captured from the running application and
reinspected as pixels during VM review; no new VM screenshots were captured:

- [Studio overview](screenshots/studio-overview.png)
- [Completed run and rejection decisions](screenshots/studio-decisions.png)
- [Narrow viewport](screenshots/studio-mobile.png)

All four supplied reference images were also inspected successfully in the VM.
They show launch messaging, benchmark claims, and the four-stage workflow. See
[reference notes](REFERENCE_NOTES.md); vendor reference images are not distributed.

## Limits of verification

No real local language model, training run, model benchmark, large-corpus
performance test, production deployment, Windows runtime, Safari, or Firefox was
verified. Composition has a record budget; token totals are estimates. Near-duplicate
matching is bounded and approximate at larger counts. This is an in-memory local
tool, not verified proprietary parity or enterprise-scale infrastructure. The
[capability map](CAPABILITIES.md) describes the remaining gaps.
