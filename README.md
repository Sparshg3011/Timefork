# Timefork

**A durable execution runtime for AI agents, built on nothing but Postgres.** Kill the process with `kill -9` at any point and the agent resumes exactly where it stopped — no repeated work, no double payments, no corrupted state. Then rewind any run to step *k*, change the prompt, and fork a new timeline without re-paying for the steps before *k*.

I built this from first principles to understand, deeply, how systems like Temporal and DBOS actually work — and to push on one thing they don't ship: polished time-travel debugging for agents.

---

## The problem

An AI agent is a long-running loop. It calls a model, calls a tool, calls the model again — for seconds, sometimes minutes. That whole time it holds its state in memory.

Processes die. A deploy restarts the box. The OOM killer fires. A worker hangs and someone reaches for `kill -9`. Say the crash lands right after the agent issued a refund. Where does that leave you? You don't know. Maybe the payment went out. Maybe it didn't. Maybe it went out *twice*, because someone retried the job. The agent was a loop holding all its state in memory, and that memory is gone.

Timefork makes the agent **durable**: its progress lives in the database, not in memory, so any crash is just a pause.

## What it does

- **Resume after a crash, for free.** Every step an agent takes is written to an append-only log. After a crash, the agent replays that log — reading back recorded answers instead of re-calling the model — then continues from where it stopped.
- **Side effects exactly once.** A refund or an email fires once and only once, even if the process is killed at the worst possible instant.
- **A fault-tolerant worker fleet.** Stateless workers claim jobs from a Postgres queue. If one dies, another picks the job up and resumes from the log. A revived "zombie" worker can't corrupt anything — its stale writes are rejected by the database.
- **Time travel.** Rewind a run to step *k*, patch it (new system prompt, new config), and fork a fresh timeline. The first *k* steps are copied for free; only the new steps cost a model call. A side-by-side diff shows the shared prefix and the exact point the two runs diverge.
- **Durable human approval.** An agent can pause at a human gate ("approve this $200 refund?"). The pause survives a crash. A human approves out-of-band, and a fresh process resumes past the gate.

## See it for yourself

