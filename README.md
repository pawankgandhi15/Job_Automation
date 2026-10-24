# Job Automation Agent

A local-first job discovery and application review system built with Python, SQLite and React. It collects opportunities from configured public career sites, evaluates them against a candidate profile, creates sourced review reports, and provides a local web dashboard for reviewing evidence and managing approvals.

> The agent does not submit applications or send email. It prepares explicit, expiring manual handoffs; a person performs the action and records the result.

## Features

- Daily opportunity discovery from configured public ATS feeds, with source health and durable run history.
- Sixteen evidence-based eligibility checks with `PASS`, `FAIL` and `UNKNOWN` states.
- Searchable SQLite ledger with FTS5, verified schema migrations and backups.
- Company and role summaries with official job details and application links.
- Local React review workstation for evidence, reports, approvals, handoffs and outcome reconciliation.
- Approval scopes bound to current job details, profile, resume and action package.
- Audit history, in-app reminders, and optional explicit desktop or webhook notifications.
- Isolated synthetic demo and offline tests.

## Requirements

- Python 3.11 or newer.
- Node.js and npm for the React site.
- SQLite with FTS5 and JSON support (included with supported Python builds).

The Python runtime uses only the standard library. Install development dependencies only when running checks or building distributions.

## First-time setup

Run these commands from the repository root in PowerShell:

```powershell
Copy-Item config/profile.example.json config/profile.json
Copy-Item config/settings.example.json config/settings.json
```

Edit `config/profile.json` with the candidate's own confirmed information. Add a resume path in `config/settings.json` and place that file locally. Review the configured sources before discovery. The real profile, settings, resume, ledger, reports and handoffs are excluded from Git.

Check the configuration and database:

```powershell
python -m job_agent preflight
python -m job_agent db-status
```

Run an isolated example without changing the live ledger:

```powershell
python -m job_agent demo
```

## Daily workflow

Start discovery and inspect the resulting reports and review queue:

```powershell
python -m job_agent discover
python -m job_agent review-queue --limit 20
python -m job_agent source-status
python -m job_agent runs --limit 5
```

Discovery writes a Markdown report and companion review, queue and run JSON files under `reports/`. Results include unresolved questions and rejected opportunities. An unsuccessful source is reported; if every configured source fails, the command exits with status 3.

Search the local ledger without making a network request:

```powershell
python -m job_agent search "Python backend" --state NEEDS_CLARIFICATION --limit 20
```

To add a source, give the agent the employer's official site and careers page. It only accepts supported public boards linked from that employer:

```powershell
python -m job_agent source-add --company "Example Employer" --website "https://example.com" --careers-url "https://example.com/careers" --tier GOOD
```

## Local website and review API

Start the local Python service from the repository root:

```powershell
python -m job_agent serve --port 8765
```

Open the review workstation at [http://127.0.0.1:8765/review](http://127.0.0.1:8765/review). The company briefs page is at [http://127.0.0.1:8765/](http://127.0.0.1:8765/).

The React source is in `web/company-overview/`. For frontend development, run Vite in another terminal:

```powershell
Set-Location web/company-overview
npm.cmd ci
npm.cmd run dev
```

Vite serves the frontend at `http://127.0.0.1:5173` and proxies API calls to the local Python service. Company snapshots contain company and opportunity details only; they do not contain the candidate profile, application drafts or ledger history. A hosted static page uses a snapshot and does not host the local review API.

## Approval and privacy

Every external action requires explicit approval for its specific channel and current action package. Handoffs expire. The agent does not automate browser logins, bypass CAPTCHAs, apply for jobs, or send messages. Record only outcomes that were actually observed; uncertain submissions block retries until reconciled.

Keep `config/profile.json`, `config/settings.json`, resume files, `data/`, `reports/` and `handoffs/` private. Do not commit access tokens or other credentials. Public configuration templates are provided in `config/*.example.json`.

## Development checks

Create an isolated environment and install the pinned development tools:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ruff check job_agent tests
.venv\Scripts\python.exe -m ruff format --check job_agent tests
.venv\Scripts\python.exe -m mypy job_agent
.venv\Scripts\python.exe -m pytest --cov=job_agent --cov-fail-under=85
.venv\Scripts\python.exe -m build --no-isolation
```

For the React site:

```powershell
Set-Location web/company-overview
npm.cmd test
npm.cmd run build
npm.cmd run test:browser
```

GitHub Actions checks Python 3.11–3.13 on Windows and Linux, runs linting, formatting, type checks and offline tests, and builds the Python package. Local checks do not claim that the remote CI matrix has run.

## Architecture and roadmap

See [Phases 2 and 3](PHASES_2_3.md) for the service layout, ATS adapters, resilient requests, local API, dashboard, notifications, verification results and known limits. [Phase 1](PHASE1.md) records the storage, configuration and CI foundations. The full technical plan is in [the roadmap](pawan_job_agent_system_improvement_plan_roadmap.md).
