"""SpeechPipeline gates OpenWakeWord hits behind a strict "hey + jarv"
transcript check.

Background: ``hey_jarvis_v0.1`` ONNX also fires on bare "Jarvis". Threshold
edits are forbidden by ``test_wake_threshold`` (BUG-009). The pipeline must
treat an OWW hit as a *candidate* only and require the prefix-verifier to
confirm before emitting the wake event.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace

from jarvis.core.config import STTConfig, TriggerConfig
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class _FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator:
        if False:  # pragma: no cover
            yield


@dataclass
class _FakeTranscript:
    text: str
    language: str = "de"
    confidence: float = 1.0


class _FakeSTT:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> _FakeTranscript:
        self.calls += 1
        return _FakeTranscript(text=self.text)


PCM_2S_16K = b"\x00\x00" * 16_000 * 2

# 2 s of a full-scale-ish square wave (rms ~0.09 normalised) — clears the
# custom-model silence gate so tests exercise the transcript logic behind it.
_LOUD_SAMPLE = (3000).to_bytes(2, "little", signed=True) + (-3000).to_bytes(
    2, "little", signed=True
)
LOUD_PCM_2S_16K = _LOUD_SAMPLE * 16_000


def _bare_pipeline(
    *,
    require_hey_prefix: bool,
    utterance_stt: object | None,
    wake_plan: object | None = None,
    wake_matcher: object | None = None,
) -> SpeechPipeline:
    """Build a SpeechPipeline shell without running __init__ — the gate logic
    only needs a few attributes set, so we avoid the heavy provider wiring.
    """
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._require_hey_prefix = require_hey_prefix
    pipe._utterance_stt = utterance_stt
    if wake_plan is not None:
        pipe._wake_plan = wake_plan
    # No default pattern ships (design 2026-07-07): the gate always runs on
    # the wake plan's matcher — these tests use the "Hey Jarvis" phrase.
    from jarvis.speech.wake_phrase import compile_wake_matcher

    pipe._wake_matcher = (
        wake_matcher if wake_matcher is not None else compile_wake_matcher("Hey Jarvis")
    )
    return pipe


async def test_gate_passes_when_flag_disabled() -> None:
    """require_hey_prefix=False restores raw OWW behaviour (legacy escape)."""
    stt = _FakeSTT("Jarvis")  # would reject, but must not even be called
    pipe = _bare_pipeline(require_hey_prefix=False, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls == 0


async def test_gate_passes_when_no_utterance_stt_configured() -> None:
    """No STT to verify with → degrade to legacy (warn, accept). Better than a
    silently bricked wake on a misconfigured rig."""
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=None)
    assert await pipe._verify_oww_hit(PCM_2S_16K) is True


async def test_gate_passes_on_hey_jarvis_transcript() -> None:
    stt = _FakeSTT("Hey Jarvis, was läuft heute")  # i18n-allow
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls == 1


async def test_gate_rejects_bare_jarvis_transcript() -> None:
    stt = _FakeSTT("Jarvis")
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is False
    assert stt.calls == 1


async def test_gate_rejects_empty_pcm_without_stt_call() -> None:
    stt = _FakeSTT("Hey Jarvis")
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(b"") is False
    assert stt.calls == 0


async def test_gate_degrades_open_on_stt_hallucination() -> None:
    """REGRESSION (live 2026-06-28): a strong OWW hit whose verify STT returns a
    KNOWN silence/noise hallucination ("Untertitelung des ZDF", "Vielen Dank.")
    must DEGRADE OPEN. Such a transcript means the STT failed to transcribe the
    real "Hey Jarvis" — it is NOT evidence the user stayed silent. Dropping these
    made the wake "stop working" for ~half of valid utterances (24 ok / 31 fail
    in one afternoon). Same intent as the transient-STT-failure degrade-open."""
    for phrase in (
        "Untertitelung des ZDF, 2020",
        "Vielen Dank.",
        "Thanks for watching",
    ):
        stt = _FakeSTT(phrase)
        pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)
        assert await pipe._verify_oww_hit(PCM_2S_16K) is True, phrase
        assert stt.calls == 1


async def test_gate_still_rejects_genuine_other_speech() -> None:
    """The hallucination degrade-open must NOT accept arbitrary non-wake speech:
    genuine other words (no "hey + jarv", not a known hallucination) are still
    suppressed, so the bare-"Jarvis" false-positive guard (BUG-009) stays."""
    stt = _FakeSTT("wie spät ist es")  # i18n-allow
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is False
    assert stt.calls == 1


class _RaisingSTT:
    """transcribe_pcm always raises — models a persistent Groq 503/timeout."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> _FakeTranscript:
        self.calls += 1
        raise self._exc


