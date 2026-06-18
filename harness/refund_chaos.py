"""Week 2 exit test: kill a side-effect agent at random points, over and over,
and prove every counter ends at exactly 1 -- the effect fired exactly once.

  python harness/refund_chaos.py [n_runs]
"""

import os
import random
import subprocess
import sys

from timefork.events import connect, create_run

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(REPO_ROOT, "harness", "refund.py")
CRASH_POINTS = ["before_intent", "before_effect", "before_commit", "after_effect"]


def _launch(run_id, crash=None):
    env = dict(os.environ)
    env.pop("TIMEFORK_CRASH_AT", None)
    env.pop("TIMEFORK_CRASH_AT_SEQ", None)
    if crash is not None:
        env["TIMEFORK_CRASH_AT"] = crash[0]
        env["TIMEFORK_CRASH_AT_SEQ"] = str(crash[1])
    return subprocess.run(
        [sys.executable, RUNNER, run_id], env=env, cwd=REPO_ROOT, capture_output=True
    )


def _status(run_id):
    with connect() as conn:
        return conn.execute(
            "select status from runs where run_id=%s", (run_id,)
        ).fetchone()[0]


def _counters(run_id):
    with connect() as conn:
        rows = conn.execute(
            "select name, value from counters where name like %s", (run_id + ":%",)
        ).fetchall()
    return {name: value for name, value in rows}


def _fresh_run():
    with connect() as conn:
        return create_run(conn, "refund_chaos", {})


def run_chaos(n_runs=100, seed=0):
    rng = random.Random(seed)

    # Baseline: one clean run tells us how many counters a run should produce.
    base_id = _fresh_run()
    _launch(base_id)
    steps = len(_counters(base_id))
    assert _status(base_id) == "completed" and steps > 0
    max_seq = 2 * steps  # each side effect owns two events (intent + completion)

    total_kills = 0
    for n in range(n_runs):
        run_id = _fresh_run()
        for _ in range(60):
            if _status(run_id) == "completed":
                break
            crash = (rng.choice(CRASH_POINTS), rng.randint(1, max_seq))
            if _launch(run_id, crash).returncode != 0:
                total_kills += 1
        if _status(run_id) != "completed":
            _launch(run_id)

        counters = _counters(run_id)
        assert _status(run_id) == "completed", f"run {n}: not completed"
        assert len(counters) == steps, f"run {n}: {len(counters)} counters, want {steps}"
        assert all(v == 1 for v in counters.values()), f"run {n}: {counters}"

    return {"runs": n_runs, "steps": steps, "kills": total_kills}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    s = run_chaos(n_runs=n, seed=0)
    print(
        f"exactly-once certificate: {s['runs']}/{s['runs']} runs completed, all "
        f"{s['runs'] * s['steps']} counters exactly 1, after {s['kills']} random SIGKILLs."
    )


if __name__ == "__main__":
    main()
