-- Week 2: a measurable side effect. Each effect bumps a named counter; the exit
-- test asserts every counter ends at exactly 1, which is how we prove the effect
-- fired exactly once across crashes.
CREATE TABLE IF NOT EXISTS counters (
    name  TEXT   PRIMARY KEY,
    value BIGINT NOT NULL DEFAULT 0
);
