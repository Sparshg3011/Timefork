"""Week 5 showcase: a refund agent on a real model, with a durable human pause.

The agent consults the model for a recommendation, then STOPS at an approval
gate before any money moves. The process can die here -- the request is in the
diary. A human decides (out of band), and the run resumes on a fresh Context,
replaying the model call for free and issuing the refund exactly once.

Runs on the real model if a key is present, else on the mock brain, so anyone
can reproduce it from a clean clone:

    pip install -e ".[showcase]"            # only needed for the real model
    export ANTHROPIC_API_KEY=sk-ant-...     # optional
    python examples/13_showcase.py
"""

import asyncio
import os

from timefork.context import Context, PausedForApproval, grant_approval
from timefork.events import connect, create_run, read_events, set_run_status
from timefork.mock_llm import MockLLM

# Optionally load ANTHROPIC_API_KEY from a local .env (gitignored), so the real
# model can run without exporting the key by hand. No-op without python-dotenv.
try:
    from dotenv import load_dotenv

    # override=True so a key in .env wins over a stale/empty shell variable.
    load_dotenv(override=True)
except ImportError:
    pass

ORDER = {"id": "A-4471", "amount": 49.99, "reason": "item arrived damaged"}


def make_brain():
    """The real Claude brain if a key is set, else the mock (so the demo always runs)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from timefork.llm import ClaudeLLM

            return ClaudeLLM(), "real model (claude-opus-4-8)"
        except ImportError:
            pass
    script = [
        f"Recommend approving: the customer reports '{ORDER['reason']}', and "
        f"${ORDER['amount']} is within the standard refund window."
    ]
    return MockLLM(script=script), "mock brain (set ANTHROPIC_API_KEY for the real model)"


def issue_refund(conn, name, amount):
    """The side effect: pay the refund once (a counter stands in for the money)."""
    conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1",
        (name,),
    )
    return {"amount": amount}


async def refund_agent(ctx, run_id):
    rec = await ctx.llm(
        f"Order {ORDER['id']}: customer requests ${ORDER['amount']} back. "
        f"Reason: {ORDER['reason']}. Should we refund?"
    )
    approved = await ctx.approval(f"{rec}\n\nApprove refund of ${ORDER['amount']}?")
    if approved:
        await ctx.side_effect(
            lambda conn: issue_refund(conn, f"{run_id}:refund", ORDER["amount"])
        )
    return approved


def run_life(run_id, brain):
    """One process-life: complete, or pause at the gate."""
    with connect() as conn:
        try:
            approved = asyncio.run(refund_agent(Context(conn, run_id, brain), run_id))
            set_run_status(conn, run_id, "completed")
            return approved
        except PausedForApproval as pause:
            set_run_status(conn, run_id, "paused")
            return pause


def main():
    brain, label = make_brain()
    print(f"brain: {label}\n")

    with connect() as conn:
        run_id = create_run(conn, "refund_showcase", ORDER)

    print("life 1 -- consult the model, then reach the approval gate:")
    paused = run_life(run_id, brain)
    print(f"  PAUSED. the diary durably holds the request:\n    {paused.question}\n")
    print("  (the process could die now and lose nothing.)\n")

    print("a human reviews and approves (out of band -- `timefork approve RUN`):")
    with connect() as conn:
        grant_approval(conn, run_id, approved=True)
    print("  APPROVAL recorded.\n")

    print("life 2 -- a FRESH process resumes from the diary:")
    approved = run_life(run_id, brain)
    with connect() as conn:
        events = read_events(conn, run_id)
        paid = conn.execute(
            "SELECT value FROM counters WHERE name = %s", (f"{run_id}:refund",)
        ).fetchone()[0]
    print(f"  approved={approved}, refund paid {paid} time(s), bill={brain.calls} model call(s)")
    print(f"  diary: {[e.type for e in events]}\n")
    print("  -> the model was consulted once; resume replayed it for free; "
          "the refund fired exactly once.")


if __name__ == "__main__":
    main()
