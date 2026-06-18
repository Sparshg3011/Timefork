"""Postgres as a task queue: many workers, no double-claim.

We enqueue 20 runs, then 4 worker threads race to claim them. SKIP LOCKED
guarantees each run is claimed by exactly one worker.

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/06_workers_claim.py
"""

import threading

from timefork.events import connect
from timefork.queue import claim_run, enqueue_run


def main():
    with connect() as conn:
        ids = [enqueue_run(conn, "agent", {"n": i}) for i in range(20)]
    print(f"enqueued {len(ids)} runs")

    claimed = {}
    lock = threading.Lock()

    def worker(name):
        count = 0
        with connect() as conn:
            while True:
                rid = claim_run(conn, name)
                if rid is None:
                    break
                with lock:
                    claimed.setdefault(rid, []).append(name)
                count += 1
        print(f"  {name} claimed {count} runs")

    threads = [threading.Thread(target=worker, args=(f"worker-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ours = {r: ws for r, ws in claimed.items() if r in set(ids)}
    doubles = {r: ws for r, ws in ours.items() if len(ws) > 1}
    print(f"of our {len(ids)} runs: {len(ours)} claimed, {len(doubles)} double-claimed")
    print(f"each claimed by exactly one worker: {len(ours) == len(ids) and not doubles}")


if __name__ == "__main__":
    main()
