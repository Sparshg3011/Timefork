"""Run a fleet of worker processes, optionally under chaos.

  python harness/fleet.py [workers] [jobs]   # happy path: drain the queue
  python harness/fleet.py chaos [workers] [jobs]   # kill a random worker repeatedly
  python harness/fleet.py zombie             # freeze a worker, reassign, thaw it

The guarantee across all of it: every job completes and every counter is exactly
1, no matter who dies -- and a thawed zombie's writes are fenced out.
"""

import os
import random
import signal
import subprocess
import sys
import threading
import time

from timefork.events import connect
from timefork.queue import enqueue_run, read_lease_token, sweep_expired

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER = os.path.join(REPO_ROOT, "harness", "worker.py")
AGENT = "fleet_job"
STEPS = 3  # side effects (counters) per job; matches harness/worker.py


def _enqueue_jobs(n):
    with connect() as conn:
        return [enqueue_run(conn, AGENT, {}) for _ in range(n)]


def _spawn(worker_id, latency=None):
    env = dict(os.environ)
    if latency is not None:
        env["FLEET_LATENCY"] = str(latency)
    return subprocess.Popen([sys.executable, WORKER, worker_id], cwd=REPO_ROOT, env=env)


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


def _all_done(ids):
    status = _statuses(ids)
    return all(status.get(r) == "completed" for r in ids)


def _check(ids):
    status = _statuses(ids)
    incomplete = [r for r in ids if status.get(r) != "completed"]
    bad = {r: _counters(r) for r in ids if sorted(_counters(r)) != [1] * STEPS}
    return incomplete, bad


def _terminate(workers):
    for w in workers:
        w.terminate()
    for w in workers:
        try:
            w.wait(timeout=5)
        except subprocess.TimeoutExpired:
            w.kill()


def _wait_status(run_id, target, timeout):
    start = time.time()
    while time.time() - start < timeout:
        with connect() as conn:
            s = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()[0]
        if s == target:
            return True
        time.sleep(0.1)
    return False


def run_fleet(n_workers=3, n_jobs=15, timeout=60.0):
    ids = _enqueue_jobs(n_jobs)
    workers = [_spawn(f"w{i}") for i in range(n_workers)]
    try:
        start = time.time()
        while time.time() - start < timeout and not _all_done(ids):
            time.sleep(0.3)
    finally:
        _terminate(workers)
    incomplete, bad = _check(ids)
    return {"jobs": n_jobs, "workers": n_workers, "incomplete": incomplete, "bad": bad}


def run_chaos(n_workers=4, n_jobs=40, kill_every=1.5, timeout=150.0, latency=0.2):
    # A small per-step latency so a kill actually lands mid-job -- otherwise the
    # queue can drain before the first SIGKILL and the certificate proves little.
    ids = _enqueue_jobs(n_jobs)
    num = [0]

    def fresh():
        num[0] += 1
        return _spawn(f"cw{num[0]}", latency=latency)

    workers = [fresh() for _ in range(n_workers)]
    stop = threading.Event()

    def sweeper():  # the orchestrator: requeue any dead worker's job
        with connect() as conn:
            while not stop.wait(1.0):
                sweep_expired(conn)

    threading.Thread(target=sweeper, daemon=True).start()

    rng = random.Random()
    kills = 0
    start = time.time()
    next_kill = start + kill_every
    try:
        while time.time() - start < timeout and not _all_done(ids):
            if time.time() >= next_kill:
                i = rng.randrange(len(workers))
                workers[i].kill()  # SIGKILL a random worker mid-job
                workers[i].wait()
                workers[i] = fresh()  # keep the fleet full
                kills += 1
                next_kill = time.time() + kill_every
            time.sleep(0.2)
    finally:
        stop.set()
        _terminate(workers)
    incomplete, bad = _check(ids)
    return {"jobs": n_jobs, "workers": n_workers, "kills": kills,
            "incomplete": incomplete, "bad": bad}


def _events_with_token(run_id, token):
    with connect() as conn:
        return conn.execute(
            "SELECT count(*) FROM events WHERE run_id = %s AND lease_token = %s",
            (run_id, token),
        ).fetchone()[0]


def run_zombie():
    with connect() as conn:
        run_id = enqueue_run(conn, AGENT, {})

    # Worker A claims it; a long step latency lets us freeze it mid-run.
    a = _spawn("zombieA", latency=0.4)
    _wait_status(run_id, "running", timeout=10)
    with connect() as conn:
        token_a = read_lease_token(conn, run_id)
    time.sleep(0.15)               # let A get into its first (slow) step
    a.send_signal(signal.SIGSTOP)  # FREEZE A -- its heartbeat stops too
    stale_before = _events_with_token(run_id, token_a)  # A's writes so far

    # A's lease lapses; sweep requeues; worker B reclaims and finishes.
    time.sleep(4.0)
    with connect() as conn:
        sweep_expired(conn)
    b = _spawn("zombieB", latency=0.0)
    _wait_status(run_id, "completed", timeout=20)
    with connect() as conn:
        token_b = read_lease_token(conn, run_id)
    after_b = sorted(_counters(run_id))

    # Thaw A -- it resumes, tries to write with its stale token, gets fenced.
    a.send_signal(signal.SIGCONT)
    time.sleep(2.0)
    after_thaw = sorted(_counters(run_id))
    stale_after = _events_with_token(run_id, token_a)

    _terminate([a, b])
    return {"token_A": token_a, "token_B": token_b, "after_B": after_b,
            "after_thaw": after_thaw,
            "stale_events_before": stale_before, "stale_events_after": stale_after,
            "completed": _statuses([run_id]).get(run_id) == "completed"}


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "chaos":
        nw = int(sys.argv[2]) if len(sys.argv) > 2 else 4
        nj = int(sys.argv[3]) if len(sys.argv) > 3 else 40
        s = run_chaos(nw, nj)
        ok = not s["incomplete"] and not s["bad"]
        print(f"chaos: {s['jobs']} jobs, {s['workers']} workers, {s['kills']} random "
              f"SIGKILLs -> {'ALL completed, every counter exactly 1' if ok else 'FAILED ' + str(s)}")
    elif arg == "zombie":
        z = run_zombie()
        ok = (z["completed"] and z["after_B"] == z["after_thaw"] == [1] * STEPS
              and z["stale_events_after"] == z["stale_events_before"])
        print(f"zombie: A held token {z['token_A']}, B reclaimed with token {z['token_B']}")
        print(f"  counters after B finished: {z['after_B']}")
        print(f"  counters after thawing A:  {z['after_thaw']}")
        print(f"  events written with A's stale token after the thaw: "
              f"{z['stale_events_after'] - z['stale_events_before']}")
        print(f"  -> {'zombie fenced out, appended nothing, counters stayed exactly 1' if ok else 'FAILED ' + str(z)}")
    else:
        nw = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        nj = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        s = run_fleet(nw, nj)
        ok = not s["incomplete"] and not s["bad"]
        print(f"fleet: {s['workers']} workers drained {s['jobs']} jobs -> "
              f"{'every counter exactly 1' if ok else 'FAILED ' + str(s)}")


if __name__ == "__main__":
    main()
