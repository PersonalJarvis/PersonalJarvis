import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.scrub_gate import ScrubHoldGate


def _chunk(n: int) -> AudioChunk:
    return AudioChunk(pcm=b"\x00\x01" * n, sample_rate=24000, timestamp_ns=0)


@pytest.mark.asyncio
async def test_clean_transcript_releases_buffered_audio():
    gate = ScrubHoldGate(language="en")
    await gate.push_audio(_chunk(4))
    released = await gate.feed_transcript("Hello there, how can I help?")
    # scrub_for_voice leaves this clean sentence unchanged
    assert released == "Hello there, how can I help?"
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


@pytest.mark.asyncio
async def test_later_segment_leak_is_caught_after_a_clean_first_segment():
    """_cleared must be one-shot: a later segment's audio still gets held.

    Regression for the sticky-_cleared defect: after the first clean
    transcript in a turn released `_cleared = True` and never reset it, so
    every later push_audio() released immediately without buffering — audio
    for a later segment (which may contain a hard leak) could reach the
    speaker before its own transcript was scrubbed.
    """
    gate = ScrubHoldGate(language="en")

    # Segment 1: buffer while no transcript yet, then a clean transcript
    # clears the gate and the next push_audio() releases.
    await gate.push_audio(_chunk(4))
    await gate.feed_transcript("A normal clean sentence.")
    out1 = await gate.push_audio(_chunk(4))
    assert out1  # segment 1 audio released

    # Segment 2: audio arrives before ITS transcript. If _cleared were still
    # sticky from segment 1, this would release immediately (the bug).
    out2 = await gate.push_audio(_chunk(4))
    assert out2 == []  # must be buffered, not released

    # Segment 2's transcript turns out to be a hard leak (a real stacktrace).
    await gate.feed_transcript(
        "Traceback (most recent call last):\n  File x\nValueError: y\n\n"
    )
    assert gate.hard_leak_pending() is True

    # Segment 2's audio must never be released.
    out3 = await gate.push_audio(_chunk(4))
    assert out3 == []
