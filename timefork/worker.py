"""A worker: claim a run, run its agent fenced by the lease, repeat.

Weeks 1-3 fused. For each queued run the worker claims it, captures its fencing
token, and runs the agent through a Context fenced by that token while a
background thread heartbeats the lease. It completes the run only if it still
holds the lease; if it was fenced out (presumed dead, the run reassigned), it
drops the run for whoever owns it now.
"""

import asyncio
import threading
import time

from .context import Context
from .events import StaleFenceError, connect
from .queue import claim_run, heartbeat, read_lease_token


def run_worker(
    worker_id,
    agent_name,
    agent_fn,
    make_llm,
    lease_seconds=30,
    heartbeat_seconds=None,
    run_forever=False,
):
    beat_every = heartbeat_seconds or max(lease_seconds / 3, 0.5)
    while True:
        with connect() as conn:
            run_id = claim_run(conn, worker_id, lease_seconds, agent_name=agent_name)
            token = read_lease_token(conn, run_id) if run_id is not None else None
        if run_id is None:
            if run_forever:
                time.sleep(0.2)
                continue
            return
        _execute(worker_id, run_id, token, agent_fn, make_llm, lease_seconds, beat_every)


def _execute(worker_id, run_id, token, agent_fn, make_llm, lease_seconds, beat_every):
    # Heartbeat the lease in the background while the agent runs.
    stop = threading.Event()

    def beat():
        with connect() as hb:
            while not stop.wait(beat_every):
                heartbeat(hb, run_id, worker_id, lease_seconds=lease_seconds)

    hb_thread = threading.Thread(target=beat, daemon=True)
    hb_thread.start()
    try:
        with connect() as conn:
            ctx = Context(conn, run_id, make_llm(), lease_token=token)
            asyncio.run(agent_fn(ctx))
            # Complete only if we still hold the lease; else we were fenced out.
            conn.execute(
                "UPDATE runs SET status = 'completed' "
                "WHERE run_id = %s AND lease_token = %s AND status = 'running'",
                (run_id, token),
            )
            conn.commit()
    except StaleFenceError:
        pass  # fenced out -- another worker owns this run now
    finally:
        stop.set()
        hb_thread.join(timeout=2)
