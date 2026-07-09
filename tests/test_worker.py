"""Test the worker loop: it drains its queue and fences its writes."""

from timefork.context import grant_approval
from timefork.events import connect, read_events
from timefork.mock_llm import MockLLM
from timefork.queue import enqueue_run
from timefork.worker import run_worker

AGENT = "test_worker_agent"


def bump(conn, name):
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return {"value": row[0]}


async def agent(ctx):
    for i in range(2):
        await ctx.side_effect(lambda conn, i=i: bump(conn, f"{ctx.run_id}:{i}"))


def test_worker_runs_each_queued_run_once_with_fenced_events():
    with connect() as conn:
        ids = [enqueue_run(conn, AGENT, {}) for _ in range(3)]

    run_worker("w1", AGENT, agent, MockLLM)

    with connect() as conn:
        for run_id in ids:
            status = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()[0]
            assert status == "completed"

            counts = [
                v
                for (v,) in conn.execute(
                    "SELECT value FROM counters WHERE name LIKE %s", (run_id + ":%",)
                ).fetchall()
            ]
            assert sorted(counts) == [1, 1]  # both side effects fired exactly once

            # Every event was fenced with the worker's (non-zero) lease token.
            tokens = [
                t
                for (t,) in conn.execute(
                    "SELECT lease_token FROM events WHERE run_id = %s", (run_id,)
                ).fetchall()
            ]
            assert tokens and all(t > 0 for t in tokens)
            assert len(read_events(conn, run_id)) == 4  # 2 effects x (intent + completion)


def _status(run_id):
    with connect() as conn:
        return conn.execute(
            "SELECT status FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()[0]


async def gated_agent(ctx):
    if await ctx.approval("go?"):
        await ctx.side_effect(lambda conn, rid=ctx.run_id: bump(conn, f"{rid}:paid"))


def test_worker_parks_a_paused_run_and_resumes_it_after_approval():
    with connect() as conn:
        run_id = enqueue_run(conn, "test_gated_agent", {})

    # First pass: the agent hits the gate; the worker parks it and survives.
    run_worker("w1", "test_gated_agent", gated_agent, MockLLM)
    assert _status(run_id) == "paused"

    # A human approves; the next pass resumes from the diary and finishes.
    with connect() as conn:
        grant_approval(conn, run_id, approved=True)
    run_worker("w2", "test_gated_agent", gated_agent, MockLLM)
    assert _status(run_id) == "completed"
    with connect() as conn:
        paid = conn.execute(
            "SELECT value FROM counters WHERE name = %s", (f"{run_id}:paid",)
        ).fetchone()[0]
    assert paid == 1


async def exploding_agent(ctx):
    raise RuntimeError("boom")


def test_a_failing_run_is_marked_failed_and_does_not_kill_the_worker():
    with connect() as conn:
        bad = enqueue_run(conn, "test_flaky_agent", {})

    # The worker must survive the exception and return normally when drained.
    run_worker("w1", "test_flaky_agent", exploding_agent, MockLLM)
    assert _status(bad) == "failed"  # not requeued into a forever-retry loop
