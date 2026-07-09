"""Tests for leases and heartbeats."""

from timefork.events import connect
from timefork.queue import claim_run, enqueue_run, heartbeat


def test_heartbeat_only_by_owner():
    with connect() as conn:
        enqueue_run(conn, "agent", {})
        run_id, _ = claim_run(conn, "owner", lease_seconds=30)

        assert heartbeat(conn, run_id, "owner") is True
        assert heartbeat(conn, run_id, "someone-else") is False


def test_heartbeat_advances_the_lease():
    with connect() as conn:
        enqueue_run(conn, "agent", {})
        run_id, _ = claim_run(conn, "owner", lease_seconds=1)

        before = conn.execute(
            "SELECT lease_expiry FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()[0]
        heartbeat(conn, run_id, "owner", lease_seconds=30)
        after = conn.execute(
            "SELECT lease_expiry FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()[0]

        assert after > before
