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


class _TimingOutStream:
    """Provider whose stream never produces a sentence within the timeout."""

    async def run(self, u: str, lang: str, *, persona_prompt: str) -> str | None:
        import asyncio

        await asyncio.sleep(10)
        return "too late"

    async def run_stream(
        self, u: str, lang: str, *, persona_prompt: str
    ) -> AsyncIterator[str]:
        import asyncio

        await asyncio.sleep(10)
        yield "too late"


def _gen_with_fallback(
    primary: object, fallback: AckGenerator, *, timeout_ms: int = 100
) -> AckGenerator:
    cfg = AckBrainConfig(timeout_ms=timeout_ms, suppress_if_brain_faster_than_ms=0)
    breaker = CircuitBreaker(threshold=3, cooldown_s=60)
    return AckGenerator(
        provider=primary, config=cfg, breaker=breaker, fallback=fallback  # type: ignore[arg-type]
    )


async def test_run_stream_fails_over_to_fallback_when_primary_times_out() -> None:
    # Live bug 2026-06-18 (session b34a4bba): the Gemini ack timed out (4 s)
    # while the Gemini deep brain was slow, and with no failover the user heard
    # 8 s of dead air and aborted. A SEPARATE fallback provider must speak
    # instead — realises the documented "Gemini primary, Grok fallback" design.
    fallback_gen = _gen(_StreamingFake(["Lass mich kurz nachschauen."]))
    gen = _gen_with_fallback(_TimingOutStream(), fallback_gen, timeout_ms=100)

    out = [s async for s in gen.run_stream("hallo", language="de")]

    assert any("nachschauen" in s for s in out)


async def test_run_stream_no_failover_when_primary_succeeds() -> None:
    # The fallback must NOT be invoked when the primary already spoke (no
    # double ack, no wasted second provider call).
    fb_provider = _StreamingFake(["Lass mich kurz nachschauen."])
    fallback_gen = _gen(fb_provider)
    gen = _gen_with_fallback(
        _StreamingFake(["Ich öffne den Browser."]), fallback_gen, timeout_ms=1500
    )

    out = [s async for s in gen.run_stream("hallo", language="de")]

    assert any("Browser" in s for s in out)
    assert not any("nachschauen" in s for s in out)  # fallback never invoked
    assert not fb_provider.deltas == []  # sanity: fallback was configured


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


async def test_run_stream_suppresses_an_unbacked_action_promise() -> None:
    provider = _StreamingFake([
        "Ich schaue gleich in dein Wiki und melde mich."  # i18n-allow: runtime output
    ])
    generator = _gen(provider)

    out = [
        sentence
        async for sentence in generator.run_stream(
            "What is in my Wiki?", language="de",
        )
    ]

    assert out == []
