"""A worker process for the fleet. Claims and runs jobs until killed.

The job mixes LLM calls (replayed on resume) and side effects (exactly once),
with a little latency so a kill can land mid-run. Launched (and killed) by the
fleet harness.

  python harness/worker.py <worker_id>
"""

import os
import sys

from timefork.mock_llm import MockLLM
from timefork.worker import run_worker

AGENT = "fleet_job"
LATENCY = float(os.environ.get("FLEET_LATENCY", "0.05"))  # per-step delay


def bump(conn, name):
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return {"value": row[0]}


async def job(ctx):
    await ctx.llm("step 1")
    await ctx.side_effect(lambda c: bump(c, f"{ctx.run_id}:1"))
    await ctx.llm("step 2")
    await ctx.side_effect(lambda c: bump(c, f"{ctx.run_id}:2"))
    await ctx.side_effect(lambda c: bump(c, f"{ctx.run_id}:3"))


def main():
    worker_id = sys.argv[1]
    run_worker(
        worker_id,
        AGENT,
        job,
        lambda: MockLLM(latency_s=LATENCY),
        lease_seconds=3,
        heartbeat_seconds=1.0,
        run_forever=True,
    )


if __name__ == "__main__":
    main()
