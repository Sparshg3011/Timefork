"""Leases and heartbeats: a worker's dead-man's switch.

A claimed run carries a lease (lease_owner + lease_expiry). A live worker
heartbeats to push the deadline forward; if it dies (stops heartbeating), the
lease lapses and the run becomes reclaimable. We use a short 2-second lease so
you can watch it happen.

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/07_lease_heartbeat.py
"""

import time

from timefork.events import connect
from timefork.queue import claim_run, enqueue_run, heartbeat


def lease_state(conn, run_id):
    row = conn.execute(
        "SELECT lease_owner, (lease_expiry < clock_timestamp()) AS expired "
        "FROM runs WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    return {"owner": row[0], "expired": row[1]}


def main():
    with connect() as conn:
        enqueue_run(conn, "agent", {})
        run_id, _token = claim_run(conn, "worker-A", lease_seconds=2)
        print(f"claimed by worker-A: {lease_state(conn, run_id)}")

        # A live worker heartbeats and stays fresh.
        ok = heartbeat(conn, run_id, "worker-A", lease_seconds=2)
        print(f"worker-A heartbeat -> {ok}; lease {lease_state(conn, run_id)}")

        # A different worker cannot extend a lease it does not own.
        ok_b = heartbeat(conn, run_id, "worker-B", lease_seconds=2)
        print(f"worker-B heartbeat -> {ok_b}  (it doesn't own the lease)")

        # worker-A 'dies' -- it stops heartbeating, so the lease lapses.
        print("worker-A dies; waiting 3s for the 2s lease to lapse...")
        time.sleep(3)
        print(f"after the deadline: {lease_state(conn, run_id)}")
        print("-> an expired lease means a presumed-dead worker; Day 3's sweeper requeues it")


if __name__ == "__main__":
    main()
