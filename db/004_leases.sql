-- Week 3: turn the runs table into a leased task queue.
-- A 'queued' run is waiting for a worker. Claiming it stamps the worker's
-- identity (lease_owner) and a deadline (lease_expiry) and moves it to 'running'.
-- lease_token is a monotonic fencing token that rises on every claim; a worker
-- presumed dead (its lease expired and the run re-claimed) holds a stale token,
-- and its writes get rejected on append (enforced in a later migration).

-- Allow the new 'queued' status.
ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE runs ADD CONSTRAINT runs_status_check
    CHECK (status IN ('queued', 'running', 'completed', 'failed'));

-- Lease columns (NULL until a run is claimed).
ALTER TABLE runs ADD COLUMN IF NOT EXISTS lease_owner  TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS lease_expiry TIMESTAMPTZ;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS lease_token  BIGINT NOT NULL DEFAULT 0;
