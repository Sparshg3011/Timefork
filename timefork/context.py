"""The replay context: record on a run's first life, replay on every life after.

An agent reaches the world only through ctx: ctx.llm() for the model (safe to
re-run after a crash) and ctx.side_effect() for things that must happen exactly
once (an email, a payment). The cursor walks the diary one event at a time --
an llm() owns one event, a side_effect() owns two (intent + completion).
"""

from typing import Any, Callable

import psycopg
from psycopg.types.json import Json

from .crash import maybe_crash
from .events import StaleFenceError, append_event, read_events


class ReplayDivergenceError(Exception):
    """On replay, the agent asked for something different from the diary.

    Replay matches recorded answers to calls by position. If the code changed
    between lives, position i no longer means the same call, so we refuse to
    hand back a stale answer and fail loudly with a diff instead.
    """

    def __init__(self, seq: int, recorded: str, requested: str) -> None:
        super().__init__(
            f"replay diverged at seq {seq}: diary has {recorded}, "
            f"code now wants {requested}"
        )
        self.seq = seq
        self.recorded = recorded
        self.requested = requested


class Context:
    """An agent's only door to non-determinism and to the outside world.

    `llm` is anything with `async complete(prompt) -> str`.
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        run_id: str,
        llm: Any,
        lease_token: int | None = None,
    ) -> None:
        self.conn = conn
        self.run_id = run_id
        self._llm = llm
        # When set (a worker run), every append is fenced with this token so a
        # presumed-dead worker's writes are rejected. None = unfenced (direct).
        self._lease_token = lease_token
        self._history = read_events(conn, run_id)  # everything recorded so far
        self._cursor = 0  # index of the next event (to replay, or about to append)
        self._config: dict = {}  # built up by PATCH_APPLIED events (forks)

    # -- cursor helpers: the dense seq of the next event is always cursor + 1 --

    def _replaying(self) -> bool:
        return self._cursor < len(self._history)

    def _replay_next(self, expected_type: str):
        event = self._history[self._cursor]
        if event.type != expected_type:
            raise ReplayDivergenceError(event.seq, event.type, expected_type)
        self._cursor += 1
        return event

    def _record(self, type: str, payload: dict) -> int:
        seq = self._cursor + 1
        append_event(self.conn, self.run_id, seq, type, payload, self._lease_token)
        self._cursor += 1
        return seq

    def _apply_pending_patches(self) -> None:
        # PATCH_APPLIED events (injected by a fork) are not agent calls; apply
        # them to the config as the cursor reaches them, transparently.
        while (
            self._cursor < len(self._history)
            and self._history[self._cursor].type == "PATCH_APPLIED"
        ):
            self._config.update(self._history[self._cursor].payload)
            self._cursor += 1

    def config(self, key: str, default: Any = None) -> Any:
        """Read a config value, as patched by any fork up to this point."""
        self._apply_pending_patches()
        return self._config.get(key, default)

    # -- agent-facing operations --

    async def llm(self, prompt: str) -> str:
        self._apply_pending_patches()
        if self._replaying():
            event = self._replay_next("LLM_CALLED")
            if event.payload["prompt"] != prompt:
                raise ReplayDivergenceError(
                    event.seq,
                    f"LLM_CALLED({event.payload['prompt']!r})",
                    f"LLM_CALLED({prompt!r})",
                )
            return event.payload["response"]

        maybe_crash("before_call", self._cursor + 1)
        response = await self._llm.complete(prompt)
        maybe_crash("before_append", self._cursor + 1)
        self._record("LLM_CALLED", {"prompt": prompt, "response": response})
        maybe_crash("after_append", self._cursor + 1)
        return response

    async def side_effect(self, fn: Callable[[psycopg.Connection], Any]) -> Any:
        """Run `fn` (a write on this connection) exactly once across crashes."""
        self._apply_pending_patches()
        # Phase 1 -- intent: record "about to act", committed before we act.
        if self._replaying():
            seq = self._replay_next("TOOL_INTENT").seq
        else:
            seq = self._cursor + 1
            maybe_crash("before_intent", seq)
            self._record("TOOL_INTENT", {"key": f"{self.run_id}-{seq}"})
        key = f"{self.run_id}-{seq}"

        # Phase 2 -- completion: recorded already means done; otherwise act once.
        if self._replaying():
            return self._replay_next("TOOL_COMPLETED").payload["result"]

        maybe_crash("before_effect", seq + 1)
        result = self._perform_once(key, fn)
        maybe_crash("after_effect", seq + 1)
        return result

    def _perform_once(self, key: str, fn: Callable) -> Any:
        # If fenced, verify (and lock) the lease before acting, so a presumed-
        # dead worker does nothing at all. The effect, the dedupe key, and the
        # completion event then commit together.
        if self._lease_token is not None:
            held = self.conn.execute(
                "SELECT 1 FROM runs WHERE run_id = %s AND lease_token = %s FOR UPDATE",
                (self.run_id, self._lease_token),
            ).fetchone()
            if held is None:
                self.conn.rollback()
                raise StaleFenceError(self.run_id, self._lease_token)

        row = self.conn.execute(
            "SELECT result FROM completed_keys WHERE idempotency_key = %s", (key,)
        ).fetchone()
        if row is not None:
            result = row[0]  # already done in a past life; reuse the result
        else:
            result = fn(self.conn)
            self.conn.execute(
                "INSERT INTO completed_keys (idempotency_key, result) VALUES (%s, %s)",
                (key, Json(result)),
            )
        self.conn.execute(
            "INSERT INTO events (run_id, seq, type, payload, lease_token) "
            "VALUES (%s, %s, %s, %s, %s)",
            (self.run_id, self._cursor + 1, "TOOL_COMPLETED",
             Json({"key": key, "result": result}), self._lease_token or 0),
        )
        maybe_crash("before_commit", self._cursor + 1)
        self.conn.commit()
        self._cursor += 1
        return result
