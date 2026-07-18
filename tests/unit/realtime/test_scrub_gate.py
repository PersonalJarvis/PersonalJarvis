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
    """A temporary ``Let me think`` prefix must not abort streamed output.

    The 2000 ms chunk exceeds what the few prefix chars fund, so it must
    buffer until the substantive continuation arrives — never abort.
    """
    gate = ScrubHoldGate(language="en")
    buffered = _chunk(48_000)

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
    """Audio beyond the vetted coverage budget still gets held.

    Regression for the sticky-_cleared defect, updated for the BUG-069
    coverage budget: a short clean first segment funds only its own
    estimated spoken duration, so a later segment's audio that outruns that
    budget (and whose own transcript may be a hard leak) must buffer until
    its transcript is scrubbed — and must be dropped when that transcript
    turns out to be a stacktrace.
    """
    gate = ScrubHoldGate(language="en")

    # Segment 1: buffer while no transcript yet, then a clean transcript
    # clears the gate and the next push_audio() releases.
    await gate.push_audio(_chunk(4))
    await gate.feed_transcript("A normal clean sentence.")
    out1 = await gate.push_audio(_chunk(4))
    assert out1  # segment 1 audio released

    # Segment 2: 2000 ms of audio arrives before ITS transcript — far more
    # than the 24-char first segment can fund. It must buffer, not release.
    out2 = await gate.push_audio(_chunk(48_000))
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
    # 2000 ms — beyond what the 25-char transcript's coverage budget funds,
    # so it must wait for the response boundary.
    tail = _chunk(48_000)

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
async def test_en_bloc_upfront_transcript_keeps_audio_flowing():
    """BUG-069 core fix: an up-front whole-reply transcript funds ALL audio.

    A Gemini Live probe (2026-07-17) delivered the entire reply transcript as
    ONE delta alongside the first audio chunk, then streamed audio only. The
    pre-budget gate released exactly one chunk per transcript delta, so every
    later chunk starved until turn end — the audible word-splitting stutter
    and 5-7 s mid-reply holes. With the coverage budget, each chunk must flow
    the moment it arrives.
    """
    gate = ScrubHoldGate(language="en")
    transcript = (
        "A perfectly ordinary answer about the weather today, spoken in "
        "several unhurried sentences that together run far longer than the "
        "audio pushed below, arriving complete before nearly all its audio."
    )

    await gate.feed_transcript(transcript)
    assert gate.release_available() == []  # nothing buffered yet

    for _ in range(10):
        chunk = _chunk(9_600)  # 400 ms at 24 kHz — a realistic Gemini chunk
        assert await gate.push_audio(chunk) == [chunk]
    assert gate.pending_audio_ms == 0.0


@pytest.mark.asyncio
async def test_budget_exhaustion_buffers_audio_beyond_vetted_text():
    """Fail-closed core: audio outrunning the vetted text still buffers."""
    gate = ScrubHoldGate(language="en")

    await gate.feed_transcript("Okay.")  # funds ~275 ms
    first = _chunk(9_600)  # 400 ms
    assert await gate.push_audio(first) == [first]  # clean-delta release

    second = _chunk(9_600)
    assert await gate.push_audio(second) == []  # budget spent: buffer

    await gate.feed_transcript(" The rest of the sentence arrives late.")
    assert gate.release_available() == [second]


@pytest.mark.asyncio
async def test_clean_delta_credit_still_releases_one_chunk_beyond_budget():
    """The budget redesign must never be stricter than the old gate.

    When a clean delta arrives with nothing buffered, the pre-budget gate
    preserved its credit and released exactly one later chunk regardless of
    size. That parity must survive: the first chunk after such a delta flows
    even though the tiny transcript cannot fund its duration — and only one.
    """
    gate = ScrubHoldGate(language="en")

    await gate.feed_transcript("Hi.")  # funds only ~165 ms
    assert gate.release_available() == []  # nothing buffered: credit survives

    first = _chunk(9_600)  # 400 ms — larger than the funded estimate
    assert await gate.push_audio(first) == [first]  # legacy credit release
    assert await gate.push_audio(_chunk(9_600)) == []  # credit is one-shot


@pytest.mark.asyncio
async def test_residue_only_transcript_never_activates_the_budget():
    """A turn whose aggregate transcript is still residue funds nothing."""
    gate = ScrubHoldGate(language="en")

    await gate.feed_transcript("Great question.")  # residue: filler opener
    assert await gate.push_audio(_chunk(9_600)) == []

    await gate.feed_transcript(" Bones need calcium and daily movement.")
    assert gate.release_available()  # aggregate turned clean: audio flows


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


class _FakeClock:
    """Deterministic stand-in for the gate's ``time`` module."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(scrub_gate_module, "time", fake)
    return fake


@pytest.mark.asyncio
async def test_lagging_transcript_backlog_flows_after_grace(clock):
    """BUG-080: a mid-reply transcription stall must not freeze the voice.

    Gemini Live's output transcription can fall 3-22 s behind its audio
    (live 2026-07-17 20:04: a 4.9 s mid-word hole). Once the aggregate
    transcript has been clean, a backlog held past the grace window flows
    even though its own transcript has not arrived yet.
    """
    gate = ScrubHoldGate(language="en")

    await gate.feed_transcript("Okay.")  # clean: activates coverage
    first = _chunk(9_600)  # 400 ms at 24 kHz
    assert await gate.push_audio(first) == [first]

    second = _chunk(9_600)
    assert await gate.push_audio(second) == []  # budget spent: buffers

    clock.now += 0.5  # past the 400 ms grace, transcript still absent
    third = _chunk(9_600)
    assert await gate.push_audio(third) == [second, third]
    assert gate.pending_audio_ms == 0.0
    assert gate.hard_leak_pending() is False


@pytest.mark.asyncio
async def test_no_grace_release_before_first_clean_transcript(clock):
    """The turn opening stays strictly fail-closed — grace never applies."""
    gate = ScrubHoldGate(language="en")

    assert await gate.push_audio(_chunk(9_600)) == []
    clock.now += 10.0
    assert await gate.push_audio(_chunk(9_600)) == []
    assert gate.fail_closed() is True


@pytest.mark.asyncio
async def test_residue_only_transcript_gets_no_grace_release(clock):
    """A turn whose aggregate transcript is still residue funds no grace."""
    gate = ScrubHoldGate(language="en")

    await gate.feed_transcript("Great question.")  # residue: filler opener
    assert await gate.push_audio(_chunk(9_600)) == []
    clock.now += 10.0
    assert await gate.push_audio(_chunk(9_600)) == []


@pytest.mark.asyncio
async def test_hard_leak_after_grace_release_still_blocks_further_audio(clock):
    """The grace release must not weaken the hard-leak kill switch."""
    gate = ScrubHoldGate(language="en")

    await gate.feed_transcript("Okay.")
    assert await gate.push_audio(_chunk(9_600))
    assert await gate.push_audio(_chunk(9_600)) == []
    clock.now += 0.5
    assert await gate.push_audio(_chunk(9_600))  # grace release happened

    await gate.feed_transcript(
        "Traceback (most recent call last):\n  File x\nValueError: y\n\n"
    )
    assert gate.hard_leak_pending() is True
    assert await gate.push_audio(_chunk(9_600)) == []
    assert gate.finalize() == []
