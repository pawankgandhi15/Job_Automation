CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_key TEXT NOT NULL, opportunity_key TEXT NOT NULL UNIQUE,
  payload TEXT NOT NULL, material_hash TEXT NOT NULL,
  first_seen_at TEXT NOT NULL, last_verified_at TEXT,
  state TEXT NOT NULL DEFAULT 'DISCOVERED', evaluation TEXT,
  package TEXT, package_hash TEXT,
  portal_status TEXT NOT NULL DEFAULT 'NOT_STARTED',
  outreach_status TEXT NOT NULL DEFAULT 'NOT_STARTED',
  follow_up_due_at TEXT, follow_up_status TEXT NOT NULL DEFAULT 'NOT_SCHEDULED',
  follow_up_sent_at TEXT, submitted_at TEXT, last_error TEXT,
  status_check TEXT, report_id TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  channel TEXT NOT NULL, scope_hash TEXT NOT NULL,
  approved_at TEXT NOT NULL, valid_from TEXT NOT NULL, expires_at TEXT NOT NULL,
  consumed_at TEXT, invalidated_at TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
  token TEXT PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  channel TEXT NOT NULL, approval_id INTEGER NOT NULL REFERENCES approvals(id),
  started_at TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, generated_at TEXT NOT NULL, delivered_at TEXT,
  path TEXT NOT NULL, opportunity_ids TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reported_companies (
  company_key TEXT PRIMARY KEY, report_id TEXT NOT NULL, reported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  opportunity_id INTEGER, action TEXT NOT NULL, result TEXT NOT NULL, evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_runs (
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, summary TEXT NOT NULL, report_id TEXT
);
CREATE TABLE IF NOT EXISTS source_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES discovery_runs(id),
  source_key TEXT NOT NULL, company TEXT NOT NULL, provider TEXT NOT NULL,
  checked_at TEXT NOT NULL, status TEXT NOT NULL, postings_count INTEGER NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS daily_companies (
  day TEXT NOT NULL, company_key TEXT NOT NULL UNIQUE, company_domain TEXT NOT NULL,
  position INTEGER NOT NULL, selected_at TEXT NOT NULL,
  PRIMARY KEY(day, company_key)
);
