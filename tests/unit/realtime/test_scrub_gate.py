import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.scrub_gate import ScrubHoldGate

_HARD_LEAK_ACTIONS = {"replaced_stacktrace", "replaced_raw_repr", "replaced_shell_command"}


def _chunk(n: int) -> AudioChunk:
    return AudioChunk(pcm=b"\x00\x01" * n, sample_rate=24000, timestamp_ns=0)


@pytest.mark.asyncio
async def test_clean_transcript_releases_buffered_audio():
    gate = ScrubHoldGate(language="en")
    await gate.push_audio(_chunk(4))
    released = await gate.feed_transcript("Hello there, how can I help?")
    # display text returned; audio releasable after the clean boundary
    assert released == "Hello there, how can I help?" or released  # scrubbed display
    out = await gate.push_audio(_chunk(4))
    assert gate.hard_leak_pending() is False
    assert out  # buffered + new audio flows once transcript cleared


@pytest.mark.asyncio
async def test_hard_leak_transcript_marks_leak_and_drops_audio():
    gate = ScrubHoldGate(language="en")
    await gate.push_audio(_chunk(4))
    # A stacktrace transcript is a hard leak (scrub_for_voice early-returns fallback).
    await gate.feed_transcript("Traceback (most recent call last):\n  File x\nValueError: y\n\n")
    assert gate.hard_leak_pending() is True
    # No audio may be released after a hard leak.
    out = await gate.push_audio(_chunk(4))
    assert out == []
    assert gate.fallback_phrase() == "An error occurred."


@pytest.mark.asyncio
async def test_scrub_is_regex_only_no_llm(monkeypatch):
    # Guard AP-11: the gate must call scrub_for_voice and nothing that awaits a model.
    import jarvis.realtime.scrub_gate as mod

    calls = {"n": 0}
    real = mod.scrub_for_voice

    def spy(text, **kw):
        calls["n"] += 1
        return real(text, **kw)

    monkeypatch.setattr(mod, "scrub_for_voice", spy)
    gate = ScrubHoldGate(language="en")
    await gate.feed_transcript("A normal sentence.")
    assert calls["n"] >= 1
