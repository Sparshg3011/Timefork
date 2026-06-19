"""Launch a fleet of worker processes to drain a queue of jobs.

Happy path for now (no kills): enqueue N jobs, start W worker processes, wait
until every job completes, then verify every counter is exactly 1. The chaos
exit test will extend this with random kills + a sweeper.

  python harness/fleet.py [workers] [jobs]
"""

import os
import subprocess
import sys
import time

from timefork.events import connect
from timefork.queue import enqueue_run

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER = os.path.join(REPO_ROOT, "harness", "worker.py")
AGENT = "fleet_job"
STEPS = 3  # side effects (counters) per job; matches harness/worker.py


def _enqueue_jobs(n):
    with connect() as conn:
        return [enqueue_run(conn, AGENT, {}) for _ in range(n)]


def _statuses(ids):
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_id, status FROM runs WHERE run_id = ANY(%s)", (ids,)
        ).fetchall()
    return {rid: s for rid, s in rows}


def _counters(run_id):
    with connect() as conn:
        return [
            v
            for (v,) in conn.execute(
                "SELECT value FROM counters WHERE name LIKE %s", (run_id + ":%",)
            ).fetchall()
        ]


def _terminate(workers):
    for w in workers:
        w.terminate()
    for w in workers:
        try:
            w.wait(timeout=5)
        except subprocess.TimeoutExpired:
            w.kill()


def run_fleet(n_workers=3, n_jobs=15, timeout=60.0):
    ids = _enqueue_jobs(n_jobs)
    workers = [
        subprocess.Popen([sys.executable, WORKER, f"w{i}"], cwd=REPO_ROOT)
        for i in range(n_workers)
    ]
    try:
        start = time.time()
        while time.time() - start < timeout:
            status = _statuses(ids)
            if all(status.get(r) == "completed" for r in ids):
                break
            time.sleep(0.3)
    finally:
        _terminate(workers)

    status = _statuses(ids)
    incomplete = [r for r in ids if status.get(r) != "completed"]
    bad = {r: _counters(r) for r in ids if sorted(_counters(r)) != [1] * STEPS}
    return {"jobs": n_jobs, "workers": n_workers, "incomplete": incomplete, "bad": bad}


def main():
    n_workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    n_jobs = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    s = run_fleet(n_workers, n_jobs)
    if not s["incomplete"] and not s["bad"]:
        print(f"fleet: {s['workers']} workers drained {s['jobs']} jobs, every counter exactly 1.")
    else:
        print(f"fleet FAILED: incomplete={len(s['incomplete'])}, bad_counters={s['bad']}")


if __name__ == "__main__":
    main()
