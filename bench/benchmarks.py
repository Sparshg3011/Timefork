"""Reproducible benchmarks for Timefork.

Everything runs on the mock LLM at fixed latencies, so the numbers are
deterministic and anyone can reproduce them from a clean clone -- no paid API,
no flaky network.

  python bench/benchmarks.py
"""

import asyncio
import time

from timefork.context import Context
from timefork.events import connect, create_run
from timefork.fork import fork_run
from timefork.mock_llm import MockLLM


def percentiles(samples):
    s = sorted(samples)

    def at(q):
        return s[min(len(s) - 1, int(q / 100 * len(s)))]

    return at(50), at(95), at(99)


async def per_step_overhead(n=500):
    """Cost of recording each step to the diary, vs not recording at all."""
    baseline = []
    brain = MockLLM(seed=1)
    for i in range(n):
        t = time.perf_counter()
        await brain.complete(f"step {i}")
        baseline.append((time.perf_counter() - t) * 1000)

    durable = []
    with connect() as conn:
        ctx = Context(conn, create_run(conn, "bench", {}), MockLLM(seed=1))
        for i in range(n):
            t = time.perf_counter()
            await ctx.llm(f"step {i}")
            durable.append((time.perf_counter() - t) * 1000)
    return percentiles(baseline), percentiles(durable)


async def recovery_time(n_steps=50, trials=40):
    """Time to recover a crashed run: build a Context and replay it to the end."""
    with connect() as conn:
        run_id = create_run(conn, "bench", {})
        ctx = Context(conn, run_id, MockLLM(seed=1))
        for i in range(n_steps):
            await ctx.llm(f"step {i}")

        times = []
        for _ in range(trials):
            t = time.perf_counter()
            rctx = Context(conn, run_id, MockLLM(seed=1))  # reads the whole diary
            for i in range(n_steps):
                await rctx.llm(f"step {i}")  # all replayed, no model calls
            times.append((time.perf_counter() - t) * 1000)
    return percentiles(times), n_steps


async def fork_vs_rerun(n_steps=40, latency=0.02):
    """Forking reuses the prefix for free; re-running pays for every step."""
    with connect() as conn:
        parent = create_run(conn, "bench", {})
        pctx = Context(conn, parent, MockLLM(seed=1, latency_s=latency))
        for i in range(n_steps):
            await pctx.llm(f"step {i}")

        # Re-run the whole thing from scratch.
        rbrain = MockLLM(seed=1, latency_s=latency)
        t = time.perf_counter()
        rctx = Context(conn, create_run(conn, "bench", {}), rbrain)
        for i in range(n_steps):
            await rctx.llm(f"step {i}")
        rerun_ms = (time.perf_counter() - t) * 1000

        # Fork near the end and finish only the last step.
        t = time.perf_counter()
        child = fork_run(conn, parent, n_steps - 1, {"note": "fork"})
        fbrain = MockLLM(seed=1, latency_s=latency)
        fctx = Context(conn, child, fbrain)
        for i in range(n_steps):
            await fctx.llm(f"step {i}")
        fork_ms = (time.perf_counter() - t) * 1000
    return {"rerun_ms": rerun_ms, "rerun_calls": rbrain.calls,
            "fork_ms": fork_ms, "fork_calls": fbrain.calls, "steps": n_steps}


def main():
    print("Timefork benchmarks (mock LLM, fixed latencies)\n")

    base, dur = asyncio.run(per_step_overhead())
    print("per-step latency, ms (p50 / p95 / p99):")
    print(f"  no durability:   {base[0]:.3f} / {base[1]:.3f} / {base[2]:.3f}")
    print(f"  durable (diary): {dur[0]:.3f} / {dur[1]:.3f} / {dur[2]:.3f}")
    print(f"  overhead/step (p50): {dur[0] - base[0]:.3f} ms\n")

    rec, n = asyncio.run(recovery_time())
    eps = n / (rec[0] / 1000) if rec[0] else 0
    print(f"recovery time for a {n}-step run, ms (p50 / p95 / p99):")
    print(f"  {rec[0]:.2f} / {rec[1]:.2f} / {rec[2]:.2f}   (~{eps:,.0f} events/sec replayed)\n")

    f = asyncio.run(fork_vs_rerun())
    print(f"fork vs rerun ({f['steps']}-step run, forked at step {f['steps'] - 1}):")
    print(f"  rerun from scratch: {f['rerun_ms']:7.1f} ms, {f['rerun_calls']:2d} model calls")
    print(f"  fork + finish:      {f['fork_ms']:7.1f} ms, {f['fork_calls']:2d} model calls")
    print(f"  -> {f['rerun_ms'] / f['fork_ms']:.0f}x faster, "
          f"{f['rerun_calls'] - f['fork_calls']} model calls saved")


if __name__ == "__main__":
    main()
