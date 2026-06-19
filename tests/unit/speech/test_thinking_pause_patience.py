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
        self.extend_calls: list[int] = []

    def request_endpoint(self) -> None:
        self.request_endpoint_calls += 1

    def extend_silence_window(self, total_ms: int) -> None:
        self.extend_calls.append(total_ms)


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
    pipe._probe_required_stable = 2  # production default — no force on one reading
    pipe._probe_empty_count = 0
    pipe._probe_required_empty = 2
    pipe._probe_seen_real_speech = False
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
async def test_loud_empty_tail_defers_until_sustained() -> None:
    """A SINGLE loud empty tail must NOT force; only a sustained one does.

    Live recurrence 2026-06-14 (turn 2): the user said a quiet hesitation
    ("och ha...") that Whisper rendered as 'um' (empty) while still acoustically
    active — ``silence_ms=0``, no pause detected. The probe forced ``stt_stable``
    on that single empty reading and cut the user off mid-thought. A brief
    mumble/half-formed syllable that transcribes empty is indistinguishable from
    speaker bleed on ONE probe, so the empty signal must persist (like the
    stable signal) before forcing: a transient miss defers and keeps the floor;
    only sustained emptiness (real bleed, where the silence endpoint can never
    fire) forces.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text=""), vad)

    # First loud empty probe → defer (the user may still be speaking).
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 0, (
        "a single loud empty tail forced the endpoint — a brief mumble/"
        "hesitation (silence_ms=0) cuts the user off mid-thought"
    )

    # Second consecutive loud empty probe → sustained → force (speaker-bleed
    # backstop, where the natural silence endpoint can never fire).
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 1, (
        "a sustained loud empty tail (speaker bleed) failed to force the endpoint"
    )


@pytest.mark.asyncio
async def test_intervening_speech_resets_empty_tail_run() -> None:
    """A real word between two empty probes must reset the empty-run, so the
    user who mumbles, then speaks clearly, then mumbles again is never force-cut
    on a stale empty count. Only CONSECUTIVE empty tails accumulate toward the
    force."""
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text=""), vad)

    # One empty probe → count 1.
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())
    # The user speaks a real word → non-empty tail → empty-run must reset.
    pipe._probe_stt = _InstantSTT(text="Melbourne")
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())
    # Another single empty probe → count back to 1, NOT 2 → must still defer.
    pipe._probe_stt = _InstantSTT(text="")
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "empty-tail count was not reset by intervening real speech — the user "
        "was force-cut on a stale empty run"
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

    # Two quiet stable probes reach the persistence threshold; the quiet tail
    # must still defer (the user may be mid-thought), never force.
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=False)
    await asyncio.gather(*_probe_tasks())
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=False)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "quiet stable tail forced an endpoint — user cut off mid-thought"
    )


@pytest.mark.asyncio
async def test_loud_stable_tail_still_forces_endpoint() -> None:
    """Positive control for Signal 2: a loud stable tail is speaker bleed → force.

    Updated 2026-06-15: stable now requires the same 2-probe persistence as the
    empty/boilerplate path — a single loud stable reading no longer forces (it
    cut the user off mid-sentence). The text is deliberately NOT syntactically
    open-ended, so the trailed-off guard does not apply and the bleed still ends
    the turn after persistence.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="background hum"), vad)
    pipe._probe_last_text = "background hum"  # already seen → next probe is "stable"

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # stable 1/2 → defer
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 0, (
        "a single loud stable tail forced — stable must now persist 2 probes"
    )

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # stable 2/2 → force
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 1, (
        "a sustained loud stable tail (speaker bleed) failed to force the endpoint"
    )


