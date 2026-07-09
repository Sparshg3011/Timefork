"""Tests for forking: copy the prefix, replay it free, diverge via the patch."""

import asyncio

import pytest

from timefork.context import Context
from timefork.events import connect, create_run, read_events
from timefork.fork import InvalidForkPointError, fork_run
from timefork.mock_llm import MockLLM


async def five_step_agent(ctx):
    out = ""
    for i in range(1, 6):
        out = await ctx.llm(f"[{ctx.config('style', 'neutral')}] step {i}")
    return out


def test_fork_copies_prefix_and_records_lineage():
    with connect() as conn:
        parent_id = create_run(conn, "agent", {})
        asyncio.run(five_step_agent(Context(conn, parent_id, MockLLM(seed=1))))

        child_id = fork_run(conn, parent_id, 3, {"style": "generous"})

        events = read_events(conn, child_id)
        assert [e.seq for e in events] == [1, 2, 3, 4]
        assert [e.type for e in events] == [
            "LLM_CALLED", "LLM_CALLED", "LLM_CALLED", "PATCH_APPLIED",
        ]
        lineage = conn.execute(
            "SELECT parent_run_id, fork_seq FROM runs WHERE run_id = %s", (child_id,)
        ).fetchone()
        assert lineage == (parent_id, 3)


def test_fork_replays_the_prefix_for_free_and_diverges():
    with connect() as conn:
        parent_id = create_run(conn, "agent", {})
        parent_out = asyncio.run(five_step_agent(Context(conn, parent_id, MockLLM(seed=1))))

        child_id = fork_run(conn, parent_id, 3, {"style": "generous"})
        child_brain = MockLLM(seed=1)
        child_out = asyncio.run(five_step_agent(Context(conn, child_id, child_brain)))

        assert child_brain.calls == 2          # steps 1-3 replayed free; only 4-5 paid
        assert child_out != parent_out         # the patch changed the ending
        assert len(read_events(conn, child_id)) == 6  # 3 copied + patch + 2 new


async def llm_then_effect_agent(ctx):
    # Diary: 1 LLM_CALLED, 2 TOOL_INTENT, 3 TOOL_COMPLETED.
    await ctx.llm("decide")
    await ctx.side_effect(lambda conn: {"ok": True})


def test_fork_rejects_invalid_points():
    with connect() as conn:
        parent_id = create_run(conn, "agent", {})
        asyncio.run(llm_then_effect_agent(Context(conn, parent_id, MockLLM(seed=1))))

        with pytest.raises(InvalidForkPointError):
            fork_run(conn, parent_id, 0, {})   # before the first step
        with pytest.raises(InvalidForkPointError):
            fork_run(conn, parent_id, 4, {})   # past the end of the diary
        with pytest.raises(InvalidForkPointError):
            fork_run(conn, parent_id, 2, {})   # splits the intent from its completion

        # Clean boundaries still fork fine.
        assert fork_run(conn, parent_id, 1, {})
        assert fork_run(conn, parent_id, 3, {})
