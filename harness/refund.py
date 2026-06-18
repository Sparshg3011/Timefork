"""A side-effect agent and a runner that executes one life of it.

Each step performs a side effect -- bumping its own counter -- exactly once.
The crash harness launches this as a subprocess, kills it at a random point
around the effect, and relaunches it to resume. Exactly-once means every
counter ends at 1, no matter how many times it was killed.

  python harness/refund.py            # fresh run
  python harness/refund.py <run_id>   # resume
"""

import asyncio
import sys

from timefork.context import Context
from timefork.events import connect, create_run, read_events, set_run_status
from timefork.mock_llm import MockLLM

STEPS = 3


def bump(conn, name):
    """The side effect: increment a counter, return the new value. Does NOT
    commit -- the executor commits it atomically with the dedupe key."""
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return {"value": row[0]}


async def refund_agent(ctx, run_id):
    """STEPS side effects; each bumps its own counter exactly once."""
    for i in range(1, STEPS + 1):
        await ctx.side_effect(lambda conn, i=i: bump(conn, f"{run_id}:{i}"))


async def run_one_life(run_id):
    with connect() as conn:
        await refund_agent(Context(conn, run_id, MockLLM()), run_id)
        set_run_status(conn, run_id, "completed")


def main():
    if len(sys.argv) > 1:
        run_id = sys.argv[1]
    else:
        with connect() as conn:
            run_id = create_run(conn, "refund_agent", {"steps": STEPS})
    asyncio.run(run_one_life(run_id))
    with connect() as conn:
        n = len(read_events(conn, run_id))
    print(f"run {run_id}: {n} events")


if __name__ == "__main__":
    main()
