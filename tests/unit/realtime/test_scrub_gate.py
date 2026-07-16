import pytest

import jarvis.realtime.scrub_gate as scrub_gate_module
from jarvis.brain.output_filter import ScrubResult
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.scrub_gate import ScrubHoldGate
from jarvis.speech.hangup import END_CALL_SIGNAL


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
async def test_split_filler_opener_can_complete_a_substantive_reply():
    """A temporary ``Let me think`` prefix must not abort streamed output."""
    gate = ScrubHoldGate(language="en")
    buffered = _chunk(8)

    display = [await gate.feed_transcript("Let me")]
    display.append(await gate.feed_transcript(" think"))
    assert gate.hard_leak_pending() is False
    assert await gate.push_audio(buffered) == []

    continuation = ", the benefits include stronger bones."
    display.append(await gate.feed_transcript(continuation))

    assert gate.hard_leak_pending() is False
    assert "".join(display) == f"Let me think{continuation}"
    assert gate.release_available() == [buffered]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param("**", id="markdown"),
        pytest.param("https://example.com", id="source-url"),
        pytest.param("As an AI.", id="self-reference"),
        pytest.param("I'm noting that down.", id="background-narration"),
        pytest.param(
            "If I understand correctly, yes.",
            id="echo-paraphrase",
        ),
        pytest.param("Great question.", id="filler-opener"),
        pytest.param("MCP", id="engineering-jargon"),
        pytest.param("Sir,", id="honorific"),
        pytest.param("\N{EM DASH}", id="dash"),
    ],
)
async def test_harmless_scrub_fragment_never_becomes_generic_error(
    fragment: str,
):
    gate = ScrubHoldGate(language="en")
    buffered = _chunk(8)

    assert await gate.feed_transcript(fragment) == fragment
    assert gate.hard_leak_pending() is False
    assert await gate.push_audio(buffered) == []

    continuation = " A substantive answer follows."
    assert await gate.feed_transcript(continuation) == continuation
    assert gate.hard_leak_pending() is False
    assert gate.release_available() == [buffered]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fragment", "expected_display"),
    [
        pytest.param(END_CALL_SIGNAL, "", id="end-call-control"),
        pytest.param("1", "one", id="number-spelling"),
    ],
)
async def test_other_non_blocking_scrub_actions_do_not_raise_generic_error(
    fragment: str,
    expected_display: str,
):
    gate = ScrubHoldGate(language="en")

    assert await gate.feed_transcript(fragment) == expected_display
    assert gate.hard_leak_pending() is False


@pytest.mark.asyncio
async def test_streamed_jargon_prefix_can_complete_a_user_facing_compound():
    """A partial ``MCP-Server`` transcript must not abort a clean reply."""
    gate = ScrubHoldGate(language="de")
    first = _chunk(4)
    jargon = _chunk(8)
    suffix = _chunk(12)

    opening = "Im Moment sind zwei"  # i18n-allow: German runtime transcript under test
    assert await gate.feed_transcript(opening) == opening
    assert await gate.push_audio(first) == [first]

    # Realtime providers may split a hyphenated user concept after ``MCP``.
    # The whole-utterance scrubber correctly preserves ``MCP-Server``, but the
    # incomplete delta alone is temporarily reduced to residue.
    assert await gate.feed_transcript(" MCP") == " MCP"
    assert gate.hard_leak_pending() is False
    assert await gate.push_audio(jargon) == [jargon]

    ending = "-Server verbunden."  # i18n-allow: German runtime transcript under test
    assert await gate.feed_transcript(ending) == ending
    assert gate.hard_leak_pending() is False
    assert await gate.push_audio(suffix) == [suffix]


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
@pytest.mark.parametrize(
    ("transcript", "expected_action"),
    [
        pytest.param(
            "Traceback (most recent call last):\n  File x\nValueError: y\n",
            "replaced_stacktrace",
            id="stacktrace",
        ),
        pytest.param("{'result': 'raw'}", "replaced_raw_repr", id="raw-repr"),
        pytest.param(
            "cmd /c start app",
            "replaced_shell_command",
            id="shell-command",
        ),
        pytest.param(
            'Before <tool_call>{"name":"spawn_worker"}</tool_call> after.',
            "removed_tool_json",
            id="tool-payload-with-prose",
        ),
    ],
)
async def test_actual_machine_leak_still_blocks_output(
    transcript: str,
    expected_action: str,
):
    gate = ScrubHoldGate(language="en")

    await gate.feed_transcript(transcript)

    assert gate.hard_leak_pending() is True
    assert expected_action in gate.hard_leak_actions()


def test_unclassified_scrub_action_fails_closed():
    result = ScrubResult(
        cleaned="Future output",
        actions=["future_unclassified_action"],
        fallback_used=False,
    )

    assert scrub_gate_module._is_hard_scrub_result(result) is True


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
