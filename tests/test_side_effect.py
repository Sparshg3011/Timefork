"""Tests for exactly-once side effects."""

import asyncio

import pytest

from timefork.context import Context
from timefork.events import append_event, connect, create_run, read_events
from timefork.mock_llm import MockLLM


def bump(conn, name):
    row = conn.execute(
        "INSERT INTO counters (name, value) VALUES (%s, 1) "
        "ON CONFLICT (name) DO UPDATE SET value = counters.value + 1 RETURNING value",
        (name,),
    ).fetchone()
    return {"value": row[0]}


@pytest.fixture
def conn():
    with connect() as c:
        yield c


def test_side_effect_runs_once_then_replays(conn):
    run_id = create_run(conn, "agent", {})
    name = f"c-{run_id[:8]}"

    r1 = asyncio.run(Context(conn, run_id, MockLLM()).side_effect(lambda c: bump(c, name)))
    assert r1["value"] == 1
    assert [e.type for e in read_events(conn, run_id)] == ["TOOL_INTENT", "TOOL_COMPLETED"]

    # Resume: the effect must not run again.
    r2 = asyncio.run(Context(conn, run_id, MockLLM()).side_effect(lambda c: bump(c, name)))
    assert r2["value"] == 1
    value = conn.execute("SELECT value FROM counters WHERE name=%s", (name,)).fetchone()[0]
    assert value == 1                              # exactly once
    assert len(read_events(conn, run_id)) == 2     # no new events


def test_completion_runs_when_only_intent_was_recorded(conn):
    # Simulate a crash after the intent committed but before the effect.
    run_id = create_run(conn, "agent", {})
    name = f"c-{run_id[:8]}"
    append_event(conn, run_id, 1, "TOOL_INTENT", {"key": f"{run_id}-1"})

    # Resume: intent replays, completion goes live -> effect runs exactly once.
    r = asyncio.run(Context(conn, run_id, MockLLM()).side_effect(lambda c: bump(c, name)))
    assert r["value"] == 1
    value = conn.execute("SELECT value FROM counters WHERE name=%s", (name,)).fetchone()[0]
    assert value == 1
    assert [e.type for e in read_events(conn, run_id)] == ["TOOL_INTENT", "TOOL_COMPLETED"]
