"""A 15-step agent plus a runner that executes one life of it.

Unlike the earlier examples, this runs as its OWN process -- which is the point:
the crash harness launches it as a subprocess, kills it at a random step, and
relaunches it on the same run_id to resume from the diary. Run by hand, one
clean life records exactly 15 events.

  python harness/agent.py            # fresh run
  python harness/agent.py <run_id>   # resume an existing run
"""

import asyncio
import sys

from timefork.context import Context
from timefork.events import connect, create_run, read_events, set_run_status
from timefork.mock_llm import MockLLM

STEPS = 15
# Fixed seed: every life of a run produces the same answers, so a crashed-and-
# resumed run is identical to a never-crashed one (the baseline the harness checks).
SEED = 7


async def fifteen_step_agent(ctx) -> str:
    """Fifteen LLM calls in a fixed order. Deterministic orchestration -- the
    same calls in the same order every life -- is what lets replay line up."""
    answer = ""
    for i in range(1, STEPS + 1):
        answer = await ctx.llm(f"step {i}")
    return answer


async def run_one_life(run_id: str) -> tuple[str, int, int]:
    """Run (or resume) one life against the diary and mark the run completed.
    Returns the final answer, the model calls made THIS life, and the diary size."""
    with connect() as conn:
        brain = MockLLM(seed=SEED)
        output = await fifteen_step_agent(Context(conn, run_id, brain))
        set_run_status(conn, run_id, "completed")
        return output, brain.calls, len(read_events(conn, run_id))


def main() -> None:
    # With a run_id: resume it. Without: create a fresh run and live it.
    if len(sys.argv) > 1:
        run_id = sys.argv[1]
    else:
        with connect() as conn:
            run_id = create_run(conn, "fifteen_step_agent", {"steps": STEPS})

    output, calls, events = asyncio.run(run_one_life(run_id))
    print(f"run {run_id}")
    print(f"  this life made {calls} model calls; diary holds {events} events")
    print(f"  output: {output!r}")


if __name__ == "__main__":
    main()
