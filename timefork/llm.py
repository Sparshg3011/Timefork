"""The one place Timefork talks to a real, paid model: the Week 5 showcase.

Everything else -- dev, tests, benchmarks, the kill -9 certificates -- runs on
the MockLLM. ClaudeLLM matches its interface exactly (async complete(prompt) ->
str, plus a .calls "bill"), so the same agent code runs against either brain
with no changes. On replay the recorded answer is read back, so the real model
is never called twice -- a crash resume costs zero tokens.

Needs the Anthropic SDK and a key:
    pip install -e ".[showcase]"
    export ANTHROPIC_API_KEY=sk-ant-...
"""

DEFAULT_MODEL = "claude-opus-4-8"

DEFAULT_SYSTEM = (
    "You are a careful customer-support assistant reviewing refund requests. "
    "Give a brief recommendation in 2-3 sentences: whether to approve and why. "
    "A human makes the final call, so be honest about edge cases."
)


class ClaudeLLM:
    """A real Claude brain, swappable for MockLLM in any agent."""

    def __init__(self, model: str = DEFAULT_MODEL, system: str = DEFAULT_SYSTEM) -> None:
        import anthropic  # optional dependency; only the showcase needs it

        self._client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        self._model = model
        self._system = system
        self.calls = 0  # the bill: how many times the real model was hit

    async def complete(self, prompt: str) -> str:
        self.calls += 1
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=self._system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in message.content if b.type == "text")
