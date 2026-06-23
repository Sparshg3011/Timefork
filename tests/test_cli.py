"""Test the CLI: fork then diff a run end to end."""

import asyncio
import subprocess
import sys

from timefork.context import Context
from timefork.events import connect, create_run
from timefork.mock_llm import MockLLM


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "timefork.cli", *args], capture_output=True, text=True
    )


async def _agent(ctx):
    for i in range(1, 6):
        await ctx.llm(f"[{ctx.config('style', 'neutral')}] step {i}")


def test_cli_fork_then_diff():
    with connect() as conn:
        parent_id = create_run(conn, "cli_agent", {})
        asyncio.run(_agent(Context(conn, parent_id, MockLLM(seed=1))))

    forked = _cli("fork", parent_id, "--at", "3", "--set", "style=generous")
    assert forked.returncode == 0, forked.stderr
    child_id = forked.stdout.strip()
    assert len(child_id) == 36  # a uuid

    diffed = _cli("diff", parent_id, child_id)
    assert diffed.returncode == 0, diffed.stderr
    assert "first divergence at seq 4" in diffed.stdout
