-- Week 3: fencing. Each event records the lease_token of the worker that wrote
-- it. Combined with a fenced append (insert only if that token still matches the
-- run's current lease_token), this rejects writes from a presumed-dead worker
-- whose run was reassigned -- a thawed 'zombie' can no longer corrupt the diary.
ALTER TABLE events ADD COLUMN IF NOT EXISTS lease_token BIGINT NOT NULL DEFAULT 0;
