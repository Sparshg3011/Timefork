"""Tests for the task queue: no run is ever claimed by two workers."""

import threading

from timefork.events import connect
from timefork.queue import claim_run, enqueue_run


def test_skip_locked_prevents_double_claim():
    with connect() as conn:
        ids = [enqueue_run(conn, "agent", {"n": i}) for i in range(30)]

    claimed = {}
    lock = threading.Lock()

    def worker(name):
        with connect() as conn:
            while True:
                rid = claim_run(conn, name)
                if rid is None:
                    break
                with lock:
                    claimed.setdefault(rid, []).append(name)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every run we enqueued was claimed exactly once, and nothing was double-claimed.
    for run_id in ids:
        assert claimed.get(run_id) is not None and len(claimed[run_id]) == 1
    assert all(len(workers) == 1 for workers in claimed.values())
