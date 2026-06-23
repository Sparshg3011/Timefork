"""Forking: rewind a run, change the prompt, branch a new timeline -- for free.

We run a 5-step parent, then fork it at step 3 with a different 'style'. The
child REPLAYS steps 1-3 (zero model calls) and only pays for the 2 new steps,
producing a different ending. A fork is a fresh experiment, not a proof of what
the parent would have done.

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/11_fork.py
"""

import asyncio

from timefork.context import Context
from timefork.events import connect, create_run, read_events
from timefork.fork import fork_run
from timefork.mock_llm import MockLLM


async def agent(ctx):
    out = ""
    for i in range(1, 6):
        style = ctx.config("style", "neutral")
        out = await ctx.llm(f"[{style}] step {i}")
    return out


async def main():
    with connect() as conn:
        # Parent: 5 steps, all "neutral".
        parent_id = create_run(conn, "agent", {})
        parent_brain = MockLLM(seed=1)
        parent_out = await agent(Context(conn, parent_id, parent_brain))
        print(f"parent: {parent_brain.calls} model calls, {len(read_events(conn, parent_id))} events")
        print(f"        ends: {parent_out!r}\n")

        # Fork at step 3, switch the style to "generous".
        child_id = fork_run(conn, parent_id, 3, {"style": "generous"})
        print(f"forked at step 3 -> child {child_id[:8]} (copied steps 1-3 + a patch)\n")

        # Run the child: it replays 1-3 for free, pays only for the 2 new steps.
        child_brain = MockLLM(seed=1)
        child_out = await agent(Context(conn, child_id, child_brain))
        print(f"child:  {child_brain.calls} model calls (steps 1-3 replayed FREE)")
        print(f"        ends: {child_out!r}\n")
        print(f"same prefix, different ending: {parent_out != child_out}")


if __name__ == "__main__":
    asyncio.run(main())
