# Timefork

A durable execution runtime for AI agents, built from first principles.

AI agents are long-running stateful loops, but processes die. Timefork records
every step of an agent's life in an append-only Postgres event log — the
"diary" — so that a crashed agent resumes exactly where it stopped, side effects
run exactly once, and any run can be rewound to step *k*, patched, and forked
into a new timeline without re-paying for steps `1..k`.

Postgres is the **only** infrastructure: the event log, the task queue, the
lease store, and the dedupe store are all one database. No Kafka, no Redis.

## Status

Week 1 — building the event log and deterministic replay. See `ROADMAP.md` for
the full 5-week plan.

## Quickstart

Requires Docker and Python 3.11+.

```bash
# 1. Start Postgres (schema auto-applies on first boot). Published on :5433.
docker compose up -d --wait

# 2. Install the package + dev tools into a virtualenv.
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 3. Run the tests and the first example.
pytest
python examples/01_hello_event_log.py
```

The connection string defaults to
`postgresql://timefork:timefork@localhost:5433/timefork`; override it with the
`DATABASE_URL` environment variable.

Reset the database after a schema change (until real migrations exist):

```bash
docker compose down -v && docker compose up -d --wait
```
