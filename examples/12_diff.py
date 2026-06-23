"""Lineage + timeline diff: see how a fork relates to its parent.

Run a parent, fork it at step 3, run the child, then show the family tree and a
side-by-side diff -- the shared prefix and the exact step where they split.

Needs Postgres:  docker compose up -d --wait
Run with:        python examples/12_diff.py
"""

import asyncio

from timefork.context import Context
from timefork.diff import diff_runs
from timefork.events import connect, create_run
from timefork.fork import children_of, fork_run, parent_of
from timefork.mock_llm import MockLLM


async def agent(ctx):
    out = ""
    for i in range(1, 6):
        out = await ctx.llm(f"[{ctx.config('style', 'neutral')}] step {i}")
    return out


async def main():
    with connect() as conn:
        parent_id = create_run(conn, "agent", {})
        await agent(Context(conn, parent_id, MockLLM(seed=1)))
        child_id = fork_run(conn, parent_id, 3, {"style": "generous"})
        await agent(Context(conn, child_id, MockLLM(seed=1)))

        forks = [(c[:8], s) for c, s in children_of(conn, parent_id)]
        par, at = parent_of(conn, child_id)
        print(f"family tree: {parent_id[:8]} --(forked at {at})--> {child_id[:8]}")
        print(f"  {parent_id[:8]} forks = {forks}\n")

        d = diff_runs(conn, parent_id, child_id)
        print(f"shared prefix: {d['shared']} steps; first divergence at seq {d['diverge_at']}\n")
        for r in d["rows"]:
            if r.same:
                print(f"  seq {r.seq}  =  {r.a}")
            else:
                print(f"  seq {r.seq}  X  parent: {r.a or '—'}")
                print(f"           child:  {r.b or '—'}")
        print("\n(a fork is a fresh experiment, not proof of what the parent would have done)")


if __name__ == "__main__":
    asyncio.run(main())
