-- PostgreSQL schema (deployment equivalent of store/db.py's SQLite tables).
--
-- Two design points:
--   1. No raw IP addresses. ip_hash supports "same network as before" and
--      city supports "new location"; neither needs the address itself.
--   2. Biometric templates are derived statistics, never raw event
--      streams. A stolen template yields timing distributions, not a
--      replayable recording.

CREATE TABLE IF NOT EXISTS app_user (
    user_id        VARCHAR(64) PRIMARY KEY,
    enrolled_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    keystroke_tpl  JSONB,
    mouse_tpl      JSONB,
    session_prof   JSONB
);

CREATE TABLE IF NOT EXISTS security_event (
    id             BIGSERIAL PRIMARY KEY,
    user_id        VARCHAR(64) NOT NULL,
    event_type     VARCHAR(32) NOT NULL,
    amount         NUMERIC(18,2),
    beneficiary_id VARCHAR(64),
    device_id      VARCHAR(64),
    ip_hash        VARCHAR(64),      -- salted hash, never the raw IP
    city           VARCHAR(64),      -- derived, IP then discarded
    channel        VARCHAR(16),
    occurred_at    TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_user_time
    ON security_event (user_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS session_record (
    id         BIGSERIAL PRIMARY KEY,
    user_id    VARCHAR(64) NOT NULL,
    features   JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_session_user ON session_record (user_id);

CREATE TABLE IF NOT EXISTS verification (
    id            BIGSERIAL PRIMARY KEY,
    user_id       VARCHAR(64) NOT NULL,
    risk_prob     NUMERIC(6,5),
    band          VARCHAR(16) NOT NULL,
    decision      VARCHAR(16) NOT NULL,
    dimensions    JSONB NOT NULL,
    model_version VARCHAR(32) NOT NULL,
    latency_ms    INTEGER,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_verif_user
    ON verification (user_id, decided_at DESC);

-- Least privilege. The application cannot rewrite history, so audit
-- integrity does not depend on the application being uncompromised.
--
-- Verify with:  SET ROLE ueba_app;
--               UPDATE verification SET band='LOW' WHERE id=1;
--               -- expected: ERROR: permission denied

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ueba_app') THEN
        CREATE ROLE ueba_app LOGIN PASSWORD 'change_me_via_env';
    END IF;
END
$$;

GRANT SELECT, INSERT, UPDATE ON app_user       TO ueba_app;
GRANT SELECT, INSERT         ON security_event TO ueba_app;
GRANT SELECT, INSERT         ON session_record TO ueba_app;
GRANT SELECT, INSERT         ON verification   TO ueba_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ueba_app;

REVOKE UPDATE, DELETE, TRUNCATE ON security_event FROM ueba_app;
REVOKE UPDATE, DELETE, TRUNCATE ON verification   FROM ueba_app;
