# Phase 1 implementation record

Implemented v0.3.0 foundations against the existing application, preserving its candidate facts, approval rules and company history.

- Candidate-backed education, experience summaries, screening answers and report identity replace embedded personal claims. Education ordering and empty experience arrays are supported. CTC remains unknown unless explicitly supplied as a required answer.
- Runtime validation rejects malformed profiles/settings before ledger access. January 2027 availability, the six-hour limit, unknown joining day and unknown working days remain as recorded in the original profile.
- Atomic, checksum-verified migrations adopt the original schema and add indexes, FTS5 and transactional search-index triggers. Existing tables are retained. SQLite online backups include WAL data; schema upgrades fail without cleaning or dropping inconsistent legacy records.
- Strict mypy checks apply to every Python module. JSON/HTTP boundaries remain explicitly dynamic; typed domain models belong to Phase 2. Ruff checks and formatting run with the offline test suite.
- CI configuration covers Python 3.11, 3.12 and 3.13 on Windows and Ubuntu, with an 85% coverage floor. Local execution uses Windows/Python 3.12; the remote matrix needs a GitHub repository push.
- The daily scheduler keeps its existing task name through `config/settings.json`. Phase 1 does not reinstall it or change its trigger.

The migration audit verified unchanged row counts and SHA-256 digests for all ten pre-existing tables: 42 opportunities, 2 daily selections, 7 reported companies, 22 reports, 176 events, 13 discovery runs, 56 source checks, 23 settings, and zero approvals or attempts. `PRAGMA integrity_check` returned `ok`; foreign-key checks found no errors. Synthetic migration tests additionally preserve active/consumed approval data and uncertain submissions.

Evidence: `data/phase1-ledger-verification.json`, its recorded backup, and `data/benchmarks/latest.json`. Search uses stable ledger-ID ordering so broad FTS matches can stop at the requested limit. Filters and representative warm searches were benchmarked at 50,000 records; inspect the measurement file for actual timings.

Final local verification: 220 tests passed with 89.09% statement coverage. Ruff lint/format and strict mypy passed; type checks also passed using Linux platform definitions. The v0.3.0 wheel and source distribution built successfully, and a separately installed wheel created a fresh database using its packaged migrations. Broad and selective warm search p95 measurements were 0.41 ms and 0.52 ms on this machine. The existing Windows task was verified enabled, with last result 0 and next trigger at 09:00 IST on 24 October 2026. These checks do not constitute execution of the remote CI matrix.

Phase 2 service decomposition, additional ATS providers, retry policies, scoring redesign, resume variants, and the interactive approval API remain separate work.
