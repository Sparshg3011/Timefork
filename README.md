# Timefork

> A durable execution runtime for AI agents — built from first principles on nothing but Postgres.

AI agents are long-running stateful loops, and processes die. Timefork records every step
of an agent's life in an append-only Postgres event log — the "diary" — so that a crashed
agent resumes exactly where it stopped, side effects run exactly once, a fleet of workers
tolerates death, and — the headline — any run can be **rewound, patched, and forked into a
new timeline** without re-paying for the steps before the fork.

**Postgres is the only infrastructure.** No Kafka, no Redis, no Temporal cluster. The event
log, the task queue (`FOR UPDATE SKIP LOCKED`), the lease store, and the dedupe store are all
one database.

## What it guarantees (and how it's proven)

| Guarantee | What it means | Proof |
|---|---|---|
| **Resume** | a crashed agent continues, never restarts | a 15-step agent killed at random steps 100× always finishes with exactly 15 events |
| **Exactly-once** | a side effect (a refund, an email) fires once | 1,000 runs killed at random points → every counter exactly 1 |
| **Fault-tolerant fleet** | workers die and recover; revived "zombies" can't corrupt | 50 jobs under random `kill -9` + a frozen-then-thawed zombie → every counter exactly 1 |
| **Time travel** | rewind, patch, and fork a run — reusing the prefix for free | forking a 40-step run pays 1 model call instead of 40 |

These aren't slogans — each row is an automated test or a `kill -9` certificate you can run
(see Quickstart).

## Benchmarks

All on the mock LLM at fixed latencies (`python bench/benchmarks.py`), so the numbers are
reproducible and never depend on a paid, flaky API. Indicative figures (Postgres 16 via
colima, Apple Silicon):

| Metric | Result |
|---|---|
| Per-step durability overhead | **~1.3 ms** (one commit; no-durability baseline 0.009 ms) |
| Recovery of a 50-step run (p50 / p95 / p99) | **0.76 / 2.07 / 2.41 ms** (~66k events/sec replayed) |
| Fork vs. rerun (40 steps, forked at step 39) | **32× faster, 39 model calls saved** |

## How it works

Four Postgres tables carry everything:

- **`events`** — the append-only diary. `PRIMARY KEY (run_id, seq)` makes a duplicate step impossible.
- **`runs`** — one row per run; in Week 3 it doubles as the task queue and lease store.
- **`completed_keys`** — the idempotency-key dedupe store that backs exactly-once.
- **`counters`** — a measurable side effect, so tests can assert "this fired exactly once."

An agent is plain async Python that reaches the world only through a `Context`:

```python
async def refund_agent(ctx):
    amount = await ctx.llm("how much should we refund?")          # recorded; replayed free on resume
    await ctx.side_effect(lambda conn: issue_refund(conn, amount)) # runs exactly once, even under kill -9
```

`ctx.llm()` records each call and reads it back after a crash (the model is never called twice).
`ctx.side_effect()` performs an action exactly once — recording an intent, then committing the
effect, its dedupe key, and a completion event in a **single transaction**, so there's no window
for a crash to double-execute. Workers claim runs (no two grab the same one), run them fenced by
a monotonic lease token, heartbeat to hold the lease, and complete — a dead worker's job is swept
back to the queue and resumed from the diary, and a revived zombie's stale-token writes are
rejected by the database.

## Quickstart

Requires Docker and Python 3.11+.

```bash
docker compose up -d --wait                 # Postgres 16 (schema auto-applies). Published on :5433.
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                                       # the full suite, against the real Postgres
python examples/03_record_and_replay.py      # a crashed run resumes for free
python examples/11_fork.py                    # a fork reuses its prefix
python examples/13_showcase.py                # a refund agent that pauses for human approval
python bench/benchmarks.py                    # the numbers above
```

The showcase agent runs on the mock brain out of the box; to point it at the real
model, `pip install -e ".[showcase]"` and set `ANTHROPIC_API_KEY`.

The CLI (time-travel debugging from the terminal):

```bash
timefork ls                                  # recent runs + lineage
timefork fork RUN --at 39 --system-prompt new.txt   # rewind, patch, branch a new timeline
timefork diff RUN_A RUN_B                     # side-by-side: shared prefix + first divergence
timefork approve RUN                          # sign off a run paused at a human-approval gate
```

The `kill -9` certificates (the proofs, run manually):

```bash
python harness/chaos.py 100                  # Resume: killed 100×, always 15 events
python harness/refund_chaos.py 1000          # Exactly-once: 1,000 runs, every counter exactly 1
python harness/fleet.py chaos                # Fleet: random worker kills, every counter exactly 1
python harness/fleet.py zombie               # Fencing: a frozen-then-thawed zombie is rejected
```

`DATABASE_URL` defaults to `postgresql://timefork:timefork@localhost:5433/timefork`.

## Design notes — the three hardest decisions (vs. Temporal's choices)

1. **Postgres as the *only* infrastructure.** Temporal runs a dedicated cluster with its own
   storage and gRPC frontend. Timefork puts the event log, the queue, leases, and the dedupe
   store in one database. The payoff isn't just operational simplicity: because a side effect
   and its idempotency key commit in the *same transaction*, exactly-once needs no external
   coordinator — a crash commits both or neither. The trade-off is that you scale by scaling
   Postgres, not a sharded service — a deliberate choice for a single-node-to-small-fleet runtime.

2. **The diary is the source of truth — and it's *exposed*.** Like Temporal, state is derived by
   replaying an event history. Unlike Temporal, where that history is an internal detail, Timefork
   treats the diary as a first-class, queryable, **forkable** object. That's what makes time-travel
   debugging possible at all: copy a prefix, append a patch event, branch a new timeline.

3. **Loud failure over silent corruption.** Replay matches recorded answers to calls *by position*,
   which is fragile if the agent's code changes between record and replay. Rather than silently
   hand back a stale answer, the divergence detector fails loudly with a diff. And a fork is
   honestly labelled a *fresh experiment*, not a counterfactual — the runtime refuses to pretend it
   knows what the agent "would have" done.

## Status

Weeks 1–4 complete: resume, exactly-once side effects, the fault-tolerant fleet, and time travel.
Week 5 adds a thin FastAPI dashboard, reproducible benchmarks, and a showcase refund agent that
runs on a real model and **pauses for a human to approve** before any money moves — the wait is
recorded in the diary, so it survives a crash like everything else. Full curriculum in
[`ROADMAP.md`](ROADMAP.md).
