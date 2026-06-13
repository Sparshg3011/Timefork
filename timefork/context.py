"""The replay context: record on a run's first life, replay on every life after.

An agent reaches the outside world only through ctx.llm(). On the first life
each call hits the model and is written to the diary. On a resumed life the
recorded answers are read back in order and the model is never called again.
"""

from typing import Any

import psycopg

from .events import append_event, read_events


class Context:
    """An agent's only door to non-determinism.

    Construction loads the run's diary. Each ctx.llm() call is the next command
    in a deterministic sequence, and command i always owns event seq i+1 --
    whether it is recorded for the first time or replayed from the diary.

    `llm` is anything with `async complete(prompt) -> str` (the mock today, a
    real model in Week 5).
    """

    def __init__(self, conn: psycopg.Connection, run_id: str, llm: Any) -> None:
        self.conn = conn
        self.run_id = run_id
        self._llm = llm
        self._history = read_events(conn, run_id)  # everything recorded so far
        self._cursor = 0                           # commands handled this life

    async def llm(self, prompt: str) -> str:
        i = self._cursor
        self._cursor += 1

        # Replay: this step is already in the diary. Read the answer back and
        # do not call the model -- the bill does not move.
        if i < len(self._history):
            return self._history[i].payload["response"]

        # Live: first time through. Call the model, then record the answer
        # before returning, so a crash one line later cannot lose it.
        response = await self._llm.complete(prompt)
        append_event(
            self.conn,
            self.run_id,
            i + 1,
            "LLM_CALLED",
            {"prompt": prompt, "response": response},
        )
        return response
