"""Tests for lineage navigation and the timeline diff."""

import asyncio

from timefork.context import Context
from timefork.diff import diff_runs
from timefork.events import connect, create_run
from timefork.fork import children_of, fork_run, parent_of
from timefork.mock_llm import MockLLM


async def agent(ctx):
    for i in range(1, 6):
        await ctx.llm(f"[{ctx.config('style', 'neutral')}] step {i}")


def test_diff_of_a_fork_shows_shared_prefix_and_divergence():
    with connect() as conn:
        parent_id = create_run(conn, "agent", {})
        asyncio.run(agent(Context(conn, parent_id, MockLLM(seed=1))))
        child_id = fork_run(conn, parent_id, 3, {"style": "generous"})
        asyncio.run(agent(Context(conn, child_id, MockLLM(seed=1))))

        d = diff_runs(conn, parent_id, child_id)
        assert d["shared"] == 3        # steps 1-3 are identical
        assert d["diverge_at"] == 4    # the patch event splits them at seq 4


def test_diff_of_a_run_with_itself_is_all_shared():
    with connect() as conn:
        run_id = create_run(conn, "agent", {})
        asyncio.run(agent(Context(conn, run_id, MockLLM(seed=1))))

        d = diff_runs(conn, run_id, run_id)
        assert d["diverge_at"] is None
        assert d["shared"] == 5


def test_lineage_links_parent_and_child():
    with connect() as conn:
        parent_id = create_run(conn, "agent", {})
        asyncio.run(agent(Context(conn, parent_id, MockLLM(seed=1))))
        child_id = fork_run(conn, parent_id, 2, {"style": "x"})

        assert parent_of(conn, child_id) == (parent_id, 2)
        assert (child_id, 2) in children_of(conn, parent_id)