async def test_gate_degrades_open_on_empty_transcript_with_real_audio(monkeypatch) -> None:
    """REGRESSION ("Hey Jarvis sometimes stops working entirely"): a strong OWW
    hit captured real audio (non-empty buffer) but the verify STT came back with
    NO transcript — a momentary outage / rate-limit / silence-mis-transcription.
    That is not evidence the user stayed silent, so it must DEGRADE OPEN (accept)
    rather than brick the wake while the STT recovers (AP-22). This is distinct
    from empty PCM (nothing captured yet), which still rejects."""
    import jarvis.speech.wake_verifier as wv

    monkeypatch.setattr(wv, "_WAKE_VERIFY_BACKOFF_S", 0.0, raising=False)
    stt = _FakeSTT("")  # STT returned nothing for genuine captured audio
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls == 1


async def test_gate_degrades_open_when_stt_persistently_fails(monkeypatch) -> None:
    """A persistently failing verify STT (every attempt raises) on a strong OWW
    hit must degrade open, not suppress — the wake survives a provider outage and
    recovers in-app with no restart (AP-22). The empty-PCM reject is unchanged."""
    import jarvis.speech.wake_verifier as wv

    monkeypatch.setattr(wv, "_WAKE_VERIFY_BACKOFF_S", 0.0, raising=False)
    stt = _RaisingSTT(RuntimeError("groq 503"))
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls >= 1


# ---------------------------------------------------------------------------
# custom_onnx hits: the verify transcript is the REAL discriminator.
#
# Live forensic 2026-07-01/02 (false-positive storm + flicker GIF): a
# user-trained custom model scored breath/ambient/other speech up to 1.000 and
# fired many times a minute even at threshold 0.50 (15 fires in 25 s at 07:37).
# The verify layer therefore treats a custom-model hit as guilty until proven:
# - near-silent audio suppresses BEFORE any STT call (the fire flood was
#   hammering the verify STT into 429/timeouts),
# - "the STT worked but heard no wake phrase" (empty transcript or a known
#   silence-hallucination) suppresses,
# - and an STT OUTAGE also suppresses (fail-closed): the very session a wake
#   would open needs that same STT to hear anything, so degrading open only
#   produced deaf ghost activations (3 overnight on 2026-07-02) — hotkey and
#   orb-click remain the in-app activation paths, so the wake is not bricked.
# The precise pretrained hey_jarvis path keeps its documented degrade-open.
# ---------------------------------------------------------------------------


def _custom_pipeline(stt: object | None) -> SpeechPipeline:
    from jarvis.speech.wake_phrase import compile_wake_matcher

    return _bare_pipeline(
        require_hey_prefix=True,
        utterance_stt=stt,
        wake_plan=SimpleNamespace(engine="custom_onnx", verify_prefix=True),
        wake_matcher=compile_wake_matcher("Hey Nico"),
    )


async def test_gate_custom_model_accepts_matching_phrase() -> None:
    stt = _FakeSTT("hey nico wie spät ist es")  # i18n-allow
    pipe = _custom_pipeline(stt)

    assert await pipe._verify_oww_hit(LOUD_PCM_2S_16K) is True
    assert stt.calls == 1


async def test_gate_custom_model_accepts_sound_folded_spelling() -> None:
    """ASR spelling drift ("Niko" for "Nico") must still confirm the wake —
    the matcher sound-folds, so verify-on-custom cannot re-break recall."""
    stt = _FakeSTT("Hey Niko")
    pipe = _custom_pipeline(stt)

    assert await pipe._verify_oww_hit(LOUD_PCM_2S_16K) is True


async def test_gate_custom_model_rejects_other_speech() -> None:
    """The exact reported bug: the model fires while the user is just talking.
    A clear non-matching transcript must suppress the activation."""
    stt = _FakeSTT("und dann habe ich ihm gesagt dass das morgen fertig wird")  # i18n-allow
    pipe = _custom_pipeline(stt)

    assert await pipe._verify_oww_hit(LOUD_PCM_2S_16K) is False
    assert stt.calls == 1


