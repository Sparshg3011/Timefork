"""Timefork: a durable execution runtime for AI agents.

Every step of an agent's life is recorded in an append-only Postgres event
log, so a crashed agent resumes where it stopped, side effects run exactly
once, and any run can be rewound, patched, and forked into a new timeline.
"""

__version__ = "0.1.0"
