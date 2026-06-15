"""Regression guards for the STT-stability-probe cross-turn state leak.

Root cause (2026-05-25): the VAD stability probe owns two pieces of state
whose lifetime is the *session* but whose meaning is the *turn* — the
pipeline ``_probe_in_flight`` latch and the VAD ``_endpoint_requested`` flag
set via ``request_endpoint()``. With a fast in-process Whisper the probe
always returned inside its own turn, so neither ever leaked. The
"lightweight wake" path re-pointed the probe at a cloud utterance-STT
(``groq-api``) whose round-trip can outlive the turn that spawned it. A probe
that completes one or more turns late then forces a stale endpoint onto the
*next* utterance, which the VAD discards as a ``false_start`` — the turn is
silently dropped and the user gets no answer ("I finished speaking but it
didn't submit", intermittent).

The fix tags every probe with a monotonic ``_probe_generation`` captured at
spawn; a probe whose generation no longer matches is dropped before it can
touch turn state. ``_reset_probe_state`` (called at every turn boundary) bumps
the generation and releases the in-flight latch.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.protocols import Transcript
from jarvis.speech.pipeline import SpeechPipeline


class _RecordingVad:
    """Minimal VAD double that records ``request_endpoint`` calls."""

    def __init__(self) -> None:
        self.request_endpoint_calls = 0

    def request_endpoint(self) -> None:
        self.request_endpoint_calls += 1


class _GatedSTT:
    """STT whose ``transcribe_pcm`` blocks until ``gate`` is set.

    Returns an empty transcript so the probe takes the ``tail_is_empty`` path
    (Signal 1) and would call ``request_endpoint`` unless suppressed.
    """

    def __init__(self) -> None:
        self.gate = asyncio.Event()

    async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
        await self.gate.wait()
        return Transcript(text="", language="de", confidence=0.0, is_partial=False)


class _InstantSTT:
    def __init__(self, text: str = "") -> None:
        self._text = text

    async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
        return Transcript(text=self._text, language="de", confidence=0.0, is_partial=False)


def _make_probe_pipe(probe_stt: object, vad: _RecordingVad) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._stt = None
    pipe._probe_stt = probe_stt
    pipe._vad = vad
    pipe._probe_in_flight = False
    pipe._probe_generation = 0
    pipe._probe_last_text = ""
    pipe._probe_live_text = ""
    pipe._probe_stable_count = 0
    pipe._probe_required_stable = 1
    pipe._probe_empty_count = 0
    pipe._probe_required_empty = 2
    pipe._probe_min_text_len = 4
    return pipe


def _probe_tasks() -> list[asyncio.Task]:
    return [
        t for t in asyncio.all_tasks() if t.get_name() == "stt-stability-probe" and not t.done()
    ]


@pytest.mark.asyncio
async def test_stale_probe_does_not_leak_endpoint_into_next_turn() -> None:
    """A probe whose turn ended before it completed must NOT force an endpoint.

    This is the core regression: a slow cloud probe returning a turn late
    used to call ``request_endpoint`` into the next turn → ``false_start`` →
    silently dropped utterance.
    """
    vad = _RecordingVad()
    gated = _GatedSTT()
    pipe = _make_probe_pipe(gated, vad)

    # Turn A: spawn the probe; it blocks inside transcribe_pcm.
    pipe._on_vad_probe(b"\x00\x00" * 256)
    await asyncio.sleep(0)  # let the task start and block on the gate
    tasks = _probe_tasks()
    assert tasks, "probe task should have been spawned"

    # Turn A ends before the probe returns (e.g. VAD silence endpoint fired).
    # ``_on_vad_endpoint`` delegates to ``_reset_probe_state``; call it
    # directly to keep the test off the turn-state-machine machinery.
    pipe._reset_probe_state()  # bumps generation + releases in-flight latch

    # Now the slow probe finally returns — into turn B.
    gated.gate.set()
    await asyncio.gather(*tasks)

    assert vad.request_endpoint_calls == 0, "stale probe leaked request_endpoint into the next turn"


@pytest.mark.asyncio
async def test_probe_forces_endpoint_within_same_turn() -> None:
    """Positive control: an in-turn SUSTAINED empty-tail probe still forces the
    endpoint.

    Guards against 'fixing' the leak by simply disabling the probe — the
    speaker-bleed backstop must keep working within the live turn. A single
    empty tail now defers (a quiet mumble mid-speech is indistinguishable from
    bleed on one probe — the "och ha..." cut, 2026-06-14); the persistent empty
    run forces.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text=""), vad)

    pipe._on_vad_probe(b"\x00\x00" * 256)
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 0  # single empty tail defers
    pipe._on_vad_probe(b"\x00\x00" * 256)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 1  # sustained empty → force


@pytest.mark.asyncio
async def test_reset_probe_state_releases_in_flight_and_bumps_generation() -> None:
    """Turn boundary clears the in-flight latch and advances the generation.

    Without releasing ``_probe_in_flight`` the next turn's probes are blocked
    by the stale latch (a second, independent face of the same leak).
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text=""), vad)
    pipe._probe_in_flight = True
    gen_before = pipe._probe_generation

    pipe._reset_probe_state()

    assert pipe._probe_in_flight is False
    assert pipe._probe_generation == gen_before + 1
