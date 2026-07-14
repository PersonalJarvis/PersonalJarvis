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
async def test_clean_transcript_preserves_provider_delta_boundaries():
    gate = ScrubHoldGate(language="en")
    raw_deltas = ["All", " right", ", ", "I", " can", " help", "."]

    display_deltas = [await gate.feed_transcript(delta) for delta in raw_deltas]

    assert "".join(display_deltas) == "All right, I can help."


@pytest.mark.asyncio
@pytest.mark.parametrize("dash", ["\N{EM DASH}", "\N{EN DASH}", " -- "])
async def test_isolated_streaming_dash_does_not_false_cancel_output(dash: str):
    gate = ScrubHoldGate(language="en")
    first = _chunk(4)
    continuation = _chunk(8)

    await gate.feed_transcript("A safe opening clause")
    assert await gate.push_audio(first) == [first]

    display = await gate.feed_transcript(dash)
    assert display == dash
    assert gate.hard_leak_pending() is False
    assert await gate.push_audio(continuation) == [continuation]

    await gate.feed_transcript(" followed by a safe continuation.")
    assert gate.hard_leak_pending() is False


@pytest.mark.asyncio
async def test_leading_streaming_dash_waits_for_meaningful_transcript():
    gate = ScrubHoldGate(language="en")
    buffered = _chunk(4)

    assert await gate.feed_transcript("\N{EM DASH}") == "\N{EM DASH}"
    assert await gate.push_audio(buffered) == []
    await gate.feed_transcript("A safe continuation follows.")

    assert gate.hard_leak_pending() is False
    assert gate.release_available() == [buffered]


@pytest.mark.asyncio
async def test_scrubbed_delta_keeps_its_original_leading_separator():
    gate = ScrubHoldGate(language="en")

    display = await gate.feed_transcript(" **ready**")

    assert display == " ready"


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


@pytest.mark.asyncio
async def test_hard_leak_split_across_transcript_deltas_is_caught():
    gate = ScrubHoldGate(language="en")

    await gate.feed_transcript("Trace")
    await gate.push_audio(_chunk(4))
    display = await gate.feed_transcript(
        "back (most recent call last):\n  File x\nValueError: y\n\n"
    )

    assert display == gate.fallback_phrase()
    assert gate.hard_leak_pending() is True
    assert gate.release_available() == []


@pytest.mark.asyncio
async def test_missing_transcript_fails_closed_instead_of_releasing_audio():
    gate = ScrubHoldGate(language="en")
    await gate.push_audio(_chunk(4))

    assert gate.release_available() == []
    assert gate.fail_closed() is True
    assert gate.hard_leak_pending() is True
    assert gate.release_available() == []


@pytest.mark.asyncio
async def test_clean_transcript_covers_buffered_tail_at_response_boundary():
    gate = ScrubHoldGate(language="en")
    first = _chunk(4)
    tail = _chunk(8)

    await gate.feed_transcript("A complete safe response.")
    assert await gate.push_audio(first) == [first]
    assert await gate.push_audio(tail) == []

    assert gate.finalize() == [tail]
    assert gate.hard_leak_pending() is False


@pytest.mark.asyncio
async def test_untranscribed_audio_buffer_is_bounded_by_audio_duration():
    gate = ScrubHoldGate(language="en")

    await gate.push_audio(_chunk(2_400))  # 100 ms at 24 kHz.

    assert gate.fail_if_pending_exceeds(50) is True
    assert gate.hard_leak_pending() is True
    assert gate.finalize() == []

@pytest.mark.asyncio
async def test_hard_leak_exposes_detector_actions_for_diagnosis():
    """BUG-056: the 15:13 abort was undiagnosable — only the generic reason
    string survived. The gate must name WHICH detectors tripped (safe
    metadata, never the flagged content), and reset them on drain()."""
    gate = ScrubHoldGate(language="en")
    assert gate.hard_leak_actions() == ()
    await gate.feed_transcript(
        "Traceback (most recent call last):\n  File x\nValueError: y\n\n"
    )
    assert gate.hard_leak_pending() is True
    actions = gate.hard_leak_actions()
    assert actions, "a hard leak must carry at least one detector name"
    # Detector names only — the flagged content itself must not appear.
    assert all("Traceback" not in a and "ValueError" not in a for a in actions)
    gate.drain()
    assert gate.hard_leak_actions() == ()


@pytest.mark.asyncio
async def test_fail_closed_reports_missing_transcript_action():
    gate = ScrubHoldGate(language="en")
    await gate.push_audio(_chunk(4))
    assert gate.fail_closed() is True
    assert gate.hard_leak_actions() == ("no_transcript",)
