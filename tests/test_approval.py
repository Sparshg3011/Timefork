"""The durable human-approval pause, proven with the mock brain (no API key).

The real-model client (timefork/llm.py) is a thin swap for the mock and is not
exercised here -- the mechanism that matters (pause -> grant -> resume) is
deterministic and needs no paid API.
"""

import asyncio

import pytest

from timefork.context import (
    Context,
    NotAwaitingApprovalError,
    PausedForApproval,
    ReplayDivergenceError,
    grant_approval,
)
from timefork.events import connect, create_run, read_events, set_run_status
from timefork.mock_llm import MockLLM


def bump(conn, name):
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return {"value": row[0]}


async def refund_agent(ctx, run_id):
    """Consult the model, gate on a human, then refund exactly once."""
    rec = await ctx.llm("recommend a refund decision")
    approved = await ctx.approval(f"{rec} -- approve?")
    if approved:
        await ctx.side_effect(lambda conn: bump(conn, f"{run_id}:refund"))
    return approved


def run_life(run_id, brain):
    """One process-life. Returns the agent's result, or None if it paused."""
    with connect() as conn:
        try:
            result = asyncio.run(refund_agent(Context(conn, run_id, brain), run_id))
            set_run_status(conn, run_id, "completed")
            return result
        except PausedForApproval:
            set_run_status(conn, run_id, "paused")
            return None


def _types(run_id):
    with connect() as conn:
        return [e.type for e in read_events(conn, run_id)]


def _status(run_id):
    with connect() as conn:
        return conn.execute(
            "SELECT status FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()[0]


def test_approval_pause_is_durable_then_resumes():
    with connect() as conn:
        run_id = create_run(conn, "refund", {})

    # First life: the model is consulted once, then the agent pauses at the gate.
    assert run_life(run_id, MockLLM(seed=1)) is None
    assert _types(run_id) == ["LLM_CALLED", "APPROVAL_REQUESTED"]
    assert _status(run_id) == "paused"

    # The wait is durable: a fresh life (a different brain) re-reads the diary
    # and pauses again, adding nothing -- no second model call, no duplicate ask.
    assert run_life(run_id, MockLLM(seed=2)) is None
    assert _types(run_id) == ["LLM_CALLED", "APPROVAL_REQUESTED"]

    # A human approves out of band.
    with connect() as conn:
        grant_approval(conn, run_id, approved=True)

    # Resume: replays the model call + the decision, then refunds exactly once.
    assert run_life(run_id, MockLLM(seed=3)) is True
    assert _types(run_id) == [
        "LLM_CALLED", "APPROVAL_REQUESTED", "APPROVAL", "TOOL_INTENT", "TOOL_COMPLETED"
    ]
    assert _status(run_id) == "completed"
    with connect() as conn:
        paid = conn.execute(
            "SELECT value FROM counters WHERE name = %s", (f"{run_id}:refund",)
        ).fetchone()[0]
    assert paid == 1


def test_denied_approval_skips_the_side_effect():
    with connect() as conn:
        run_id = create_run(conn, "refund", {})

    assert run_life(run_id, MockLLM(seed=1)) is None
    with connect() as conn:
        grant_approval(conn, run_id, approved=False)

    assert run_life(run_id, MockLLM(seed=2)) is False
    assert _types(run_id) == ["LLM_CALLED", "APPROVAL_REQUESTED", "APPROVAL"]
    with connect() as conn:
        refunded = conn.execute(
            "SELECT value FROM counters WHERE name = %s", (f"{run_id}:refund",)
        ).fetchone()
    assert refunded is None  # no money moved


def test_a_changed_question_fails_loudly_instead_of_reusing_the_answer():
    async def ask(ctx, question):
        return await ctx.approval(question)

    with connect() as conn:
        run_id = create_run(conn, "refund", {})
        with pytest.raises(PausedForApproval):
            asyncio.run(ask(Context(conn, run_id, MockLLM(seed=1)), "refund $12?"))
        grant_approval(conn, run_id, approved=True)

        # The human said yes to $12 -- code asking $1200 must not inherit it.
        with pytest.raises(ReplayDivergenceError):
            asyncio.run(ask(Context(conn, run_id, MockLLM(seed=1)), "refund $1200?"))


def test_double_approval_is_rejected():
    async def ask(ctx):
        return await ctx.approval("go?")

    with connect() as conn:
        run_id = create_run(conn, "refund", {})
        with pytest.raises(PausedForApproval):
            asyncio.run(ask(Context(conn, run_id, MockLLM(seed=1))))

        grant_approval(conn, run_id, approved=True)
        with pytest.raises(NotAwaitingApprovalError):
            grant_approval(conn, run_id, approved=True)  # the second click
        assert _types(run_id) == ["APPROVAL_REQUESTED", "APPROVAL"]  # not corrupted