async def test_gate_custom_model_suppresses_silence_before_stt() -> None:
    """Flood-breaker (live 2026-07-02): most custom-model false fires happen on
    breath/near-silence; each used to cost a full STT verify round-trip, and the
    resulting request flood drove the STT into 429/timeouts (which then opened
    the degrade-open hole). Near-silent audio is suppressed WITHOUT any STT
    call — same rms convention/threshold as RollingWhisperWake's silence gate."""
    stt = _FakeSTT("Hey Nico")  # would match if called — must NOT be called
    pipe = _custom_pipeline(stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is False
    assert stt.calls == 0, "silence must be suppressed before the STT round-trip"


async def test_gate_hey_jarvis_has_no_silence_gate() -> None:
    """The silence gate is custom_onnx-only: the pretrained hey_jarvis path
    keeps verifying silent-ish buffers via STT (its 2026-06-28 forensics rely
    on the transcript, not on energy)."""
    stt = _FakeSTT("Hey Jarvis")
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls == 1


async def test_gate_custom_model_rejects_empty_transcript(monkeypatch) -> None:
    """The "fires out of nowhere" half of the bug: the model fires on breath /
    noise, the verify STT works fine and hears NO speech. For a custom model an
    empty transcript is evidence of a false fire — suppress, do not degrade open."""
    import jarvis.speech.wake_verifier as wv

    monkeypatch.setattr(wv, "_WAKE_VERIFY_BACKOFF_S", 0.0, raising=False)
    stt = _FakeSTT("")
    pipe = _custom_pipeline(stt)

    assert await pipe._verify_oww_hit(LOUD_PCM_2S_16K) is False
    assert stt.calls == 1


async def test_gate_custom_model_rejects_hallucination_boilerplate() -> None:
    """Silence-hallucination boilerplate ("Vielen Dank.") on a custom-model hit
    means the buffer held silence/noise, not the wake phrase — suppress."""
    stt = _FakeSTT("Vielen Dank.")  # i18n-allow
    pipe = _custom_pipeline(stt)

    assert await pipe._verify_oww_hit(LOUD_PCM_2S_16K) is False


async def test_gate_custom_model_fails_closed_on_stt_outage(monkeypatch) -> None:
    """REGRESSION (live 2026-07-02, 3 ghost activations overnight): the fire
    flood of a weak custom model eventually hits a verify-STT timeout/429, and
    the old degrade-open then activated Jarvis although nobody spoke. For a
    custom-model hit an STT outage now SUPPRESSES (fail-closed): the session a
    wake opens needs that same STT anyway (it would be a deaf session), and
    hotkey/orb-click remain as in-app activation paths — so this does not brick
    the wake (AP-22 honoured by honest degradation, not by guessing)."""
    import jarvis.speech.wake_verifier as wv

    monkeypatch.setattr(wv, "_WAKE_VERIFY_BACKOFF_S", 0.0, raising=False)
    stt = _RaisingSTT(RuntimeError("groq 503"))
    pipe = _custom_pipeline(stt)

    assert await pipe._verify_oww_hit(LOUD_PCM_2S_16K) is False
    assert stt.calls >= 1


# ---------------------------------------------------------------------------
# Optimistic overlay reveal: only safe for precise pretrained candidates.
#
# The optimistic bar exists so a genuine "Hey Jarvis" feels instant on the
# precise pretrained model (false candidates are rare — a reject costs one
# brief flash). A custom model fires many times a minute, so the same
# optimism made the bar pop open/closed on auto-repeat (user GIF 2026-07-02).
# Custom ONNX and Vosk grammar candidates can be noisy, so their bar appears
# only AFTER verification confirms the full configured wake phrase.
# ---------------------------------------------------------------------------


def test_optimistic_candidate_shown_for_pretrained_when_idle() -> None:
    from jarvis.speech.pipeline import PipelineState

    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=None)
    pipe._state = PipelineState.IDLE
    assert pipe._should_show_optimistic_candidate() is True


def test_optimistic_candidate_hidden_for_custom_model() -> None:
    from jarvis.speech.pipeline import PipelineState

    pipe = _custom_pipeline(None)
    pipe._state = PipelineState.IDLE
    assert pipe._should_show_optimistic_candidate() is False


def test_optimistic_candidate_hidden_when_not_idle() -> None:
    from jarvis.speech.pipeline import PipelineState

    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=None)
    pipe._state = PipelineState.ACTIVE
    assert pipe._should_show_optimistic_candidate() is False


def test_optimistic_candidate_hidden_for_vosk_kws_when_idle() -> None:
    """Vosk stage-one grammar hits are unverified and frequently match speech."""
    from jarvis.speech.pipeline import PipelineState

    pipe = _bare_pipeline(
        require_hey_prefix=True,
        utterance_stt=None,
        wake_plan=SimpleNamespace(engine="vosk_kws", verify_prefix=False),
    )
    pipe._state = PipelineState.IDLE
    assert pipe._should_show_optimistic_candidate() is False


# ---------------------------------------------------------------------------
# Real construction: require_hey_prefix flows from config into the pipeline.
# ---------------------------------------------------------------------------


def _cfg_groq(*, require_hey_prefix: bool) -> SimpleNamespace:
    trigger = TriggerConfig(require_hey_prefix=require_hey_prefix)
    return SimpleNamespace(stt=STTConfig(provider="groq-api"), trigger=trigger)


def test_pipeline_default_requires_hey_prefix() -> None:
    pipe = SpeechPipeline(
        tts=_FakeTTS(),
        bus=None,
        enable_openwakeword=False,
        enable_whisper_wake=False,
        enable_local_whisper=False,
        config=_cfg_groq(require_hey_prefix=True),
    )
    assert pipe._require_hey_prefix is True


def test_pipeline_honours_disabled_require_hey_prefix() -> None:
    pipe = SpeechPipeline(
        tts=_FakeTTS(),
        bus=None,
        enable_openwakeword=False,
        enable_whisper_wake=False,
        enable_local_whisper=False,
        config=_cfg_groq(require_hey_prefix=False),
    )
    assert pipe._require_hey_prefix is False
