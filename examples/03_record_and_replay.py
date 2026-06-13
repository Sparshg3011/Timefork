"""Record and replay: an agent's second life costs nothing.

First life: each ctx.llm() call hits the (mock) model and is written to the
diary. Second life (a simulated crash + resume): a brand-new Context -- handed
a different, random model -- reads the recorded answers back and never calls
the model at all.

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/03_record_and_replay.py
"""

import asyncio

from timefork.context import Context
from timefork.events import connect, create_run, read_events, set_run_status
from timefork.mock_llm import MockLLM


async def chat_agent(ctx) -> str:
    """A tiny three-step agent. Its orchestration is deterministic -- the same
    three calls, in the same order, every life -- which is what makes replay work."""
    await ctx.llm("what is durable execution?")
    await ctx.llm("give me an analogy")
    return await ctx.llm("summarize in one line")


async def main() -> None:
    with connect() as conn:
        run_id = create_run(conn, "chat_agent", {"topic": "durable execution"})
        print(f"run {run_id}\n")

        # First life -- records. The bill climbs as the model is really called.
        brain1 = MockLLM(seed=42)
        out1 = await chat_agent(Context(conn, run_id, brain1))
        print(f"first life:  bill = {brain1.calls}, diary = {len(read_events(conn, run_id))} events")
        print(f"             output = {out1!r}\n")

        # Second life -- a fresh process would rebuild its Context from the diary.
        # We hand it a different, RANDOM brain to prove it is never asked.
        brain2 = MockLLM()
        out2 = await chat_agent(Context(conn, run_id, brain2))
        print(f"second life: bill = {brain2.calls}, diary = {len(read_events(conn, run_id))} events")
        print(f"             output = {out2!r}\n")

        print(f"same output across lives:            {out1 == out2}")
        print(f"second life's brain was never called: {brain2.calls == 0}")

        set_run_status(conn, run_id, "completed")


if __name__ == "__main__":
    asyncio.run(main())
