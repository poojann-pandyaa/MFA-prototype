-- UEBA service schema
--
-- Two design points:
--
-- 1. No raw IP addresses. ip_hash supports "same network as before" and
--    city supports "new location"; neither requires the address itself.
--    This is the data minimisation position under the DPDP Act 2023.
--
-- 2. model_version exists from the first migration. Once a second model
--    version is in play, every historical decision must record which model
--    produced it, or the evaluation is not reconstructable.

CREATE TABLE IF NOT EXISTS security_event (
    id              BIGSERIAL PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL,
    event_type      VARCHAR(32)  NOT NULL,
    amount          NUMERIC(18,2),
    beneficiary_id  VARCHAR(64),
    device_id       VARCHAR(64),          -- hashed upstream
    ip_hash         VARCHAR(64),          -- salted hash, never the raw IP
    city            VARCHAR(64),          -- derived, IP then discarded
    channel         VARCHAR(16),
    occurred_at     TIMESTAMPTZ  NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- The feature engine's only access pattern.
CREATE INDEX IF NOT EXISTS idx_event_user_time
    ON security_event (user_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS risk_decision (
    id              BIGSERIAL PRIMARY KEY,
    user_id         VARCHAR(64)  NOT NULL,
    event_id        BIGINT       REFERENCES security_event(id),
    risk_prob       NUMERIC(6,5),
    raw_score       NUMERIC(12,6),
    band            VARCHAR(16)  NOT NULL,
    status          VARCHAR(24)  NOT NULL,   -- OK | INSUFFICIENT_DATA
    features        JSONB,
    model_version   VARCHAR(32)  NOT NULL,
    shadow_of       BIGINT       REFERENCES risk_decision(id),
    latency_ms      INTEGER,
    decided_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decision_user_time
    ON risk_decision (user_id, decided_at DESC);

-- Least privilege. The application cannot rewrite history; audit integrity
-- does not depend on the application being correct or uncompromised.
--
-- Verify with:  SET ROLE ueba_app;
--               UPDATE risk_decision SET band='LOW' WHERE id=1;
--               -- expected: ERROR: permission denied
-- Keep that failing query as evidence for the report.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ueba_app') THEN
        CREATE ROLE ueba_app LOGIN PASSWORD 'change_me_via_env';
    END IF;
END
$$;

GRANT SELECT, INSERT ON security_event TO ueba_app;
GRANT SELECT, INSERT ON risk_decision  TO ueba_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ueba_app;

REVOKE UPDATE, DELETE, TRUNCATE ON security_event FROM ueba_app;
REVOKE UPDATE, DELETE, TRUNCATE ON risk_decision  FROM ueba_app;
