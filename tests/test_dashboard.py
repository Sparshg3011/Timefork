"""Test the FastAPI dashboard with FastAPI's in-process TestClient."""

from fastapi.testclient import TestClient

from timefork.dashboard import app
from timefork.events import append_event, connect, create_run

client = TestClient(app)


def test_dashboard_lists_shows_and_forks():
    with connect() as conn:
        run_id = create_run(conn, "dash_agent", {})
        append_event(conn, run_id, 1, "LLM_CALLED", {"prompt": "p1", "response": "r1"})
        append_event(conn, run_id, 2, "LLM_CALLED", {"prompt": "p2", "response": "r2"})

    # index lists the run
    r = client.get("/")
    assert r.status_code == 200 and run_id[:8] in r.text

    # run detail shows its timeline
    r = client.get(f"/run/{run_id}")
    assert r.status_code == 200 and "timeline" in r.text

    # forking redirects to the new child, which shows its lineage
    r = client.post(
        f"/run/{run_id}/fork",
        data={"at_seq": "2", "key": "style", "value": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    child = r.headers["location"].split("/run/")[1]
    rc = client.get(f"/run/{child}")
    assert rc.status_code == 200 and "forked from" in rc.text

    # diff of parent vs child shows a divergence
    rd = client.get(f"/diff/{run_id}/{child}")
    assert rd.status_code == 200 and "divergence" in rd.text
