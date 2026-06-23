-- Week 4: forking. A forked run records its lineage -- which run it branched
-- from (parent_run_id) and at which step (fork_seq). The child copies the
-- parent's event prefix up to fork_seq, so replaying it costs zero executor
-- calls, then diverges via a PATCH_APPLIED event.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS parent_run_id TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS fork_seq      BIGINT;
