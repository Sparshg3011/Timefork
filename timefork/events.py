"""The diary module: create runs and append/read their event logs.

Every write is one short transaction, committed before the function returns.
Once append_event returns, that event is durable.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Json

DEFAULT_DATABASE_URL = "postgresql://timefork:timefork@localhost:5433/timefork"


def connect() -> psycopg.Connection:
    """Open a connection using DATABASE_URL (defaults to the local compose DB)."""
    return psycopg.connect(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))


class DuplicateSequenceError(Exception):
    """An event already exists at this (run_id, seq) slot."""

    def __init__(self, run_id: str, seq: int) -> None:
        super().__init__(f"event {seq} already exists for run {run_id}")
        self.run_id = run_id
        self.seq = seq


class UnknownRunError(Exception):
    """No run with this run_id exists."""


@dataclass(frozen=True)
class Event:
    """One diary entry, read back from the log."""

    run_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


def create_run(conn: psycopg.Connection, agent_name: str, input: dict[str, Any]) -> str:
    """Create a run and return its id (generated here, not by the database)."""
    run_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO runs (run_id, agent_name, input) VALUES (%s, %s, %s)",
            (run_id, agent_name, Json(input)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return run_id


def append_event(
    conn: psycopg.Connection, run_id: str, seq: int, type: str, payload: dict[str, Any]
) -> None:
    """Append entry `seq` to a run's diary.

    The (run_id, seq) primary key makes a double-append impossible; we just
    translate the database's refusal into a precise exception.
    """
    try:
        conn.execute(
            "INSERT INTO events (run_id, seq, type, payload) VALUES (%s, %s, %s, %s)",
            (run_id, seq, type, Json(payload)),
        )
        conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        raise DuplicateSequenceError(run_id, seq) from exc
    except Exception:
        conn.rollback()
        raise


def read_events(conn: psycopg.Connection, run_id: str) -> list[Event]:
    """Read a run's full diary, oldest first. This is what replay walks."""
    rows = conn.execute(
        "SELECT run_id, seq, type, payload, created_at"
        " FROM events WHERE run_id = %s ORDER BY seq",
        (run_id,),
    ).fetchall()
    return [Event(*row) for row in rows]


def set_run_status(conn: psycopg.Connection, run_id: str, status: str) -> None:
    """Update a run's lifecycle status (the CHECK constraint vets the value)."""
    try:
        cur = conn.execute(
            "UPDATE runs SET status = %s WHERE run_id = %s", (status, run_id)
        )
        found = cur.rowcount == 1
        if found:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    if not found:
        raise UnknownRunError(f"no run with id {run_id}")