@pytest.mark.asyncio
async def test_hallucination_tail_after_real_speech_does_not_force() -> None:
    """The live recurrence (2026-06-15 16:54): after the user has already
    produced real speech, a loud tail that Whisper mis-decodes as boilerplate
    must NOT force the endpoint.

    Log proof: the probe correctly deferred 'can you help me?' (real speech),
    then 0.6 s later faster-whisper rendered the loud, still-ongoing-speech tail
    as 'thank you for your help.' (conf 0.43). That matched
    ``_STT_HALLUCINATION_RE`` and the probe force-endpointed *immediately* at
    silence_ms=320 — cutting the user off mid-sentence. Once real speech is on
    the record, a boilerplate tail is a mis-transcription of ongoing speech, not
    deterministic speaker bleed: it must defer like a loud empty tail.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="can you help me"), vad)

    # Probe 1: real speech → registers that the user holds the floor.
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 0

    # Probe 2: Whisper mis-transcribes the loud ongoing-speech tail as boilerplate.
    pipe._probe_stt = _InstantSTT(text="thank you for your help.")
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "a boilerplate tail after real speech force-cut the user mid-sentence"
    )


@pytest.mark.asyncio
async def test_sustained_boilerplate_after_real_speech_still_forces() -> None:
    """The deadlock-breaker survives: if loud boilerplate genuinely PERSISTS
    after the user spoke (real bleed starts mid/after the turn), it still forces
    — just after the same 2-probe persistence as an empty tail, not on probe 1.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="can you help me"), vad)

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # real → flag set
    await asyncio.gather(*_probe_tasks())

    pipe._probe_stt = _InstantSTT(text="thank you for your help.")
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # boilerplate 1/2 → defer
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 0

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # boilerplate 2/2 → force
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 1, (
        "sustained loud boilerplate after real speech never finalized the turn"
    )


@pytest.mark.asyncio
async def test_real_speech_resets_boilerplate_defer_run() -> None:
    """Intervening real speech resets the boilerplate-defer run, so a user who
    speaks, gets one mis-transcription, then keeps talking is never force-cut on
    a stale count (the boilerplate path shares the empty-tail counter)."""
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="can you help me"), vad)

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # real → flag set
    await asyncio.gather(*_probe_tasks())

    pipe._probe_stt = _InstantSTT(text="thank you for your help.")
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # boilerplate 1/2
    await asyncio.gather(*_probe_tasks())

    pipe._probe_stt = _InstantSTT(text="and then i want")
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # real → reset run to 0
    await asyncio.gather(*_probe_tasks())

    pipe._probe_stt = _InstantSTT(text="thank you for your help.")
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # boilerplate back to 1/2 → defer
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "boilerplate-defer run was not reset by intervening real speech"
    )


# --------------------------------------------------------------------------- #
# Live recurrence 2026-06-15: "I would like you to [pause] open your Chrome
# browser" was force-cut after only "I would like you to..." (two sessions,
# 18:47:55 + 19:07:37). Both forced via the STT probe at silence_ms≈0 while the
# user was still mid-sentence — the 1.5 s silence rule was never reached because
# request_endpoint() bypasses it. Two surviving one-shot force branches caused
# it; these tests pin the corrected, uniform "no force on a single reading and
# never on a trailed-off tail" contract.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stable_trailed_off_tail_does_not_force() -> None:
    """Occurrence A (18:47:55): the probe transcribed the tail as
    'i would like you to...'. Whisper's trailing ellipsis ('...') marks a
    CLIPPED, still-ongoing utterance — the user trailed off mid-sentence, they
    are not done. The stable-tail signal force-cut it anyway at silence_ms=0.

    A tail that ``completion.is_incomplete`` flags as syntactically open-ended
    (trailing '...', open conjunction/determiner, trailing comma) must DEFER to
    the natural silence endpoint and never force — even when loud and stable.
    ``_probe_required_stable`` is pinned to 1 here so the stable threshold is
    reached on the first repeat, isolating the trailed-off guard from the
    persistence count.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="i would like you to..."), vad)
    pipe._probe_required_stable = 1
    pipe._probe_last_text = "i would like you to..."  # already seen → next is "stable"

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "a loud, stable, trailed-off tail ('...') force-cut the user mid-sentence"
    )


@pytest.mark.asyncio
async def test_open_conjunction_tail_does_not_force() -> None:
    """Companion to the ellipsis case: a stable tail ending in an open
    conjunction ('... open chrome and') is also syntactically incomplete — the
    user is mid-list, not done — and must defer, not force."""
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="open chrome and"), vad)
    pipe._probe_required_stable = 1
    pipe._probe_last_text = "open chrome and"

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "a stable tail ending in an open conjunction force-cut the user"
    )


@pytest.mark.asyncio
async def test_pre_speech_hallucination_single_probe_does_not_force() -> None:
    """Occurrence B (19:07:37): the user's real opening speech 'I would like you
    to' was mis-decoded by faster-whisper as the boilerplate 'i would like to
    thank you for your time.' (matches _STT_HALLUCINATION_RE) on the FIRST probe.
    Because no clean probe had landed yet, ``_probe_seen_real_speech`` was still
    False and the old code force-cut immediately as 'pure pre-speech bleed'.

    A single loud hallucination reading cannot be distinguished from a user whose
    live speech is being hallucinated — so it must DEFER (require persistence),
    not behead the turn on one probe.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(
        _InstantSTT(text="i would like to thank you for your time."), vad
    )
    assert pipe._probe_seen_real_speech is False  # fresh turn, no real speech yet

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "a single loud pre-speech hallucination probe force-cut the user "
        "mid-sentence (their real speech was mis-decoded as boilerplate)"
    )


