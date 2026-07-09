# Timefork — Roadmap

A 5-week build of a durable execution runtime for AI agents. Read the current
week's section before working on it. Each week ends with an **exit test** that
must pass before moving on.

A **mock LLM** is used for all development and tests; a real LLM appears only in
the Week 5 showcase. Postgres is the only infrastructure.

This was the working plan, and it shipped. A few Week 5 stretch items were cut
per the buffer policy — the dashboard's kill/retry buttons, the throughput-knee
benchmark, and the demo video. Everything else below exists in the repo.

---

## Week 1 — Event log + replay (single machine)

**Goal:** an agent's entire life is an append-only log of events (the "diary"),
and a crashed agent recovers by *replaying* that log instead of re-running work.

Day-by-day:

- **Day 1 — Schema + diary module.** `db/schema.sql` (`runs`, `events`, with the
  central invariant `PRIMARY KEY (run_id, seq)`). `timefork/events.py`: the diary
  module — `create_run`, `append_event` (raises `DuplicateSequenceError` on a
  repeated seq), `read_events`, `set_run_status`. Example
  `01_hello_event_log.py`. 3 tests.
- **Day 2 — Mock LLM.** `timefork/mock_llm.py`: scripted responses, adjustable
  latency, deterministic mode. No real API, ever, in tests.
- **Days 3–4 — Context + replay engine.** A `Context` object exposes `ctx.llm()`.
  On a fresh run it *records* each call as an `LLM_CALLED` event; on recovery it
  *replays* — reading the recorded result back instead of calling the LLM again.
  This is the determinism rule: orchestration code is deterministic; all
  non-determinism (LLM, tools, time, randomness) goes through `ctx.*` and is
  recorded as an event.
- **Day 5 — Divergence detector + crash test.** On replay, if the code asks for
  something different from what the diary recorded, fail loudly with a diff —
  never silently corrupt. Crash-at-random-step resume harness.

**Exit test:** a 15-step agent, killed at random steps 100×, always completes,
and the diary contains exactly 15 LLM events.

**Check your understanding:**
- Why must the orchestration loop be deterministic for replay to work?
- Why supply `seq` explicitly instead of using a DB auto-increment?
- What should happen if, on replay, step 7 asks for a different model than the
  diary recorded at step 7?

---

## Week 2 — Exactly-once side effects

`completed_keys(idempotency_key PK, result, completed_at)`; `TOOL_INTENT` /
`TOOL_COMPLETED` events with key `{run_id}-{seq}`. The key and result are stored
atomically with the completion, so a crash between "did the work" and "recorded
it" never double-charges.

**Exit test:** full crash matrix + 1,000 random-timing runs → every side-effect
counter is exactly 1.

---

## Week 3 — Workers, leases, fencing

Workers claim runs via `FOR UPDATE SKIP LOCKED`; heartbeats + a sweeper requeue
dead workers' runs; monotonic **fencing tokens** are enforced on append so a
revived "zombie" worker's writes are rejected.

**Exit test:** failover demo; a SIGSTOP/SIGCONT zombie's writes are provably
rejected; a 10-minute chaos run (a random worker killed every 10s) completes
50/50 with zero duplicates.

---

## Week 4 — Time travel (the headline feature)

Fork a run: copy its event prefix up to step *k*, append a `PATCH_APPLIED` event
(new prompt/model), and enqueue it as a new timeline — without re-paying for
steps `1..k`. Lineage (`parent_run_id`, `fork_seq`), a timeline diff, and a CLI
(`timefork fork RUN --at 39 --system-prompt new.txt`, `timefork diff A B`).

**Exit test:** forking a 40-step run pays zero executor calls for the prefix; the
diff shows the shared prefix and the first divergence. **Honesty rule:** a fork
is a fresh experiment, not a counterfactual proof — the UI/CLI must say so.

---

## Week 5 — Dashboard, benchmarks, write-up

Thin FastAPI dashboard (run list, timeline, kill/retry/fork/diff). `bench/`:
recovery p50/p95/p99, per-step overhead vs a no-durability baseline at fixed mock
latencies, replay events/sec, fork-vs-rerun, throughput knee, a chaos
certificate. One real-LLM showcase agent with a single approval pause. <3-min
demo video, README with numbers, design write-up.

**Exit test:** a stranger reproduces everything from a clean clone without asking
us anything.

**Buffer policy:** if behind, cut Week 5 dashboard polish first, the showcase
agent second. **Never** cut Week 2–3 correctness tests.
