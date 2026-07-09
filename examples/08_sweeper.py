"""The sweeper: a dead worker's job goes back in the queue.

worker-A claims a run with a 1-second lease, then 'dies' (stops heartbeating).
The sweeper finds the lapsed lease and requeues the run, and worker-B reclaims
it. This is the whole failover story; the reclaiming worker would then resume
from the diary (Weeks 1-2).

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/08_sweeper.py
"""

import time

from timefork.events import connect
from timefork.queue import claim_run, enqueue_run, sweep_expired


def show(conn, run_id):
    row = conn.execute(
        "SELECT status, lease_owner FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    return {"status": row[0], "owner": row[1]}


def main():
    with connect() as conn:
        enqueue_run(conn, "agent", {})
        run_id, _ = claim_run(conn, "worker-A", lease_seconds=1)
        print(f"worker-A claimed it:        {show(conn, run_id)}")

        print("worker-A dies; waiting 2s for the 1s lease to lapse...")
        time.sleep(2)

        requeued = sweep_expired(conn)
        print(f"sweeper requeued {len(requeued)} run(s); ours is now {show(conn, run_id)}")

        # A healthy worker reclaims it (skipping any other queued runs).
        while True:
            got = claim_run(conn, "worker-B", lease_seconds=30)
            if got is None or got[0] == run_id:
                break
        print(f"worker-B reclaimed it:      {show(conn, run_id)}")


if __name__ == "__main__":
    main()
