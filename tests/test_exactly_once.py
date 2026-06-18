"""Week 2 exit test, as a fast regression slice.

run_chaos asserts internally that every run's counters all end at 1; here we run
a handful so the suite guards exactly-once forever. The full certificate is
`python harness/refund_chaos.py 1000`.
"""

from harness.refund_chaos import run_chaos


def test_random_kills_keep_every_counter_at_one():
    stats = run_chaos(n_runs=5, seed=0)
    assert stats["runs"] == 5
    assert stats["steps"] >= 1
