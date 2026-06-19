"""A worker: claim a run, execute its agent fenced by the lease, repeat.

This wires Weeks 1-3 together. For each queued run the worker claims it (no two
workers get the same one), captures its fencing token, and runs the agent
through a Context fenced by that token -- so a presumed-dead worker's writes are
rejected -- then marks it completed. Returns when no queued runs of this kind
remain (handy for demos and tests; a long-lived worker would sleep and retry).
"""

import asyncio

from .context import Context
from .events import connect, set_run_status
from .queue import claim_run, read_lease_token


def run_worker(worker_id, agent_name, agent_fn, make_llm, lease_seconds=30):
    while True:
        with connect() as conn:
            run_id = claim_run(conn, worker_id, lease_seconds, agent_name=agent_name)
            if run_id is None:
                return
            token = read_lease_token(conn, run_id)  # the worker's fence for this run
            ctx = Context(conn, run_id, make_llm(), lease_token=token)
            asyncio.run(agent_fn(ctx))
            set_run_status(conn, run_id, "completed")
