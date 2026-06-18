"""Postgres as a task queue: enqueue runs, and let workers claim them.

A claim is one transaction: SELECT ... FOR UPDATE SKIP LOCKED, then UPDATE.
SKIP LOCKED is the bouncer -- a row another worker is mid-claiming is invisible
to everyone else, so two workers never grab the same run.
"""

import uuid

import psycopg
from psycopg.types.json import Json


def enqueue_run(conn: psycopg.Connection, agent_name: str, input: dict) -> str:
    """Create a run waiting in the queue (status 'queued')."""
    run_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO runs (run_id, agent_name, input, status) "
        "VALUES (%s, %s, %s, 'queued')",
        (run_id, agent_name, Json(input)),
    )
    conn.commit()
    return run_id


def claim_run(conn: psycopg.Connection, worker_id: str, lease_seconds: int = 30):
    """Claim one queued run for this worker; return its id, or None if empty.

    The SELECT locks the chosen row and SKIP LOCKED hides rows other workers are
    already claiming, so the same run is never handed to two workers.
    """
    row = conn.execute(
        "SELECT run_id FROM runs WHERE status = 'queued' "
        "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
    ).fetchone()
    if row is None:
        conn.commit()
        return None
    run_id = row[0]
    conn.execute(
        "UPDATE runs SET status = 'running', lease_owner = %s, "
        "lease_expiry = now() + make_interval(secs => %s), "
        "lease_token = lease_token + 1 "
        "WHERE run_id = %s",
        (worker_id, lease_seconds, run_id),
    )
    conn.commit()
    return run_id
