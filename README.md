# Pawan's job discovery and application workflow

Phase 1 is implemented in **v0.3.0**. Candidate education, experience and report text come from `config/profile.json`. Configuration is validated before opening the ledger. Optional education `level` and `branch_aliases` fields support other profiles; aliases never establish employer-approved degree equivalence or turn missing evidence into PASS. Resume reconciliation and action-scope checks still apply when facts or rendered answers change.

The existing database is upgraded automatically using packaged, checksum-verified SQL migrations. Before a legacy ledger is changed, SQLite's online backup API creates a consistent copy under `data/backups/`, including committed WAL pages. Pending migrations run in one explicit transaction; an error rolls back the group. Applied migration files must never be edited. WAL, foreign keys, a five-second busy timeout and FULL synchronization are enabled. The upgrade adds indexes and an FTS5 search table with insert/update/delete triggers, retaining original opportunity IDs, approvals, attempts, reports, events and daily-company history. SQLite must provide JSON functions and FTS5; unsupported builds fail explicitly.

```powershell
python -m job_agent db-status
python -m job_agent search "Python backend" --state NEEDS_CLARIFICATION --limit 20
```

Search is offline, treats input as literal terms, and returns matching records in ledger-ID order. It does not approve, submit or score a role. The existing scoring scale remains 0–10. The broad-experience exclusion threshold remains two years by default and is configurable as `automatic_experience_exclusion_min_years`; candidate duration bounds are calculated from recorded dates, without counting overlapping months twice or treating internships as full-time employment.

