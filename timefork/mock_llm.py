"""A fake LLM for development and tests.

It keeps the properties that make real LLMs hard to be durable around --
latency, nondeterminism, and a cost per call -- without the network or the
bill. `calls` counts every completion; crash tests assert it never exceeds
one call per step.
"""

import asyncio
import random


class ScriptExhaustedError(Exception):
    """A scripted MockLLM was asked for more responses than it was given."""


class MockLLM:
    """Three modes: scripted (exact responses, in order), deterministic
    (seed set: same seed + same prompt = same answer, across processes),
    and random (the default: a fresh answer every call)."""

    def __init__(
        self,
        script: list[str] | None = None,
        latency_s: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self.script = list(script) if script is not None else None
        self.latency_s = latency_s
        self.seed = seed
        self.calls = 0
        self._rng = random.Random()  # random mode only

    async def complete(self, prompt: str) -> str:
        if self.script is not None and self.calls >= len(self.script):
            raise ScriptExhaustedError(
                f"call {self.calls + 1} requested but script has "
                f"{len(self.script)} responses"
            )

        if self.latency_s > 0:
            await asyncio.sleep(self.latency_s)
        self.calls += 1

        if self.script is not None:
            return self.script[self.calls - 1]

        if self.seed is not None:
            # Seed with a string: process-stable. (Python's hash() is salted
            # per process and would quietly break cross-process comparisons.)
            rng = random.Random(f"{self.seed}:{prompt}")
        else:
            rng = self._rng

        return f"mock-{rng.getrandbits(48):012x}: a reply to {prompt!r}"
