"""Tests for the mock LLM's three modes and its call counter."""

import asyncio

import pytest

from timefork.mock_llm import MockLLM, ScriptExhaustedError


def test_scripted_responses_in_order_then_exhausted():
    llm = MockLLM(script=["one", "two"])
    assert asyncio.run(llm.complete("a")) == "one"
    assert asyncio.run(llm.complete("b")) == "two"
    with pytest.raises(ScriptExhaustedError):
        asyncio.run(llm.complete("c"))
    assert llm.calls == 2  # the refused call is not billed


def test_deterministic_mode_is_stable_across_instances():
    a = MockLLM(seed=7)
    b = MockLLM(seed=7)
    assert asyncio.run(a.complete("p")) == asyncio.run(b.complete("p"))
    # A different prompt or a different seed changes the answer.
    assert asyncio.run(a.complete("q")) != asyncio.run(b.complete("p"))
    assert asyncio.run(MockLLM(seed=8).complete("p")) != asyncio.run(
        MockLLM(seed=7).complete("p")
    )


def test_random_mode_differs_and_counts_calls():
    llm = MockLLM()
    first = asyncio.run(llm.complete("p"))
    second = asyncio.run(llm.complete("p"))
    assert first != second
    assert llm.calls == 2
