"""Exactly-once side effects: the action runs once, even on replay.

The side effect here bumps a counter. We perform it, then 'resume' the run with
a fresh Context -- and the counter does NOT move, because replay reuses the
recorded completion instead of doing the effect again.

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/05_side_effect.py
"""

import asyncio

from timefork.context import Context
from timefork.events import connect, create_run, read_events
from timefork.mock_llm import MockLLM


def bump_counter(conn, name):
    """The 'real-world action' -- increment a counter, return the new value.
    Does NOT commit; the executor commits it atomically with the dedupe key."""
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return {"counter": name, "value": row[0]}


async def refund_agent(ctx, name):
    """One step: send the refund (bump the counter) exactly once."""
    return await ctx.side_effect(lambda conn: bump_counter(conn, name))


async def main():
    with connect() as conn:
        run_id = create_run(conn, "refund_agent", {})
        name = f"refunds-{run_id[:8]}"

        # First life: the effect runs.
        r1 = await refund_agent(Context(conn, run_id, MockLLM()), name)
        kinds = [e.type for e in read_events(conn, run_id)]
        print(f"first life:  result={r1}")
        print(f"             diary={kinds}")

        # Second life (resume): replay -- the effect must NOT run again.
        r2 = await refund_agent(Context(conn, run_id, MockLLM()), name)
        value = conn.execute(
            "SELECT value FROM counters WHERE name=%s", (name,)
        ).fetchone()[0]
        print(f"second life: result={r2}")
        print(f"counter '{name}' = {value}   (exactly once: {value == 1})")


if __name__ == "__main__":
    asyncio.run(main())
