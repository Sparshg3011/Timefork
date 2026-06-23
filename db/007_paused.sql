-- A run can stop and wait for a human to approve a side effect (a refund).
-- 'paused' marks that wait; the decision is recorded as an event and the run
-- is re-queued, so the pause survives a process death like everything else.

ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE runs ADD CONSTRAINT runs_status_check
    CHECK (status IN ('queued', 'running', 'completed', 'failed', 'paused'));
