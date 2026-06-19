"""Fencing tokens: a thawed zombie worker cannot write.

The story: worker-A claims a run (token 1) and remembers it. worker-A freezes;
its lease lapses; the sweeper requeues the run; worker-B claims it (token 2).
worker-A thaws and tries to write with its stale token 1 -- and the database
rejects it. (We drive claim/requeue/reclaim with plain UPDATEs here so the focus
stays on the fence; claim_run + sweep_expired do exactly this in the real system.)

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/09_fencing.py
"""

from timefork.events import StaleFenceError, append_event, connect, read_events
from timefork.queue import enqueue_run, read_lease_token


def claim_as(conn, run_id, worker):
    # What claim_run does to one row: take it and bump the fencing token.
    conn.execute(
        "UPDATE runs SET status = 'running', lease_owner = %s, "
        "lease_token = lease_token + 1 WHERE run_id = %s",
        (worker, run_id),
    )
    conn.commit()
    return read_lease_token(conn, run_id)


def main():
    with connect() as conn:
        run_id = enqueue_run(conn, "agent", {})

        token_a = claim_as(conn, run_id, "worker-A")
        print(f"worker-A claimed it; remembers token {token_a}")

        # worker-A freezes; lease lapses; sweeper requeues; worker-B claims it.
        token_b = claim_as(conn, run_id, "worker-B")
        print(f"worker-B reclaimed it; token is now {token_b}")

        # worker-A thaws and tries to append with its stale token.
        try:
            append_event(conn, run_id, 1, "LLM_CALLED", {"by": "zombie-A"}, lease_token=token_a)
            print("zombie write SUCCEEDED (bad!)")
        except StaleFenceError as exc:
            print(f"zombie write REJECTED by the database: {exc}")

        # worker-B, holding the current token, writes fine.
        append_event(conn, run_id, 1, "LLM_CALLED", {"by": "worker-B"}, lease_token=token_b)
        print(f"worker-B write accepted; diary has {len(read_events(conn, run_id))} event(s)")


if __name__ == "__main__":
    main()