@pytest.mark.asyncio
async def test_pre_speech_hallucination_then_real_speech_never_forces() -> None:
    """Occurrence B, full sequence: probe 1 hallucinates the user's opening words
    as boilerplate (defers), then probe 2 — ~0.6 s later — sees the user's
    continued real speech. The turn must never be force-cut."""
    vad = _RecordingVad()
    pipe = _make_probe_pipe(
        _InstantSTT(text="i would like to thank you for your time."), vad
    )

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # hallucinated → defer
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 0

    # The user keeps talking; the probe now decodes the real continuation.
    pipe._probe_stt = _InstantSTT(text="open your chrome browser")
    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.request_endpoint_calls == 0, (
        "the turn was force-cut despite the user's real continuation arriving"
    )


@pytest.mark.asyncio
async def test_sustained_pre_speech_bleed_still_forces() -> None:
    """Positive control: removing the one-shot pre-speech force must NOT kill the
    speaker-bleed cure. Pure pre-speech boilerplate that PERSISTS (TV keeps
    playing) still forces — just after the same 2-probe persistence as every
    other empty/boilerplate tail, not on probe 1.
    """
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="vielen dank."), vad)

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # bleed 1/2 → defer
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 0, (
        "a single pre-speech boilerplate probe force-cut — should defer first"
    )

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)  # bleed 2/2 → force
    await asyncio.gather(*_probe_tasks())
    assert vad.request_endpoint_calls == 1, (
        "sustained pre-speech speaker bleed never forced — the bleed cure broke"
    )


# --------------------------------------------------------------------------- #
# Adaptive endpoint patience for delegation composition (live 2026-06-16).
#
# Forensic: "Could you please start a sub-agent mission which gives me a
# complete, complete, complete" was submitted on a mid-composition thinking
# pause — the turn ENDED at the normal 1.5 s silence window (reason=silence,
# silence_ms=1472), not on a probe force-cut. The word "sub-agent" is not a
# trigger; composing a delegation simply takes longer pauses than a short
# command. Fix: when the live partial shows a delegation is being composed, the
# STT probe extends THIS utterance's silence window so the pause to formulate
# the task is not mistaken for "done". Short commands never match → snappy
# default preserved.
# --------------------------------------------------------------------------- #


def test_looks_like_delegation_composition_matches_bilingual_markers() -> None:
    from jarvis.speech.pipeline import _looks_like_delegation_composition

    assert _looks_like_delegation_composition(
        "please spawn a sub-agent that researches"
    )
    assert _looks_like_delegation_composition("could you start a subagent mission")
    assert _looks_like_delegation_composition("starte mir eine Sub-Agent-Mission, die")
    assert _looks_like_delegation_composition("starte eine SubAgenten-Mission")
    assert _looks_like_delegation_composition("delegate this to a worker and")
    # No delegation marker → ordinary command, must NOT match.
    assert not _looks_like_delegation_composition("open chrome and search for the news")
    assert not _looks_like_delegation_composition("what is the weather in san francisco")
    assert not _looks_like_delegation_composition("")


@pytest.mark.asyncio
async def test_delegation_partial_extends_silence_window() -> None:
    """When the live partial shows a delegation being composed, the probe must
    extend the silence window so the user is not cut off on a thinking pause."""
    from jarvis.speech.pipeline import _DELEGATION_SILENCE_MS

    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="spawn a sub-agent that researches"), vad)

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.extend_calls, "a delegation partial did not extend the silence window"
    assert vad.extend_calls[0] == _DELEGATION_SILENCE_MS
    # Extending patience must NOT itself end the turn — it only widens the
    # silence window; the natural endpoint still does the finalizing.
    assert vad.request_endpoint_calls == 0


@pytest.mark.asyncio
async def test_ordinary_command_partial_does_not_extend_window() -> None:
    """A short, non-delegation command must keep the snappy default window."""
    vad = _RecordingVad()
    pipe = _make_probe_pipe(_InstantSTT(text="open chrome and search the news"), vad)

    pipe._on_vad_probe(b"\x00\x00" * 256, tail_loud=True)
    await asyncio.gather(*_probe_tasks())

    assert vad.extend_calls == [], (
        "an ordinary command extended the silence window — short commands must "
        "stay snappy"
    )
