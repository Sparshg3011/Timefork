"""Several workers drain one queue: each job runs once, every counter is 1."""

import threading

from timefork.events import connect
from timefork.mock_llm import MockLLM
from timefork.queue import enqueue_run
from timefork.worker import run_worker

AGENT = "test_fleet_agent"


def bump(conn, name):
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return {"value": row[0]}


async def job(ctx):
    await ctx.side_effect(lambda c: bump(c, f"{ctx.run_id}:1"))
    await ctx.side_effect(lambda c: bump(c, f"{ctx.run_id}:2"))


def test_many_workers_drain_one_queue_each_job_once():
    with connect() as conn:
        ids = [enqueue_run(conn, AGENT, {}) for _ in range(9)]

    def worker(name):
        run_worker(name, AGENT, job, MockLLM)  # drain mode

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

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
