"""Tests for the replay context: record once, replay for free, resume and continue."""

import asyncio

import pytest

from timefork.context import Context
from timefork.events import connect, create_run, read_events
from timefork.mock_llm import MockLLM


@pytest.fixture
def conn():
    with connect() as c:
        yield c


async def three_step_agent(ctx):
    await ctx.llm("one")
    await ctx.llm("two")
    return await ctx.llm("three")


def test_first_life_records_each_call(conn):
    run_id = create_run(conn, "agent", {})
    brain = MockLLM(seed=1)

    out = asyncio.run(three_step_agent(Context(conn, run_id, brain)))

    assert brain.calls == 3  # the bill: three real calls
    events = read_events(conn, run_id)
    assert [e.seq for e in events] == [1, 2, 3]
    assert all(e.type == "LLM_CALLED" for e in events)
    assert events[2].payload["response"] == out  # last answer is the output


def test_second_life_replays_with_no_new_calls(conn):
    run_id = create_run(conn, "agent", {})
    brain1 = MockLLM(seed=1)
    out1 = asyncio.run(three_step_agent(Context(conn, run_id, brain1)))

    # Resume with a different, random brain. If replay works it is never called.
    brain2 = MockLLM()
    out2 = asyncio.run(three_step_agent(Context(conn, run_id, brain2)))

    assert brain2.calls == 0                     # nothing re-executed
    assert out2 == out1                          # identical output, from the diary
    assert len(read_events(conn, run_id)) == 3   # no duplicate events


def test_partial_replay_then_continue(conn):
    # First life crashes after two steps: only two events recorded.
    run_id = create_run(conn, "agent", {})
    brain1 = MockLLM(seed=1)
    ctx1 = Context(conn, run_id, brain1)
    asyncio.run(ctx1.llm("one"))
    asyncio.run(ctx1.llm("two"))
    assert brain1.calls == 2
    assert len(read_events(conn, run_id)) == 2

    # Second life runs the full three-step agent: two replay, one goes live.
    brain2 = MockLLM(seed=1)
    asyncio.run(three_step_agent(Context(conn, run_id, brain2)))

    assert brain2.calls == 1                      # only the new step was paid for
    assert len(read_events(conn, run_id)) == 3    # diary completed to three
