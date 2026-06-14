"""The divergence guard: replay refuses to lie.

Records a run, then resumes it with *changed* code -- a different prompt at the
same step. Instead of silently returning the old answer, replay raises
ReplayDivergenceError with a diff. This is the "never silent corruption" rule.

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/04_divergence.py
"""

import asyncio

from timefork.context import Context, ReplayDivergenceError
from timefork.events import connect, create_run, read_events
from timefork.mock_llm import MockLLM


async def main() -> None:
    with connect() as conn:
        run_id = create_run(conn, "divergent_agent", {})

        # First life records one step asking "what is 2 + 2?".
        await Context(conn, run_id, MockLLM(seed=1)).llm("what is 2 + 2?")
        print(f"diary recorded prompt: {read_events(conn, run_id)[0].payload['prompt']!r}")

        # Second life: the code changed -- it asks a different question at the
        # same step. Replay catches the mismatch instead of trusting position.
        ctx = Context(conn, run_id, MockLLM(seed=1))
        try:
            await ctx.llm("what is the capital of France?")
            print("no divergence (unexpected!)")
        except ReplayDivergenceError as exc:
            print(f"caught loudly: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