Development checks use an isolated virtual environment; runtime commands still need only Python's standard library:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ruff check job_agent tests
.venv\Scripts\python.exe -m ruff format --check job_agent tests
.venv\Scripts\python.exe -m mypy job_agent
.venv\Scripts\python.exe -m pytest --cov=job_agent --cov-fail-under=85
.venv\Scripts\python.exe -m build --no-isolation
.venv\Scripts\python.exe -m scripts.benchmark_search
```

The GitHub Actions workflow covers Windows/Linux and Python 3.11–3.13, strict typing, lint/format checks, offline tests, an 85% coverage gate, and package builds. It will run when this project is pushed to a GitHub repository; a remote CI run is not claimed by local checks. Offline fixtures include Greenhouse, Lever and Ashby. The benchmark uses 50,000 synthetic records in a temporary database and records warm-query median/p95 timings in `data/benchmarks/latest.json`; these are local measurements, not a hardware-independent guarantee. The Phase 1 live-ledger preservation audit is in `data/phase1-ledger-verification.json`.

An analyst-assisted Python implementation of `Advanced_Job_Search_Agent_Prompt.txt`. It uses the supplied Pawan Gandhi profile, a SQLite ledger, conservative eligibility checks, transparent scoring, local review reports and explicit approvals. The original prompt is preserved.

Version 0.3 retains your latest confirmation: **both internships and jobs from January 2027, at most six hours per day, with location confirmed for each role**. Expected M.Tech graduation remains June 2027. The January joining day, working days per week and internship duration remain unconfirmed. These newer facts in `config/profile.json` override the older availability defaults in the original prompt. The software does not enter January 1, assume a five-day week, or assume that a full-time employer accepts six-hour days.

Python 3.11+ is required. The runtime uses only the standard library; no API key or package installation is needed for the commands below. Run from `E:\Projects\Job_automation`.

```powershell
python -m job_agent preflight
python -m job_agent demo
python -m job_agent status
```

`demo` uses clearly labelled synthetic vacancies and its own isolated database under `data/demo/`. It does not put examples in the live ledger, perform a real job search, send messages or apply anywhere. The command prints the path to a complete demonstration report.

For the live workflow:

```powershell
python -m job_agent availability
python -m job_agent discover
python -m job_agent review-queue --limit 20
python -m job_agent source-status
python -m job_agent runs --limit 5
```

`discover` fetches the configured public feeds, updates the persistent ledger and writes a Markdown report plus `.review.json`, `.queue.json` and `.run.json` companions. The queue identifies unresolved conditions and who can resolve them. Its HIGH/MEDIUM/LOW priority is a review aid, not a match score or permission to apply. Identity or live-vacancy verification failures take precedence over skill mentions. Source checks and discovery runs persist across restarts; interrupted/failed/partial runs remain visible.

The last discovery's counts distinguish new, changed and unchanged leads, locations outside the search scope, and leads deferred by the run budget. Ledger counts are cumulative. Reports suppress unchanged watchlist rows only after their preceding report is marked delivered; the full pending queue and rejection details remain available. A report can be successfully written even when sources fail. If **every** configured source fails, `discover` exits with code **3** so a terminal or external scheduler can detect the failure. Invalid commands/workflow errors use code 2.

## What is implemented

| Workflow requirement | Behavior |
| --- | --- |
| Candidate and preferences | `config/profile.json` and `config/settings.json`, transcribed from the supplied prompt; both degrees and internship labels preserved |
| Preflight | IST timestamp, resume availability/hash/reconciliation, history completeness, configured sources, actual capability limitations |
| Discovery | Greenhouse, Lever, Ashby and employer-published JobPosting metadata; exact employer-to-ATS link/iframe proof, including embedded Greenhouse forms |
| Wider research | IBM-first search-query links and reviewed JSON evidence imports; global web research and job-board login flows are analyst steps |
| Eligibility | Sixteen PASS/FAIL/UNKNOWN gates; unknown mandatory conditions are watchlisted; explicit incompatible experience, branch, start date, fee and deadline rules override asserted passes |
| Match score | Five components, weighted technical groups, duplicate-synonym removal and explicit evidence; missing denominators do not produce invented scores |
| Salary | Separate monthly internship and annual full-time floors; no undisclosed-pay point; explicitly accepted GREAT-tier exceptions tied to the exact disclosed compensation |
| Reports | Parts A–D, facts/inferences/unknowns, sources, dates, score breakdowns, drafts, screening blockers and concrete channel/attachment plans |
| Application package | Two-line summary, supported matches/gaps, original factual bullet ordering, short outreach and follow-up drafts, screening answers, required consent controls |
| Resume | Exact file SHA-256 and profile hash, user reconciliation attestation, selectable-text provisional export; original resume is not overwritten |
| Deduplication | Persistent company/job keys, stable JOB IDs, new-company history, significant existing-company updates, suspected repost review and configurable domain aliases |
| Approval | Most recent delivered review report, exact channel and action hash, 72-hour expiry, single-use initial actions, invalidation after material changes |
| Execution | Single-use manual portal/email handoffs; separate outcomes and provider evidence for each channel; uncertain submissions cannot be blindly retried |
| Follow-up | One check seven IST calendar days after the earliest confirmed action, separate approval, reply-status check, stopping outcomes, no cold follow-up after portal-only applications |
| Continuity | SQLite transactions, OS-managed run lock, append-only events, durable discovery/source history, complete JSON export |
| Weekly improvement | After seven days of local history, report counts by role family/source, current eligibility, confirmed actions, recorded replies/interviews and recurring skill gaps with explicit denominators |
| Scheduling | Optional Windows Task Scheduler XML for daily discovery at 09:00 IST; generation does not install or enable a task |

This is not a fully autonomous research or browser agent. A person must assess JD semantics, confirm candidate-specific eligibility and unresolved screening answers, research company/contact evidence, and perform portal/email actions. It does not scrape around CAPTCHAs, automate logins, use a mail provider, send report emails, or calculate interview probabilities. Weekly counts describe observed local history; they do not imply interview or conversion probabilities. The `report_email_authorized` configuration field is reserved; changing it does not create an email transport.

## Configure actual sources

`config/settings.json` includes Supabase's Ashby board and Stripe's Greenhouse board. Each run rechecks the link from the configured official employer page, including Stripe's embedded application form. Published postings still need eligibility review. Add another source by providing an employer's own website and careers page:

```powershell
python -m job_agent source-add --company "EMPLOYER" --website "https://OFFICIAL_DOMAIN" --careers-url "https://OFFICIAL_DOMAIN/careers" --tier GOOD
```

This command reads the supplied employer page and saves only supported ATS links or JobPosting metadata actually present there. It does not guess board names, fetch the job feed, or apply. `source-status` shows recorded checks without making another network request. A subsequent `discover` verifies the source and fetches postings.

Each configured ATS source has this structure (placeholders below are deliberately not usable):

```json
{
  "company": "ACTUAL_EMPLOYER",
  "official_website": "https://ACTUAL_OFFICIAL_DOMAIN",
  "official_careers_url": "https://ACTUAL_OFFICIAL_DOMAIN/careers",
  "provider": "greenhouse",
  "board": "ACTUAL_BOARD_TOKEN",
  "tier": "GOOD"
}
```

Supported `provider` values are `greenhouse`, `lever`, `ashby` and `jobposting`. ATS careers pages must contain an actual HTML link or iframe to that exact board or one of its jobs. JavaScript-only pages and access-blocked pages are reported as verification incomplete. An ATS host alone is insufficient evidence of employer identity. Source errors and postings missing from a previously verified feed require rechecking; they do not silently mark jobs closed. One broader title pass runs when fewer than five eligible companies exist in the ledger. Non-engineering roles and explicitly US/Canada-only remote jobs are excluded from engineering discovery; global/APAC remote roles still require India eligibility review.

`jobposting` reads [Schema.org JobPosting](https://schema.org/JobPosting) JSON-LD from `posting_urls` on the configured employer's own domain, falling back to `official_careers_url`. It supports JSON-LD arrays and graphs without executing scripts. Disclosed start dates, exact daily/weekly hours and base salary are retained; estimated salary is ignored. Hours expressed as a range are preserved for review rather than converted into an exact requirement. Date-only deadlines do not acquire an invented timezone. `directApply` metadata does not establish that a usable application form was inspected.

Network fetching uses up to `discovery_workers` concurrent sources (default 3, maximum 4); database updates remain on the main thread. `max_leads_per_run` bounds imported leads (default 150), prioritizing India/remote early-career titles. `discovery_location_terms` controls the geographic lead search, not eligibility. Deferred and excluded counts are explicit; adjust these settings when broadening a search. Full public feeds are still read to check live listing presence. TLS certificate verification stays enabled; blocked requests, DNS, timeout and certificate failures are reported.

The adapters implement the providers' documented public GET interfaces: [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html), [Lever Postings API](https://github.com/lever/postings-api), [Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api). Greenhouse `updated_at` is not treated as the posting date. Ashby `publishedAt`, when disclosed, is retained.

```powershell
python -m job_agent queries
python -m job_agent discover
```

IBM remains the preferred employer and first generated search query. IBM and other unsupported portals use the primary-source import/review route, rather than a guessed private endpoint.

Discovery alone will normally produce watchlist leads. It cannot establish work authorization, compatible academic hours or detailed degree eligibility from the presence of a job on a board. It deliberately does not turn missing requirements into passes.

Discovery and report evaluation now extract explicit numeric experience minima from primary-source job descriptions. A requirement such as “At least 5 years of backend engineering experience” rejects a role incompatible with the supplied internship history and retains the exact excerpt and source. Preferred/ideal experience, education durations, upper bounds, company history and alternative qualifications do not become hard rejections. This extraction only supplies exclusion evidence; it does not auto-pass missing eligibility or invent a match score. Product-management titles are excluded from engineering discovery, including the broader pass.

The output's `pending_clarification` count refers to opportunity records with unresolved conditions, not the number of questions you must answer. The companion `.review.json` file lists each rejection reason and pending condition. `delivered: false` means the report has not been marked reviewed; `delivery.local_report_created: true` confirms that the file was successfully written. It does not indicate a failed discovery run.

## Review and enrich evidence

The compact company overview uses React.js and Vite in `web/company-overview`. It shows a short company description, source-confirmed CEO where available, a dated recent project/update, sector tags, roles requiring review, and source/verification details. Unknown leadership or project information stays explicitly unverified. These are sourced highlights, not a claim to know every employer's newest internal project. For local development in Windows PowerShell, run `npm.cmd ci` then `npm.cmd run dev` in that directory; the built page requires an HTTP server rather than opening `dist/index.html` directly. See the [React project instructions](web/company-overview/README.md).

Private hosted page: [Company Briefs](https://job-search-company-briefs.silvergiant1025.chatgpt.site). Deployment identity is recorded in `data/company-overview-deployment.json` and the Site identity in `web/company-overview/.openai/hosting.json`.

The page supports role and location search, employment/location filters, company sorting and saved companies stored in your browser. Discovery status shows source failures and stale checks; each role shows its employment type and last vacancy check. These controls help review the evidence and do not approve applications.

Each role also shows concise employer-JD highlights: a summary, key requirements, skills and separately labelled optional qualifications or team technologies. `config/role_briefs.json` holds reviewed highlights tied to the source description hash. Public ATS descriptions can supply section excerpts for new or changed roles; missing information stays unverified. The full job description remains linked for complete requirements.

```powershell
python -m job_agent company-web
```

Every `discover` or `report` also refreshes the local page and writes a `REPORT-….companies.json` companion. Company facts are maintained separately in `config/company_briefs.json`; running discovery does not pretend to re-research leadership or product news. Company research older than 30 days is labelled for rechecking. The company export omits your resume, contact information, screening answers, drafts, approvals and submission records.

The private hosted page is a published snapshot. Use **Load newer snapshot** with a later `.companies.json` report to update the current browser tab; the file is read locally and is not uploaded to the server. This does not republish the Site or enable a background schedule. Local reports refresh the local page automatically.

```powershell
python -m job_agent status --id JOB-001
python -m job_agent export-opportunity JOB-001 --output data/JOB-001.review.json
```

Review the official JD and application route, enrich the exported JSON, then reimport it:

```powershell
python -m job_agent import data/JOB-001.review.json
```

The input envelope is `{"opportunities": [...]}`. `examples/opportunity.sample.json` documents the complete shape but is synthetic and rejected by live imports while its `sample` marker is present. Replace every example fact and source when creating a real input. The program validates structure and consistency; it cannot authenticate an analyst's claimed research. Imported evidence and manual confirmation references are user-provided, not independently verified by this software.

Required opportunity fields are `company`, `official_website`, `role`, `canonical_url`, `location` and `employment_type`. Employment type is `internship`, `full_time`, `graduate_programme` or `unknown`. Use the official job ID whenever possible. If absent, URL/role/location form a labelled fallback key. Unknown URL parameters are retained; only known tracking parameters are stripped from normalized keys.

Every eligibility gate marked PASS or FAIL needs an exact reason, primary `source` URL and offset-bearing `checked_at` timestamp. Use UNKNOWN when evidence is insufficient. A PASS may reflect an explicitly inapplicable criterion only when its reason explains why. Do not infer citizenship, work authorization, academic availability, grade conversions or an undergraduate branch exception. The gate names are in `job_agent/policy.py` and the sample.

`primary_source_urls` records employer-linked ATS/evidence URLs. Adding a URL asserts that you established its primary-source provenance; it does not cause the application to independently verify its contents or employer affiliation. The official company domain is also accepted as a primary source.

Other review fields:

- `technical_groups`: one object per required/preferred skill group, with `group`, `priority`, `source` and optional `mandatory`. Required groups are mandatory by default; mark an assessed ranking requirement `mandatory: false` only when the JD supports that interpretation. Unsupported required essentials reject; unsupported preferred skills reduce the technical score.
- `constraints`: supported factual restrictions such as `minimum_professional_years`, `accepted_undergraduate_branches`, `explicit_postgraduate_alternative` and `immediate_full_time`. Preserve the primary evidence in the corresponding gate reasons. Do not use preferred experience as a strict minimum.
- `responsibility_alignment` (0/1/2) with `responsibility_source`; `level_fit` (1/2) with `level_source`. A reviewer assigns these according to the prompt's rubric; an unsupported assessment blocks the score.
- `business_description`/`business_source`, `engineering_context`/`engineering_source`: sourced evidence for the two half-points. Optional headquarters, size and review ratings should include their sources and checked dates in the supplied values. Missing reviews remain “Not available.”
- `compensation`: official `minimum`, `currency: "INR"`, `period: "month"` or `"year"`, and `source`. Record disclosed CTC, fixed/variable pay and any third-party estimate distinctly using additional fields. The scoring engine does not annualize unusual pay or use third-party estimates. Use `null` for undisclosed pay.
- `contact`: published recruiting `name`, `destination`, `recruiting_relevance`, `status`, `source` and `checked_at`. Status is SOURCE_CONFIRMED or PUBLICLY_ATTRIBUTED. No usable contact means no outreach action in the plan. Publication does not establish deliverability; do not guess emails or probe mailboxes.
- `required_questions`: form question keys. Known identity/degree/current-CTC/student-availability fields receive supplied factual answers. Unrecognized questions remain blockers until the candidate supplies an explicit supported answer with `record-answer`. Never invent forced numeric answers or store credentials, government IDs or banking details.
- `required_consents`: exact mandatory consent text. Record the candidate's explicit answer separately with `record-consent`; ordinary application approval does not imply consent. A refused mandatory consent continues to block the package.
- `deadline`: ISO timestamp including the employer's disclosed timezone offset. Do not invent an offset; keep an unclear deadline out of this field and record it as an unresolved gate.

Live checks expire for shortlist purposes after 24 hours. Manual execution rechecks have a five-minute window. Dates after mid-2027 require profile/availability reconfirmation. The alias map `company_domain_aliases` can map a verified old domain to its canonical employer domain; configure aliases before importing history. Existing database keys are not silently rewritten when aliases change.

## Resume and review report

The designated `Pawan_resume.pdf` was supplied during setup on 22 October 2026. The assistant extracted it with PyMuPDF, compared it with `config/profile.json`, and recorded the matching file/profile hashes and review provenance in `data/resume-review.json` and the ledger. This comparison is not independent verification of the resume's claims or a candidate attestation. Availability and work authorization still require role-specific checks.

For a replacement resume, reconcile it with `config/profile.json`, correct any conflicts, and explicitly attest:

```powershell
python -m job_agent confirm-resume --file .\Pawan_resume.pdf --profile-reviewed
python -m job_agent resume-text
python -m job_agent report
```

`confirm-resume` records your attestation; the CLI itself does not parse or independently verify the PDF. An assistant's evidence-backed review is recorded separately with its actual provenance. A changed resume or resume-backed profile fact removes the reconciliation match and blocks external-use packages until reviewed again. Availability-only updates preserve a recorded reconciliation using the resume-backed fact hash, while changing action hashes and invalidating stale approvals. Legacy whole-profile attestations remain conservatively tied to their complete profile hash. `resume-text` creates a provisional text artifact without making it the approved attachment.

Review the generated Markdown file, then record its delivery to you:

```powershell
python -m job_agent mark-delivered REPORT-ID-FROM-OUTPUT
```

Generation is not delivery. Once a delivered report mentions a company, including a watchlist company, it cannot later be labelled new. No email is sent by this command. Only eligible action packages from the most recent delivered review report can receive initial approval; watchlist items are excluded. Reports are saved under `reports/`.

## Approve specific actions and execute through a manual handoff

Approval commands do not submit or send:

```powershell
python -m job_agent approve JOB-001 --channels portal
python -m job_agent approve JOB-002 --channels outreach
python -m job_agent approve JOB-003 --channels portal outreach
python -m job_agent approve --all --channels portal
```

The last command is limited to eligible items awaiting approval in the most recent delivered report. A fresh command is explicit authorization for its displayed scope. In this CLI, the `approve` command is the equivalent of the prompt's natural-language “go ahead”; natural-language command parsing is not implemented.

Use these separate commands when actually needed; neither approves an application:

```powershell
python -m job_agent accept-pay-exception JOB-001
python -m job_agent resolve-repost JOB-002 --distinct-vacancy --evidence "Official evidence that this is a separate vacancy"
python -m job_agent record-consent JOB-003 --question "Exact displayed mandatory consent" --answer yes
python -m job_agent record-answer JOB-003 --question "Exact displayed required question" --answer "Candidate-confirmed truthful answer" --evidence "Candidate confirmation or designated resume section"
```

A changed compensation disclosure needs a new below-floor exception acceptance. Resolve suspected reposts only after establishing that they are distinct opportunities. Generate and review a fresh report after changing material package fields.

Immediately before a manual/IBM handoff, reopen the official vacancy and verify status, eligibility, deadline, exact destination and duplicate history. Record only checks you actually performed:

```powershell
python -m job_agent recheck JOB-001 --evidence "Official URLs and the vacancy, eligibility, deadline, destination and application-history checks performed"
python -m job_agent handoff JOB-001 --channel portal
```

Configured ATS sources are re-fetched before handoff; changed JD content discards stale assessments and invalidates approvals. Imported/manual opportunities require the recorded human recheck within five minutes. For outreach, include the named person's identity, current employer and recruiting relevance in the manual destination check.

The generated `handoffs/TOKEN.json` contains the approved action and exact attachment hash. Approval is consumed when the handoff is created, and the action is marked EXECUTING. **No application or email has been performed by the application.** Perform the approved action using the portal or mail provider yourself, then record its actual result:

```powershell
python -m job_agent record-result TOKEN --outcome confirmed --evidence "Actual portal confirmation reference or sent message ID"
python -m job_agent record-result OTHER-TOKEN --outcome failed --evidence "Observed failure before submission"
python -m job_agent record-result UNCERTAIN-TOKEN --outcome uncertain --evidence "Timeout after possible submission"
```

Supply `--occurred-at "2026-10-21T10:00:00+05:30"` with the actual action time if recording later. Evidence is explicitly marked user-provided in the audit trail. The software cannot independently authenticate a typed reference. An uncertain attempt blocks retries until checked portal/sent-mail evidence confirms it or proves `--outcome not_submitted`. A failed action needs a new explicit approval before a new handoff. Successful channels are never repeated just because another channel failed.

Do not execute an expired or materially changed handoff. The local approval controls cannot prevent a person from independently using a copied handoff outside the program.

## Follow-up, pause and status

Confirmed initial actions set one due timestamp seven calendar days later in IST. The earliest confirmed portal/outreach action anchors the date; recording another channel does not create a second follow-up. The local server and daily run create in-app reminders; external delivery requires explicit opt-in.

```powershell
python -m job_agent status
python -m job_agent check-status JOB-001 --status no_reply --evidence "Authorized application/email sources checked"
python -m job_agent approve JOB-001 --channels follow_up
python -m job_agent recheck JOB-001 --evidence "Current official vacancy and approved destination rechecked"
python -m job_agent handoff JOB-001 --channel follow_up
```

A preapproved follow-up becomes valid at its due check and expires 72 hours afterward. It cannot be handed off early. The current authorized reply/application-status check must be within 24 hours. `--status unknown` holds sending; `--status unknown_reconfirmed` is the candidate's explicit reconfirmation to proceed despite unavailable reply status. Rejection, withdrawal, closure, reply, interview, employer no-follow-up instructions or a previously sent follow-up stop further follow-ups. Portal-only applications do not authorize new cold outreach. Use the existing approved email thread.

```powershell
python -m job_agent skip JOB-001
python -m job_agent pause
python -m job_agent resume
python -m job_agent export --output data/ledger-export.json
```

Pause stops new discovery and handoffs in the ledger. It still allows read-only status and recording the truth about an already-started attempt. It does not change Windows Task Scheduler triggers; disable installed tasks separately. Resume does not revive expired approvals. Preserve `data/ledger.sqlite3` and backups for continuity; do not delete it to reset deduplication. The export includes opportunity payloads, evaluations, packages, channel states, per-channel approvals, attempts, report delivery/company history and all events. It contains personal/application data; keep it local unless intentionally sharing it.

## Different companies each day at 09:00 IST

Daily company discovery is installed in Windows Task Scheduler as `PawanJobAgent-DailyCompanyDiscovery`, at the candidate's requested daily cadence and the prompt's default **09:00 Asia/Kolkata**. A real background run was verified with `LastTaskResult = 0` on 23 October 2026. Inspect the actual current task instead of relying on this historical installation record:

```powershell
python -m job_agent schedule-status
python -m job_agent daily
```

The computer must be on and the selected Windows user signed in. The task uses `pythonw.exe` without opening a console and catches up when available. Overlapping task instances are ignored; the ledger also has a run lock. Pausing the agent prevents discovery. Reinstall only this matching task with `python -m job_agent schedule-install`; an unrelated task with that name is never overwritten. The legacy `schedule-template` command only writes XML and does not install anything.

Scheduler inspection reports access or provider errors explicitly and preserves the last verified schedule record. A failed query does not mean the task is missing.

Each daily run refreshes a catalogue of candidate official careers pages once per IST day, accepts only supported ATS links or JobPosting metadata actually published by the employer, and searches all configured feeds. Company round-robin selection and a per-company lead cap prevent large boards from consuming the review budget. Edit `config/employers.json` to expand the candidate pool, or run `python -m job_agent source-pool-refresh` to recheck it immediately. Unsupported providers and inaccessible pages remain recorded as review needed; no board tokens are guessed.

The target is up to five new companies for review, using current employer-identity and vacancy evidence. Eligibility checks, January 2027 availability and the six-hour daily limit still apply. A company selected on a previous day or mentioned in a delivered report is not labelled new again. Same-day reruns retain their selection. The finite verified source pool may produce fewer companies or none; a daily quota is not guaranteed. Selection does not mean an application is eligible or approved.

Reports remain in `reports/`; the current run result is `data/daily/latest-result.json`, with dated copies alongside it. Persistent company selections live in the existing SQLite ledger. Preserve this history to prevent repeats. The task updates local website assets after discovery finishes. The React page defaults to **Today's companies**, polls for updated local data every minute while visible, and keeps earlier results in **All companies**. A hosted snapshot still requires republishing or loading a newer company JSON. No report email, application, outreach or follow-up is sent by the daily task.

## Tests

The test suite requires pytest, which was already available in the development environment:

```powershell
python -m pytest -q
```

Tests use temporary databases and synthetic fixtures, with no live network calls or employer-facing actions. They cover eligibility and scoring invariants, exact-scope approval, report delivery/history, single-use execution, uncertain-result recovery, attachment changes, partial success, separate follow-up authorization and employer-to-ATS provenance. Public adapters should also be smoke-tested against chosen configured boards when setting up real discovery; site changes and access restrictions remain possible.

## Interactive review workstation (v1.0.0)

Run `python -m job_agent serve --port 8765` and open http://127.0.0.1:8765/review. Search and filter the ledger, inspect evidence, record confirmed answers, review reports, approve individual channels and reconcile manual handoffs. Company briefs remain at http://127.0.0.1:8765/.

See [Phases 2 and 3](PHASES_2_3.md) for implementation details, validation and supported provider/notification boundaries. The hosted Site serves the snapshot; interactive ledger actions use the local Python service.
#   J o b _ A u t o m a t i o n  
 