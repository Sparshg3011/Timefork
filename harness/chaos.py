"""The Week 1 exit test: kill a 15-step agent at random points, over and over,
and prove every run still finishes with exactly the same events as a
never-crashed baseline.

  python harness/chaos.py [n_runs]
"""

import os
import random
import subprocess
import sys

from timefork.events import connect, create_run, read_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(REPO_ROOT, "harness", "agent.py")
CRASH_POINTS = ["before_call", "before_append", "after_append"]


def _launch(run_id: str, crash=None) -> subprocess.CompletedProcess:
    """Run one life in a fresh process. `crash` is (point, seq) or None."""
    env = dict(os.environ)
    env.pop("TIMEFORK_CRASH_AT", None)
    env.pop("TIMEFORK_CRASH_AT_SEQ", None)
    if crash is not None:
        env["TIMEFORK_CRASH_AT"] = crash[0]
        env["TIMEFORK_CRASH_AT_SEQ"] = str(crash[1])
    return subprocess.run(
        [sys.executable, RUNNER, run_id], env=env, cwd=REPO_ROOT, capture_output=True
    )


def _status(run_id: str) -> str:
    with connect() as conn:
        return conn.execute(
            "select status from runs where run_id=%s", (run_id,)
        ).fetchone()[0]


def _events(run_id: str):
    """Return (seqs, responses) recorded for a run, in order."""
    with connect() as conn:
        events = read_events(conn, run_id)
    return [e.seq for e in events], [e.payload["response"] for e in events]


def _fresh_run() -> str:
    with connect() as conn:
        return create_run(conn, "chaos", {})


def run_chaos(n_runs: int = 100, seed: int = 0) -> dict:
    rng = random.Random(seed)

    # Baseline: one clean life -- the never-crashed ground truth.
    base_id = _fresh_run()
    _launch(base_id)
    seqs, baseline = _events(base_id)
    assert _status(base_id) == "completed" and seqs == list(range(1, len(seqs) + 1))
    steps = len(baseline)

    total_kills = 0
    for n in range(n_runs):
        run_id = _fresh_run()
        # Keep killing at random points until a launch slips through to the end.
        for _ in range(50):
            if _status(run_id) == "completed":
                break
            crash = (rng.choice(CRASH_POINTS), rng.randint(1, steps))
            if _launch(run_id, crash).returncode != 0:
                total_kills += 1
        if _status(run_id) != "completed":
            _launch(run_id)  # safety net: one guaranteed clean finish

        seqs, responses = _events(run_id)
        assert _status(run_id) == "completed", f"run {n}: did not complete"
        assert seqs == list(range(1, steps + 1)), f"run {n}: seqs {seqs}"
        assert responses == baseline, f"run {n}: diverged from the baseline"

    return {"runs": n_runs, "steps": steps, "kills": total_kills}


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    s = run_chaos(n_runs=n, seed=0)
    print(
        f"chaos certificate: {s['runs']}/{s['runs']} runs completed, every one with "
        f"exactly {s['steps']} LLM events identical to the never-crashed baseline, "
        f"after {s['kills']} random SIGKILLs."
    )


if __name__ == "__main__":
    main()
