# Verification evidence

Verified locally on macOS arm64 with Python 3.13.11, Node 22.22.3, and Chromium
156 (Playwright 1.64). CI runs Python 3.11 and 3.13 plus Chromium on Ubuntu.
See the pull request's exact-head checks for their current result.

| Check | Local result |
| --- | --- |
| `ruff check .` | Passed |
| `mypy` | Passed, all nine application modules |
| `pytest --cov=data_curator` | 189 passed, 89% statement coverage |
| `npm --prefix web run lint` | Passed |
| `npm --prefix web run build` | Passed, strict TypeScript and production build |
| `npm --prefix web run test:e2e` | Five Chromium workflows passed |
| `python scripts/audit_demo.py` | Four planted rejections matched; repeat bytes and hashes matched |

The browser suite uses the actual HTTP handler, SQLite store, and pipeline with
throwaway data. It checks desktop and 390px layout, sequential keyboard typing and
focus, idempotent demo loading, saved recipes, explicit generation skip, decisions,
manifest/export SHA reconciliation, repeat/retry/reload, malformed and invalid-UTF-8
uploads, code preservation, literal rendering of HTML, invalid configuration, and
cancellation followed by retry on 1,800 generated fixture records. Requests are
checked for unexpected external destinations in the demo workflow.

The Python tests include Unicode/math/code behavior, exact and near duplicates,
held-out exclusion before and after generation, role isolation, cross-process hash
seed stability, mixture shortages, immutable input checks, interrupted workers,
atomic uploads, traversal and symlink containment, Host/Origin rejection, schema
and size limits, large integer/non-finite validation, and mock-model failure,
redirect, timeout, cancellation, and retry paths. Mock inference runs on temporary
loopback servers; no model download or paid service is used.

Screenshots captured from the actual running application and visually inspected:

- [Studio overview](screenshots/studio-overview.png)
- [Completed run and rejection decisions](screenshots/studio-decisions.png)
- [Narrow viewport](screenshots/studio-mobile.png)

## Limits of verification

No real local language model was configured. Its adapter was tested against a
controlled OpenAI-compatible loopback server, not actual model quality or hardware.
No training, model benchmark, large-corpus performance, production deployment,
Windows runtime, Safari, or Firefox verification was performed. Composition has
a record budget; token totals are estimates, not a tokenizer or token-budget
sampler. Near-duplicate matching is deliberately bounded and approximate at larger
input counts; the application is an in-memory local tool.

All four supplied Library image references were blocked by HTTP 403 after the
supported retry, so their pixels were not inspected. See [reference notes](REFERENCE_NOTES.md).
The shipped screenshots show this original implementation, not those references.
