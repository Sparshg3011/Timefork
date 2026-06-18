-- Week 2: the dedupe store for exactly-once side effects.
-- A side effect's idempotency key ({run_id}-{seq}) is written here with its
-- result, in the same transaction that performs the effect. Checking this table
-- before acting -- key present means "already done" -- is what makes an email
-- or a payment fire exactly once across crashes.
CREATE TABLE IF NOT EXISTS completed_keys (
    -- The idempotency key. As PRIMARY KEY it can be inserted exactly once, so a
    -- retry after a crash cannot record (or perform) the same effect twice.
    idempotency_key TEXT        PRIMARY KEY,
    result          JSONB       NOT NULL,
    completed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
