"""Forking: rewind a run to step k, patch it, and branch a new timeline.

The child copies the parent's event prefix (steps 1..k) -- so replaying it costs
zero executor calls -- then records a PATCH_APPLIED event and is enqueued. A
worker replays the free prefix and diverges from there. A fork is a fresh
experiment, not a proof of what the parent 'would have' done.
"""

import uuid

import psycopg
from psycopg.types.json import Json

from .events import UnknownRunError


class InvalidForkPointError(Exception):
    """The requested at_seq cannot be forked at: out of range, or it would
    split an operation (an intent without its completion, a question without
    its answer) and leave the child a diary that can never replay."""


def fork_run(conn: psycopg.Connection, parent_run_id: str, at_seq: int, patch: dict) -> str:
    """Fork `parent_run_id` at step `at_seq` into a new queued run, applying
    `patch` (e.g. {"system_prompt": "..."}). Returns the child run id.

    The run row, the copied prefix, and the patch event all commit together.
    """
    parent = conn.execute(
        "SELECT agent_name, input FROM runs WHERE run_id = %s", (parent_run_id,)
    ).fetchone()
    if parent is None:
        conn.rollback()
        raise UnknownRunError(f"no run with id {parent_run_id}")
    agent_name, input = parent

    # The fork point must be a real step, and a clean one: cutting right after
    # a TOOL_INTENT or an APPROVAL_REQUESTED would copy half an operation, and
    # the child's replay would meet the patch where the other half belongs.
    total = conn.execute(
        "SELECT count(*) FROM events WHERE run_id = %s", (parent_run_id,)
    ).fetchone()[0]
    if not 1 <= at_seq <= total:
        conn.rollback()
        raise InvalidForkPointError(
            f"at_seq {at_seq} is out of range for run {parent_run_id} (1..{total})"
        )
    boundary = conn.execute(
        "SELECT type FROM events WHERE run_id = %s AND seq = %s",
        (parent_run_id, at_seq),
    ).fetchone()[0]
    if boundary in ("TOOL_INTENT", "APPROVAL_REQUESTED"):
        conn.rollback()
        raise InvalidForkPointError(
            f"at_seq {at_seq} splits a {boundary} from its completion; "
            f"fork one step earlier or later"
        )

    child_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO runs (run_id, agent_name, input, status, parent_run_id, fork_seq) "
        "VALUES (%s, %s, %s, 'queued', %s, %s)",
        (child_id, agent_name, Json(input), parent_run_id, at_seq),
    )
    # Copy the parent's prefix (events 1..at_seq) -- the part we do NOT re-run.
    conn.execute(
        "INSERT INTO events (run_id, seq, type, payload, lease_token) "
        "SELECT %s, seq, type, payload, 0 FROM events "
        "WHERE run_id = %s AND seq <= %s",
        (child_id, parent_run_id, at_seq),
    )
    # The patch is the next entry in the child's diary; replay applies it here.
    conn.execute(
        "INSERT INTO events (run_id, seq, type, payload) VALUES (%s, %s, %s, %s)",
        (child_id, at_seq + 1, "PATCH_APPLIED", Json(patch)),
    )
    conn.commit()
    return child_id


def parent_of(conn: psycopg.Connection, run_id: str):
    """Return (parent_run_id, fork_seq) for a run, or (None, None) if it's a root."""
    return conn.execute(
        "SELECT parent_run_id, fork_seq FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()


def children_of(conn: psycopg.Connection, run_id: str) -> list[tuple[str, int]]:
    """Return [(child_run_id, fork_seq), ...] -- the forks branched off this run."""
    rows = conn.execute(
        "SELECT run_id, fork_seq FROM runs WHERE parent_run_id = %s "
        "ORDER BY fork_seq, created_at",
        (run_id,),
    ).fetchall()
    return [(rid, fseq) for rid, fseq in rows]
