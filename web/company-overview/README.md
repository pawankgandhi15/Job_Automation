# Company Briefs · React

A React 19 + Vite site for reviewing daily employer discoveries and job openings. Company cards show sourced details (including CEO and recent updates when available), role summaries, requirements, locations, employment types, and direct application links. Search, filters, sorting, saved companies, discovery freshness, snapshot imports, and mobile layouts are included.

## Pages

- `/` — company research, the current India Standard Time daily selection, and job opening links.
- `/review` — local opportunity queue, evidence and report review, channel approvals, and manual handoff tracking. This view requires the parent Python app and its local ledger.

Applications and messages are not submitted by the website. The review screen prepares and tracks explicitly approved manual actions.

## Run locally

From this directory, start Vite:

```powershell
npm.cmd ci
npm.cmd run dev
```

From the parent project (`E:\Projects\Job_automation`), start the local API in another terminal:

```powershell
python -m job_agent serve --port 8765
```

Open the URL printed by Vite, normally http://127.0.0.1:5173. Vite proxies API requests to the local service. The static company page can be hosted separately; interactive review needs the local Python backend. Do not open `dist/index.html` as a file because the page loads JSON over HTTP.

Node.js and npm are required. Use `npm` instead of `npm.cmd` on macOS or Linux.

## Data and updates

The Python agent writes company-only snapshots to `public/companies.json` and `dist/companies.json`, plus a dated copy beside each discovery report. Private candidate profiles, screening answers, drafts, and approvals are not exported. Run `python -m job_agent company-web` from the parent project to refresh the local website from the existing ledger.

The page checks for newer JSON every minute while visible and when the tab regains focus. A hosted page contains a static snapshot until it is rebuilt and republished. The **Load newer snapshot** action can display a local snapshot in the current tab. Daily selections use India Standard Time; past selections are available in **All companies**. The system can show fewer companies than the daily target when sources or new evidence are limited.

Company and opening facts depend on recorded source checks. The page shows research freshness and discovery source health; missing or stale details remain identified as such.

## Build and checks

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run preview
npm.cmd run test:browser
```

The browser check uses installed Chrome, a temporary loopback server, and an isolated browser profile. Set `CHROME_PATH` to choose another Chrome executable. Its screenshots go under the project data directory.

See the parent project [`README.md`](../../README.md) and [`PHASES_2_3.md`](../../PHASES_2_3.md) for setup and backend details.
