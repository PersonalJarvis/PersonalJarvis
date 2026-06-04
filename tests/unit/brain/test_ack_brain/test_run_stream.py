"""AckGenerator.run_stream — early sentence yield + safe fallback (Wave 3).

Streaming must surface the first complete ack sentence as its own yield (so the
pipeline can speak it immediately), and must fall back to the proven run() path
whenever the provider has no run_stream or the stream produces nothing — so a
broken stream never silences the ack (BUG-007/BUG-020 class).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from jarvis.brain.ack_brain import AckGenerator, CircuitBreaker
from jarvis.brain.ack_brain.config import AckBrainConfig


def _gen(provider: object) -> AckGenerator:
    cfg = AckBrainConfig(timeout_ms=1500, suppress_if_brain_faster_than_ms=0)
    breaker = CircuitBreaker(threshold=3, cooldown_s=60)
    return AckGenerator(provider=provider, config=cfg, breaker=breaker)  # type: ignore[arg-type]


class _StreamingFake:
    """Provider whose run_stream emits two sentences across several deltas."""

    def __init__(self, deltas: list[str]) -> None:
        self.deltas = deltas

    async def run(self, utterance: str, language: str, *, persona_prompt: str) -> str | None:
        return "".join(self.deltas).strip() or None

    async def run_stream(
        self, utterance: str, language: str, *, persona_prompt: str
    ) -> AsyncIterator[str]:
        for d in self.deltas:
            yield d


async def test_run_stream_yields_each_sentence_separately() -> None:
    prov = _StreamingFake(["Lass mich kurz ", "schauen. ", "Ich öffne ", "den Browser."])
    gen = _gen(prov)

    out = [s async for s in gen.run_stream("öffne browser", language="de")]

    assert len(out) == 2
    assert "schauen" in out[0]
    assert "Browser" not in out[0]  # the first yield is sentence 1 only
    assert "Browser" in out[1]


async def test_run_stream_falls_back_to_run_without_streaming_support() -> None:
    class _RunOnly:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, u: str, lang: str, *, persona_prompt: str) -> str | None:
            self.calls += 1
            return "Lass mich kurz schauen."

    prov = _RunOnly()
    gen = _gen(prov)

    out = [s async for s in gen.run_stream("hallo", language="de")]

    assert out == ["Lass mich kurz schauen."]
    assert prov.calls == 1  # the proven run() path was used


async def test_run_stream_empty_stream_falls_back_to_run() -> None:
    class _EmptyStream:
        async def run(self, u: str, lang: str, *, persona_prompt: str) -> str | None:
            return "Lass mich kurz schauen."

        async def run_stream(
            self, u: str, lang: str, *, persona_prompt: str
        ) -> AsyncIterator[str]:
            return
            yield  # noqa: unreachable — makes this an async generator that yields nothing

    gen = _gen(_EmptyStream())

    out = [s async for s in gen.run_stream("hallo", language="de")]

    assert out == ["Lass mich kurz schauen."]
