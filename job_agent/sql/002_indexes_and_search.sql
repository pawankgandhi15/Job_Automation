CREATE INDEX IF NOT EXISTS idx_opportunities_company ON opportunities(company_key);
CREATE INDEX IF NOT EXISTS idx_opportunities_state_score ON opportunities(state, json_extract(evaluation, '$.match_score') DESC, last_verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_scope ON approvals(opportunity_id, channel, scope_hash);
CREATE INDEX IF NOT EXISTS idx_attempts_opportunity ON attempts(opportunity_id, channel, status);
CREATE INDEX IF NOT EXISTS idx_events_opportunity ON events(opportunity_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_source_checks_run ON source_checks(run_id);
CREATE INDEX IF NOT EXISTS idx_source_checks_latest ON source_checks(source_key, id DESC);

CREATE VIRTUAL TABLE opportunities_fts USING fts5(
    opportunity_id UNINDEXED,
    company,
    role,
    location,
    description,
    tokenize='unicode61'
);

INSERT INTO opportunities_fts(rowid, opportunity_id, company, role, location, description)
SELECT id, id, json_extract(payload, '$.company'), json_extract(payload, '$.role'),
       json_extract(payload, '$.location'), coalesce(json_extract(payload, '$.description'), '')
FROM opportunities;

CREATE TRIGGER opportunities_fts_insert AFTER INSERT ON opportunities BEGIN
    INSERT INTO opportunities_fts(rowid, opportunity_id, company, role, location, description)
    VALUES(new.id, new.id, json_extract(new.payload, '$.company'), json_extract(new.payload, '$.role'), json_extract(new.payload, '$.location'), coalesce(json_extract(new.payload, '$.description'), ''));
END;

CREATE TRIGGER opportunities_fts_update AFTER UPDATE OF payload ON opportunities
WHEN new.payload IS NOT old.payload BEGIN
    DELETE FROM opportunities_fts WHERE rowid = old.id;
    INSERT INTO opportunities_fts(rowid, opportunity_id, company, role, location, description)
    VALUES(new.id, new.id, json_extract(new.payload, '$.company'), json_extract(new.payload, '$.role'), json_extract(new.payload, '$.location'), coalesce(json_extract(new.payload, '$.description'), ''));
END;

CREATE TRIGGER opportunities_fts_delete AFTER DELETE ON opportunities BEGIN
    DELETE FROM opportunities_fts WHERE rowid = old.id;
END;
