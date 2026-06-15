"""Deterministic crash injection for tests.

`maybe_crash(name, seq)` is a no-op in a normal run. When the environment names
this point (TIMEFORK_CRASH_AT, optionally pinned to a step with
TIMEFORK_CRASH_AT_SEQ), it hard-kills the process with SIGKILL -- the same
uncatchable death as `kill -9`: no `finally`, no `atexit`, no buffer flush that
could paper over a missing write.
"""

import os
import signal


def maybe_crash(name: str, seq: int | None = None) -> None:
    if os.environ.get("TIMEFORK_CRASH_AT") != name:
        return
    pinned = os.environ.get("TIMEFORK_CRASH_AT_SEQ")
    if pinned is not None and str(seq) != pinned:
        return
    os.kill(os.getpid(), signal.SIGKILL)
