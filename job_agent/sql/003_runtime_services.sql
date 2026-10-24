CREATE TABLE event_integrity (
    event_id INTEGER PRIMARY KEY REFERENCES events(id),
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE TABLE opportunity_aliases (
    alias_key TEXT PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    source_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER REFERENCES events(id),
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT
);
CREATE TRIGGER guard_approval_transition BEFORE UPDATE OF state ON opportunities
WHEN new.state='APPROVED' AND old.state NOT IN ('APPROVED','AWAITING_APPROVAL','READY_FOR_REVIEW','ACTION_FAILED','EXPIRED')
BEGIN SELECT RAISE(ABORT,'Invalid approval state transition'); END;
CREATE TABLE attempt_expiry (
    token TEXT PRIMARY KEY REFERENCES attempts(token),
    expires_at TEXT NOT NULL
);

CREATE TRIGGER guard_execution_transition BEFORE UPDATE OF state ON opportunities
WHEN new.state='EXECUTING' AND old.state NOT IN ('APPROVED','EXECUTING','SUBMITTED_CONFIRMED','OUTREACH_SENT_CONFIRMED','PARTIAL_SUCCESS','ACTION_FAILED')
BEGIN SELECT RAISE(ABORT,'Invalid execution state transition'); END;
