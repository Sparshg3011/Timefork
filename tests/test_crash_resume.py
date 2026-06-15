"""The Week 1 exit test, as a fast regression slice.

run_chaos asserts internally that every run completes with exactly the baseline
events; here we run a handful of chaos runs so the suite guards that forever.
The full 100x certificate is `python harness/chaos.py`.
"""

from harness.chaos import run_chaos


def test_random_kills_always_recover_to_the_baseline():
    stats = run_chaos(n_runs=5, seed=0)
    assert stats["runs"] == 5
    assert stats["steps"] == 15
