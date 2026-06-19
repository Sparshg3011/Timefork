"""Tests for fencing tokens: a stale token cannot append."""

import pytest

from timefork.events import (
    StaleFenceError,
    append_event,
    connect,
    create_run,
    read_events,
)
from timefork.queue import enqueue_run, read_lease_token


def test_stale_token_rejected_current_accepted():
    with connect() as conn:
        run_id = enqueue_run(conn, "agent", {})
        conn.execute("UPDATE runs SET lease_token = 5 WHERE run_id = %s", (run_id,))
        conn.commit()
        assert read_lease_token(conn, run_id) == 5

        # A stale token writes nothing and is rejected.
        with pytest.raises(StaleFenceError):
            append_event(conn, run_id, 1, "LLM_CALLED", {"x": 1}, lease_token=4)
        assert read_events(conn, run_id) == []

        # The current token is accepted.
        append_event(conn, run_id, 1, "LLM_CALLED", {"x": 1}, lease_token=5)
        assert len(read_events(conn, run_id)) == 1


def test_unfenced_append_still_works():
    # Weeks 1-2 path: no token, plain insert, unaffected by fencing.
    with connect() as conn:
        run_id = create_run(conn, "agent", {})
        append_event(conn, run_id, 1, "LLM_CALLED", {"x": 1})
        assert len(read_events(conn, run_id)) == 1
