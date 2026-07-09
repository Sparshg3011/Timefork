"""Tests for the sweeper: lapsed leases are requeued, fresh ones are not."""

from timefork.events import connect
from timefork.queue import claim_run, enqueue_run, sweep_expired


def _status_owner(conn, run_id):
    return conn.execute(
        "SELECT status, lease_owner FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()


def test_sweeper_requeues_an_expired_lease():
    with connect() as conn:
        enqueue_run(conn, "agent", {})
        run_id, _ = claim_run(conn, "dead-worker", lease_seconds=30)
        # Force the lease into the past, as if the worker died.
        conn.execute(
            "UPDATE runs SET lease_expiry = now() - make_interval(secs => 5) "
            "WHERE run_id = %s",
            (run_id,),
        )
        conn.commit()

        requeued = sweep_expired(conn)
        assert run_id in requeued
        assert _status_owner(conn, run_id) == ("queued", None)


def test_sweeper_leaves_a_fresh_lease_alone():
    with connect() as conn:
        enqueue_run(conn, "agent", {})
        run_id, _ = claim_run(conn, "live-worker", lease_seconds=30)

        requeued = sweep_expired(conn)
        assert run_id not in requeued
        assert _status_owner(conn, run_id) == ("running", "live-worker")