The fastest way to believe any of this is to watch a process get killed and come back. After the [quickstart](#quickstart):

```bash
python harness/chaos.py 100         # kill 100 runs at random steps; all complete with exactly 15 events
python harness/refund_chaos.py 1000 # 1,000 refund runs; all 3,000 side-effect counters land on exactly 1
```

These are the certificates. They do the bragging.

---

## Architecture

Postgres is the only moving part. It is the event log, the task queue, the lease store, and the dedupe store — all at once. No Kafka, no Redis, no Kubernetes. That's a deliberate design stance, not a shortcut (more on [why](#design-notes--three-hard-decisions) below).

```
  YOUR AGENT  (plain async Python)
  reaches the outside world only through ctx
       │
       ▼
  ┌─────────────────────┐
  │  Context            │   record on the first life, replay on every life after
  │   ctx.llm()         │     ctx.llm()        → 1 event
  │   ctx.side_effect() │     ctx.side_effect()→ 2 events (intent + completion)
  │   ctx.approval()    │     ctx.approval()   → pause durably at a human gate
  └─────────────────────┘
       │  append (committed = durable), fenced by a lease token
       ▼
  ┌──────────────────────── POSTGRES ────────────────────────┐
  │  events          the diary. PK (run_id, seq), dense 1..N  │
  │  runs            status, lease_owner/expiry/token, lineage│
  │  completed_keys  idempotency keys → results (exactly-once)│
  │                                                           │
  │  the task queue IS the runs table:                        │
  │      SELECT ... FOR UPDATE SKIP LOCKED                     │
  └───────────────────────────────────────────────────────────┘
       ▲                              ▲
       │ claim / heartbeat / fence    │ sweep expired leases
       │                              │
  ┌──────────┐  ┌──────────┐    ┌──────────────────┐
  │ worker 1 │  │ worker 2 │ .. │ sweeper (a job,  │
  │ claim →  │  │(stateless)│   │  not a service)  │
  │ replay → │  └──────────┘    └──────────────────┘
  │ execute  │
  └──────────┘
       ▲
       │
  ┌────────────────────────┐
  │ API + thin dashboard   │  list runs, view a timeline, fork, diff
  │ (FastAPI, server HTML) │
  └────────────────────────┘
```

Four tables carry the whole system:

| Table | What it holds |
|---|---|
| `events` | The diary. Every step, in order. Primary key `(run_id, seq)` — dense, gap-free positions starting at 1. |
| `runs` | One row per run: its status, the worker lease (`lease_owner`, `lease_expiry`, `lease_token`), and fork lineage (`parent_run_id`, `fork_seq`). |
| `completed_keys` | The dedupe store. One idempotency key per side effect, with its result. This is what makes "exactly once" exact. |
| *(no fourth broker)* | The task queue **is** the `runs` table — a row's status (`queued` / `running` / `paused` / `completed` / `failed`) is its queue state. |

The orchestrator is not a service. It's a sweeper job: find runs whose lease expired, put them back on the queue, let a healthy worker reclaim them.

## How it works

Your agent is plain async Python. The only rule: every bit of non-determinism — the model, tools, time, randomness — goes through `ctx`. That's what makes replay possible.

```python
async def refund_agent(ctx, order):
    # Recorded the first time, replayed (for free) on every life after.
    decision = await ctx.llm(f"Should we refund order {order['id']}? Reason: {order['reason']}")

    # A durable pause. The process may die here; the question is already in the diary.
    if not await ctx.approval(f"Refund ${order['amount']} for order {order['id']}?"):
        return "denied"

    # Fires exactly once, even under kill -9 at any instant.
    await ctx.side_effect(lambda conn: issue_refund(conn, order["id"], order["amount"]))
    return "refunded"
```

**Record/replay.** On a run's first life, each `ctx.llm()` call records an `LLM_CALLED` event with the prompt and the response. On the next life, the `Context` reads the log from the database and a cursor walks it: position *i* in the code maps to event *i* in the log. While there are events left to replay, `ctx.llm()` returns the recorded response and never touches the model. When the cursor runs past the end of the log, the agent is "caught up" and starts doing real work again. That's resume.

**The strict rule that makes it safe:** the agent must make the same calls in the same order each life. If you change the code (say, you edit a prompt) between crashes, the cursor would be out of sync — and a naive replay would hand back a stale, wrong answer. Timefork refuses. It compares what the code is asking for against what the log recorded, and on a mismatch raises `ReplayDivergenceError` with a precise diff. Never silent corruption.

**Exactly-once side effects** are the same idea, split into two durable phases:

- **Phase 1 — intent.** Record a `TOOL_INTENT` event and commit. Now the diary says "about to issue this refund," and the process is free to die.
- **Phase 2 — completion.** Do the work *and* write the result in one transaction: run the effect, insert an idempotency key + result into `completed_keys`, and append a `TOOL_COMPLETED` event — all committing together.

Before acting, Phase 2 checks `completed_keys`. **Crash before that commit** → on resume the key is absent, so the effect runs. **Crash after** → the key is present, so the effect is skipped. Either way: exactly once.

**Fencing tokens** stop zombies. Each time a worker claims a run, a monotonic `lease_token` (a `BIGINT`) is bumped, and the worker stamps that token onto every write. The append only succeeds `WHERE EXISTS (... AND lease_token = my_token)`. If the worker was presumed dead and the run got reclaimed, the token on the row has moved on — so the zombie's `INSERT` matches zero rows and is rejected with `StaleFenceError`. The correctness lives in the SQL, not in a Python `if`.

The diary's vocabulary is small and legible:

| Event type | Meaning |
|---|---|
| `RUN_STARTED` / `RUN_COMPLETED` / `RUN_FAILED` | The run's lifecycle. |
| `LLM_CALLED` | A model call, with its prompt and response. |
| `TOOL_INTENT` / `TOOL_COMPLETED` | The two phases of an exactly-once side effect. |
| `APPROVAL_REQUESTED` / `APPROVAL` | A human gate: the question, then the yes/no decision. |
| `PATCH_APPLIED` | A fork's patch (new prompt/config), injected at the fork point. |

## Guarantees, and the proof for each

I don't ask you to trust the prose. Each guarantee is tied to a test or a reproducible certificate you can run yourself.

| Guarantee | How it's enforced | Proof you can run |
|---|---|---|
| **Durable** — once an event commits, no crash erases it; duplicates fail loudly, never silently. | `append_event` commits before returning; primary key `(run_id, seq)` makes duplicate or out-of-order writes impossible. | `pytest tests/test_events.py` |
| **Resumable** — a crashed agent continues exactly where it stopped, with zero re-paid model calls. | Replay reads recorded answers from the diary instead of re-calling the model. | `python harness/chaos.py 100` → 100/100 complete, **exactly 15** events each, byte-identical to a never-crashed baseline |
| **No silent corruption** — changed code gives a loud error, never a stale answer. | The replay cursor compares what the code asks for against what the diary recorded; a mismatch raises `ReplayDivergenceError` with a diff. | `pytest tests/test_context.py` |
| **Exactly-once side effects** — a refund/email/payment fires once, wherever it's killed. | Two phases (intent + completion) plus an idempotency key; effect, key, and completion event commit in one transaction. | `python harness/refund_chaos.py 1000` → 1,000/1,000 runs, all **3,000** counters exactly 1 |
| **Zombie-proof fleet** — a dead worker that wakes up can't corrupt anything. | Monotonic fencing tokens; every claim bumps the token, and appends are rejected unless it still matches. | `python harness/fleet.py chaos` and `python harness/fleet.py zombie` → all jobs done, the thawed zombie fenced out, counters stay 1 |
| **Cheap forks** — forking a 40-step run at step 39 costs 1 model call, not 40. | The prefix is copied with `INSERT ... SELECT` and replayed for free; only post-fork steps execute. | `python bench/benchmarks.py` |
| **Durable approval pause** — a human-in-the-loop gate survives a crash. | The pause is recorded as an event; a fresh process replays and re-pauses at the same gate. | `pytest tests/test_approval.py` |

## Benchmarks

Numbers are **indicative** (mock LLM at fixed latencies, Postgres 16 on a laptop) and **reproducible** via `python bench/benchmarks.py`. They measure the machinery, not network or real-model variance.

| What | Result |
|---|---|
| Per-step durability overhead (one commit) | **~1.3 ms** p50 |
| Recover a 50-step run (replay) | **0.76 / 2.07 / 2.41 ms** p50/p95/p99 |
| Replay throughput | **~66,000 events/sec** |
| Fork vs. rerun (40 steps, fork at 39) | **32× faster**, 39 model calls saved |

## What this project demonstrates

If you're skimming for skills, here's what's on display — each grounded in real code, not slideware:

- **Distributed-systems fundamentals** — leases, heartbeats, failover, monotonic fencing tokens, a queue built on `FOR UPDATE SKIP LOCKED`.
- **Event sourcing** — modeling all state as an append-only log of events (the source of truth is *what happened*, not a current snapshot), with deterministic replay.
- **Postgres internals & raw SQL** — no ORM. Transactions, row-level locking, `clock_timestamp()` vs. transaction time, fenced inserts via `WHERE EXISTS`, idempotency stores.
- **Idempotency / exactly-once semantics** — the two-phase intent/completion pattern with an atomic completion.
- **Correctness under failure** — crash-injection testing with uncatchable `SIGKILL` at named code points, plus `SIGSTOP`/`SIGCONT` zombie scenarios, all asserting on side-effect counters.
- **API design** — a small `Context` SDK that hides all of it behind `ctx.llm()`, `ctx.side_effect()`, and `ctx.approval()`.

## Quickstart

Prerequisites: Docker (Colima works), Python 3.11+.

```bash
# 1. Start Postgres 16 (schema auto-applies on first boot; published on :5433).
docker compose up -d --wait

# 2. A virtualenv, and the package.
python3.11 -m venv ~/.venvs/timefork
source ~/.venvs/timefork/bin/activate
pip install -e ".[dev]"

# 3. The whole suite — 32 tests, ~5s, against the real Postgres.
pytest
```

**See it work** — narrated examples, one mechanism each:

```bash
python examples/03_record_and_replay.py   # a crashed run resumes for free
python examples/11_fork.py                # fork at step 3: 5 model calls become 2 after the fork
python examples/13_showcase.py            # refund agent: consult model, pause for approval, resume, refund once
```

**Run the crash certificates yourself** — the part worth your 30 seconds:

```bash
python harness/chaos.py 100         # resume: 100/100 complete, exactly 15 events each
python harness/refund_chaos.py 1000 # exactly-once: 1,000/1,000, all 3,000 counters = 1
python harness/fleet.py chaos       # fleet: random worker kills, all jobs done, every counter = 1
python harness/fleet.py zombie      # fleet: a thawed zombie's writes are fenced out
```

**Time-travel debugging from the CLI:**

```bash
timefork ls                                  # recent runs + lineage
timefork show RUN                            # a run's events + parent/forks
timefork fork RUN --at 39 --system-prompt new.txt
timefork diff RUN_A RUN_B                    # shared prefix + first divergence
```

**Or the thin web dashboard** (server-rendered HTML, no JavaScript):

```bash
uvicorn timefork.dashboard:app --port 8000   # open http://localhost:8000
```

**The real model, optional and honest.** `examples/13_showcase.py` runs on a **mock model by default**, so anyone can reproduce everything from a clean clone with no API key. Point it at the real Claude model only if you want to:

```bash
pip install -e ".[showcase]"
export ANTHROPIC_API_KEY=sk-ant-...
python examples/13_showcase.py
```

Replay reads recorded answers back, so even on the real model nothing is ever re-called twice.

`DATABASE_URL` defaults to `postgresql://timefork:timefork@localhost:5433/timefork`.

## Tech stack

- **Python 3.11+**, `psycopg` 3, `pytest`. No ORM — raw SQL is the point.
- **PostgreSQL 16** as the *only* infrastructure: event log, queue, lease store, dedupe store.
- **FastAPI** for the small API and dashboard.
- **`anthropic`** SDK — optional, only for the real-model showcase.

## Design notes — three hard decisions

The interesting parts are the tradeoffs, and where I chose differently from Temporal.

1. **Postgres-only, on purpose.** Temporal needs its own server plus a database; many setups add Kafka or Redis on top. I made Postgres carry every role. The cost is a throughput ceiling at very large scale. The win is that the whole system is one `docker compose up`, every invariant is a database constraint you can inspect, and there are no moving parts to drift out of sync. Better still: because a side effect and its idempotency key commit in the *same transaction*, exactly-once needs no external coordinator — a crash commits both or neither. For agent workloads — minutes-long, not millions-per-second — that's the right trade.

2. **The diary is exposed and forkable.** Most durable-execution engines treat the history as an internal detail. I made it a first-class, queryable, *forkable* object, because the headline feature — rewind, patch, branch, diff — only works if the log is something you can read and copy. The honest caveat, stated right in the CLI and dashboard: a fork is a *fresh experiment*, not a counterfactual proof of what the original would have done.

3. **Loud failure over silent corruption.** When replay can't be trusted (the code changed under it), the easy path is to return the stale recorded answer and hope. I made it raise, with a diff. A run that stops with a clear error is recoverable; a run that silently returns the wrong answer is a debugging nightmare you may never even notice.

## Status

A finished, working system, top to bottom: durable resume, exactly-once side effects, a fault-tolerant worker fleet, forking with a timeline diff, a CLI, a thin dashboard, a durable approval gate, reproducible benchmarks, and crash-injection certificates. **32 tests pass in ~5s** against a real Postgres. The full design write-up — every concept, in order, with the reasoning — lives in [`ROADMAP.md`](ROADMAP.md).

Honest limitations: the real-model client is written and matches the mock's interface, but it's exercised only in the showcase, not in tests — every test, benchmark, and certificate runs on the mock model, by design. The fleet chaos and zombie runs are *manual* certificates, kept out of the automated suite on purpose (spawning and `SIGKILL`ing many processes is too heavy to run on every `pytest`). Benchmarks are indicative, not production numbers.
