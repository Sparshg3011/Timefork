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


def claim_run(
    conn: psycopg.Connection,
    worker_id: str,
    lease_seconds: int = 30,
    agent_name: str | None = None,
):
    """Claim one queued run for this worker; return its id, or None if empty.

    The SELECT locks the chosen row and SKIP LOCKED hides rows other workers are
    already claiming, so the same run is never handed to two workers. An optional
    agent_name restricts which runs this worker will pick up.
    """
    if agent_name is None:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE status = 'queued' "
            "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE status = 'queued' AND agent_name = %s "
            "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",
            (agent_name,),
        ).fetchone()
    if row is None:
        conn.commit()
        return None
    run_id = row[0]
    # clock_timestamp() is the real wall-clock time; now() is the transaction's
    # start time, which goes stale if an earlier read left a transaction open.
    conn.execute(
        "UPDATE runs SET status = 'running', lease_owner = %s, "
        "lease_expiry = clock_timestamp() + make_interval(secs => %s), "
        "lease_token = lease_token + 1 "
        "WHERE run_id = %s",
        (worker_id, lease_seconds, run_id),
    )
    conn.commit()
    return run_id


def heartbeat(
    conn: psycopg.Connection, run_id: str, worker_id: str, lease_seconds: int = 30
) -> bool:
    """Extend a run's lease -- the worker's 'still alive' signal.

    Only the current owner of a still-running run can extend it; if the run was
    reclaimed or already finished, the heartbeat fails and returns False.
    """
    cur = conn.execute(
        "UPDATE runs SET lease_expiry = clock_timestamp() + make_interval(secs => %s) "
        "WHERE run_id = %s AND lease_owner = %s AND status = 'running'",
        (lease_seconds, run_id, worker_id),
    )
    conn.commit()
    return cur.rowcount == 1


def sweep_expired(conn: psycopg.Connection) -> list[str]:
    """Requeue runs whose lease has lapsed -- their worker is presumed dead.

    This is the entire failover mechanism: an expired lease goes back to
    'queued' (owner cleared) so a healthy worker reclaims it and resumes from
    the diary. lease_token is left untouched; the next claim bumps it, which is
    what later fences out the presumed-dead worker. Returns the requeued ids.
    """
    rows = conn.execute(
        "UPDATE runs SET status = 'queued', lease_owner = NULL, lease_expiry = NULL "
        "WHERE status = 'running' AND lease_expiry < clock_timestamp() "
        "RETURNING run_id"
    ).fetchall()
    conn.commit()
    return [r[0] for r in rows]


def read_lease_token(conn: psycopg.Connection, run_id: str) -> int:
    """The run's current fencing token. A worker captures this at claim time and
    stamps its appends with it; the database rejects appends with a stale one."""
    return conn.execute(
        "SELECT lease_token FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()[0]
