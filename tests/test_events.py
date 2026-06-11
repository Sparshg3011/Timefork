"""Day 1 tests: the diary module against a real Postgres (no mocks).

Postgres must be up:  docker compose up -d --wait
"""

import pytest

from timefork.events import (
    DuplicateSequenceError,
    UnknownRunError,
    append_event,
    connect,
    create_run,
    read_events,
    set_run_status,
)


@pytest.fixture
def conn():
    with connect() as c:
        yield c


def test_append_and_read_round_trip(conn):
    run_id = create_run(conn, "test_agent", {"n": 1})

    append_event(conn, run_id, 1, "RUN_STARTED", {"n": 1})
    append_event(conn, run_id, 2, "LLM_CALLED", {"response": "hi"})
    append_event(conn, run_id, 3, "RUN_COMPLETED", {"output": "hi"})

    events = read_events(conn, run_id)
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.type for e in events] == ["RUN_STARTED", "LLM_CALLED", "RUN_COMPLETED"]
    assert events[1].payload == {"response": "hi"}


def test_duplicate_seq_is_rejected_and_history_unchanged(conn):
    run_id = create_run(conn, "test_agent", {})
    append_event(conn, run_id, 1, "RUN_STARTED", {"v": "original"})

    with pytest.raises(DuplicateSequenceError):
        append_event(conn, run_id, 1, "RUN_STARTED", {"v": "impostor"})

    events = read_events(conn, run_id)
    assert len(events) == 1
    assert events[0].payload == {"v": "original"}


def test_set_run_status(conn):
    run_id = create_run(conn, "test_agent", {})
    set_run_status(conn, run_id, "completed")

    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()[0]
    assert status == "completed"

    with pytest.raises(UnknownRunError):
        set_run_status(conn, "no-such-run", "failed")
