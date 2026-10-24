# Phases 2 and 3 implementation record

Release: 1.0.0. Phase 1 foundations and the existing candidate profile, daily-company history, reports and approval controls are retained.

## Phase 2: services and discovery reliability

- The Agent facade is 108 lines. Discovery, evaluation, packaging, approval, execution and reporting use separate services, with a declarative CLI command registry.
- Domain states, approval scopes and attempt tokens have explicit models. Approval/execution transitions have service validation and database guards. Existing state names remain compatible with the ledger.
- Domain events publish after successful transactions. The existing event history has a SHA-256 chain and metadata-only correlated JSONL logging. This provides local tamper detection, without an external trust anchor.
- Public GET requests use bounded retries, per-host spacing, Retry-After handling, ETag/304 caching and circuit breakers. Cache persists between runs; host circuit state is scoped to the running agent.
- Greenhouse, Lever and Ashby remain supported; SmartRecruiters and Workable public adapters were added. Employer-to-board provenance is still required. Workday remains unsupported until a reviewed public endpoint contract is available.
- Section-aware experience parsing and dependency-ordered policy rules retain the existing 16 eligibility gates and 0–10 score. Unknown requirements remain unresolved. Unverified degree equivalencies and currency conversions are not introduced.
- Exact duplicate posting routes can share a ledger identity across providers. SimHash flags likely cross-posts for review, without automatically discarding distinct vacancies.
- Validated role-family templates and separately attested resume variants bind their exact attachment hashes into action packages. No resume variants were fabricated.
- Handoff expiry is capped at 24 hours and by approval expiry. Expiry never restores consumed permission automatically. Actual late outcomes can still be reconciled.
- `--dry-run` returns a non-executing command plan before opening the ledger; it does not simulate discoveries or claim successful actions. JSON remains the CLI output format.

## Phase 3: local review workstation and observability

Start from the project directory:

```powershell
python -m job_agent serve --port 8765
```

Open http://127.0.0.1:8765/review for the interactive queue, or http://127.0.0.1:8765/ for company briefs. The React development server can proxy the same API from localhost:5173.

- Queue search, state/tier/family filters and pagination; gate inspection and exact-excerpt highlighting; full descriptions and official application links.
- Reviewed eligibility evidence, candidate-confirmed screening answers and explicit declarations. Edits invalidate stale action scopes.
- Report generation and explicit report review before initial approval; separate approval channels; skip reasons; current verification; manual handoff and actual-outcome reconciliation.
- SSE activity refresh and keyboard navigation: j/k, a, s, h, c and slash. Shortcuts do not intercept typing in form controls.
- API mutations require a current scope, same-origin requests, a local session cookie and CSRF token. The server binds only to 127.0.0.1. Protected GET routes use read-only SQLite connections.
- Persistent in-app alerts cover strong matches, due follow-ups, expiring handoffs and suspicious zero-posting runs. Daily runs scan alerts; the local server scans once per minute while running. Desktop or configured webhook delivery requires an explicit `notify` command. No SMTP delivery or notification destination was enabled in this upgrade.
- The hosted owner-private Site serves company snapshots and the React shell. Private ledger actions run through localhost; hosting the shell does not host the Python backend or synchronize private application data.

Applications, outreach and follow-ups remain manual handoffs requiring exact per-channel approval. No employer submissions, recruiter messages or external notifications were executed during this implementation.

## Preservation and verification

Before migration 003, a consistent SQLite backup was saved under the path in `data/phase23-baseline-path.txt`. `data/phase23-migration-verification.json` verifies identical pre-existing table contents immediately after migration: 42 opportunities, two daily selections, seven reported companies, 22 reports, 176 events, 13 runs, 56 source checks, 23 settings, zero approvals and zero attempts. Integrity and foreign-key checks passed. Later evaluation refreshes may legitimately update derived packages.

Local validation: 248 Python tests passed with 87.77% coverage; Ruff lint/format and strict mypy passed, including Linux platform definitions. All 11 React snapshot tests and the Vite production build passed. Wheel and source distribution built successfully. Windows/Python 3.12 was executed locally; the configured remote Python/OS CI matrix has not been run here.

The 50,000-record synthetic warm-search benchmark measured p95 0.67 ms for a broad query and 1.12 ms for a selective query on this computer. The authenticated HTTP queue endpoint also passed the 50ms target: p95 34.98 ms for broad search and 34.39 ms with search/state/tier/family filters. See `data/benchmarks/latest.json` and `data/benchmarks/review-api.json`. These local warm-query measurements are not portable performance guarantees.

The roadmap's illustrative automatic transition from expired handoff back to approved was deliberately replaced with conservative reconciliation. Its proposed 0–100 scoring scale was not substituted for the established 0–10 scale without calibration evidence. Workday, unverified FX conversion and SMTP remain documented extension points rather than claimed capabilities.

Browser verification passed the complete report-review, approval, handoff and uncertain-result workflow on an isolated synthetic ledger, plus a 390px mobile layout. Existing company-page filters, sorting, saved-company persistence, application links and import recovery also passed browser checks. A separately installed wheel verified version 1.0.0, all three migrations, four templates and a fresh database integrity check.

Final real-ledger verification retained 42 opportunities, two daily selections, seven reported companies, zero approvals and zero attempts. The designated resume hash remains valid and reconciled. The existing Windows task is enabled with last result 0 and next run 24 October 2026 at 09:00 IST. Evidence: `data/phase23-final-verification.json`, `data/phase23-schedule-verification.json` and `data/phase23-browser-verification.json`.
