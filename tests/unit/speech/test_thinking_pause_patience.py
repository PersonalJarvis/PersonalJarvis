"""Regression guards: a thinking pause must not end the voice turn.

Root cause (reported 2026-05-25): the STT stability probe ends the turn the
moment its tail transcribes to empty/short via ``request_endpoint()`` — which
bypasses the ``silence_ms`` guard entirely (``vad.py`` endpoint trigger ``(c)``).
A genuine *thinking pause* produces exactly the same empty tail as "the user is
done", so the user gets cut off mid-thought ("I pause to think and it submits").

The probe's empty/stable signal only exists to defeat **speaker bleed**: loud
music/TV that keeps Silero reporting "speech" so the natural silence endpoint
never fires. The discriminator is tail energy: a *loud* empty tail is speaker
bleed (force the endpoint, silence will never come); a *quiet* empty tail is a
genuine pause (defer to ``silence_ms`` so the user keeps the floor).

These tests pin that contract. They use the same reusable calibration as the
per-frame relative-silence gate, so ``tail_loud=False`` is guaranteed to mean
the silence endpoint *will* fire — there is no hang risk in deferring.
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
    pipe._probe_min_text_len = 4
    return pipe


def _probe_tasks() -> list[asyncio.Task]:
    return [
        t for t in asyncio.all_tasks() if t.get_name() == "stt-stability-probe" and not t.done()
    ]


@pytest.mark.asyncio
async def test_quiet_empty_tail_does_not_force_endpoint() -> None:
    """A genuine thinking pause (quiet, empty tail) must NOT force the endpoint.

    This is the reported bug: the probe used to call ``request_endpoint`` on any
    empty tail, bypassing ``silence_ms``, so a pause cut the user off. With a
    quiet tail the probe must defer to the natural silence endpoint.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text=""), vad)

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=False)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "quiet empty tail (thinking pause) forced an endpoint — user cut off"
    )


@pytest.mark.asyncio
async def test_loud_empty_tail_still_forces_endpoint() -> None:
    """Positive control: a loud empty tail is speaker bleed → still force endpoint.

    Guards against 'fixing' the pause bug by neutering the probe — the
    speaker-bleed backstop (where the silence endpoint can never fire) must
    keep working.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text=""), vad)

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 1, (
        "loud empty tail (speaker bleed) failed to force the endpoint"
    )


@pytest.mark.asyncio
async def test_quiet_stable_tail_does_not_force_endpoint() -> None:
    """Signal 2 (stable tail) must also defer when the tail is quiet.

    A short trailing word that sits quietly in the tail and repeats across two
    probes used to force an endpoint after ~1.3 s. When quiet, defer to
    ``silence_ms`` instead — the user may still be mid-thought.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="und dann"), vad)
    pipe._probe_last_text = "und dann"  # already seen once → next probe is "stable"

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=False)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "quiet stable tail forced an endpoint — user cut off mid-thought"
    )


@pytest.mark.asyncio
async def test_loud_stable_tail_still_forces_endpoint() -> None:
    """Positive control for Signal 2: a loud stable tail is bleed → force."""
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="background hum"), vad)
    pipe._probe_last_text = "background hum"

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 1, (
        "loud stable tail (speaker bleed) failed to force the endpoint"
    )
