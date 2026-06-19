"""A worker drains the queue: claim, run (fenced), complete, repeat.

We enqueue a few runs whose agent does side effects, then run one worker. It
claims each, runs it through a Context fenced by its lease token, and marks it
complete -- every side effect firing exactly once.

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/10_worker.py
"""

from timefork.events import connect
from timefork.mock_llm import MockLLM
from timefork.queue import enqueue_run
from timefork.worker import run_worker

AGENT = "side_effect_demo"


def bump(conn, name):
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return {"value": row[0]}


async def agent(ctx):
    for i in range(3):
        await ctx.side_effect(lambda conn, i=i: bump(conn, f"{ctx.run_id}:{i}"))


def main():
    with connect() as conn:
        ids = [enqueue_run(conn, AGENT, {}) for _ in range(5)]
    print(f"enqueued {len(ids)} runs")

    run_worker("worker-1", AGENT, agent, MockLLM)

    with connect() as conn:
        for run_id in ids:
            status = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()[0]
            counts = [
                v
                for (v,) in conn.execute(
                    "SELECT value FROM counters WHERE name LIKE %s", (run_id + ":%",)
                ).fetchall()
            ]
            print(f"  {run_id[:8]}: status={status}, counters={counts}")


if __name__ == "__main__":
    main()
