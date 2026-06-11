"""The mock LLM: our stand-in for a real model.

Shows the three modes (scripted, random, deterministic), the latency knob,
and the call counter -- the "bill" that crash tests assert on.

Run with: python examples/02_mock_llm.py
"""

import asyncio
import time

from timefork.mock_llm import MockLLM, ScriptExhaustedError


async def main() -> None:
    # Scripted: full control, for tests that need an exact conversation.
    scripted = MockLLM(script=["plan: read the diary", "done"])
    print("scripted #1:", await scripted.complete("what next?"))
    print("scripted #2:", await scripted.complete("and now?"))
    try:
        await scripted.complete("one more?")
    except ScriptExhaustedError as exc:
        print("scripted #3 refused:", exc)

    # Random (default): a fresh answer every call, like a creative model.
    # This is what makes replay provable -- re-executing a step instead of
    # replaying it would visibly change the output.
    creative = MockLLM()
    print("\nrandom call A:", await creative.complete("same prompt"))
    print("random call B:", await creative.complete("same prompt"))

    # Deterministic: same seed + same prompt = same answer, even in another
    # process tomorrow. Lets crash tests compare runs against a baseline.
    a = MockLLM(seed=42)
    b = MockLLM(seed=42)
    answer_a = await a.complete("same prompt")
    answer_b = await b.complete("same prompt")
    print("\nseed 42, instance A:", answer_a)
    print("seed 42, instance B:", answer_b)
    print("identical:", answer_a == answer_b)

    # Latency and the bill.
    slow = MockLLM(latency_s=0.2)
    start = time.perf_counter()
    await slow.complete("think hard")
    print(f"\none slow call took {time.perf_counter() - start:.2f}s")
    print("slow llm's bill (calls):", slow.calls)


if __name__ == "__main__":
    asyncio.run(main())
