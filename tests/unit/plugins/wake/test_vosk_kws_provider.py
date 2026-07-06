"""The any-word Vosk grammar KWS provider: confirm contract, gates, firing.

CI has no vosk model (and often no vosk wheel), so a fake ``vosk`` module is
injected into ``sys.modules``; the provider's lazy in-method imports pick it
up. What is pinned here:

- ``sound_confirm`` is PERMISSIVE (AP-27): a sound-close mis-hearing ("hey
  room" for "Hey Ruben") passes, unrelated speech ("vielen dank") and an
  empty free transcript are rejected.  # i18n-allow: German utterance under test
- A grammar partial hit fires the keyword only after the free-decode confirm.
- Near-silent candidates are rejected on raw ENERGY (never transcript).
- A confirm infrastructure error fails OPEN (never eats a real wake).
- The wake detector yields the canonical keyword, never transcript text.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from collections.abc import AsyncIterator

import numpy as np
import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake.vosk_kws_provider import VoskKwsProvider, sound_confirm

# --- sound_confirm ------------------------------------------------------------


def test_sound_confirm_accepts_sound_close_mishearing() -> None:
    assert sound_confirm("hey room", "Hey Ruben") is True
    assert sound_confirm("hey ruben", "Hey Ruben") is True


def test_sound_confirm_rejects_unrelated_speech() -> None:
    assert sound_confirm("vielen dank", "Hey Ruben") is False  # i18n-allow: utterance under test


def test_sound_confirm_rejects_empty_free_transcript() -> None:
    # The free ear heard NOTHING — the grammar hit was noise, not speech.
    assert sound_confirm("", "Hey Ruben") is False


def test_sound_confirm_finds_phrase_inside_longer_speech() -> None:
    # i18n-allow: German utterance under test
    assert sound_confirm("ich sagte hey ruben gerade eben", "Hey Ruben") is True


# --- fake vosk runtime ----------------------------------------------------------


def _timed_words(text: str, conf: float, start: float = 0.5) -> list[dict]:
    out = []
    t = start
    for w in text.split():
        out.append({"word": w, "start": t, "end": t + 0.3, "conf": conf})
        t += 0.35
    return out


class _FakeRecognizer:
    """Scriptable KaldiRecognizer stand-in.

    Grammar mode (grammar arg passed): after ``fire_after`` chunks the partial
    contains the phrase; FinalResult re-hears the phrase with timed words at
    ``model.grammar_conf`` (the verify re-score input). Free mode (no
    grammar): FinalResult returns ``model.free_text`` with timed words in the
    same span — the knob the confirm-path tests turn.
    """

    def __init__(self, model, rate, grammar=None):  # noqa: ANN001
        self._model = model
        self._grammar = grammar
        self._chunks = 0

    def SetWords(self, flag):  # noqa: ANN001, N802
        pass

    def AcceptWaveform(self, pcm):  # noqa: ANN001, N802
        self._chunks += 1
        return False  # partial path only — finals are exercised via partials

    def PartialResult(self):  # noqa: N802
        if self._grammar is not None and self._chunks >= self._model.fire_after:
            return json.dumps({"partial": self._model.phrase.lower()})
        return json.dumps({"partial": ""})

    def Result(self):  # noqa: N802
        return json.dumps({"text": ""})

    def FinalResult(self):  # noqa: N802
        if self._grammar is not None:
            phrase = self._model.phrase.lower()
            return json.dumps({
                "text": phrase,
                "result": _timed_words(phrase, self._model.grammar_conf),
            })
        return json.dumps({
            "text": self._model.free_text,
            "result": _timed_words(self._model.free_text, 0.9),
        })


class _FakeModel:
    def __init__(self, path):  # noqa: ANN001
        self.path = path
        self.phrase = "hey nova"
        self.free_text = "hey nova"
        self.grammar_conf = 0.95
        self.fire_after = 3


@pytest.fixture()
def fake_vosk(monkeypatch):
    mod = types.ModuleType("vosk")
    state = {"model": None}

    def _model_factory(path):  # noqa: ANN001
        state["model"] = _FakeModel(path)
        return state["model"]

    mod.Model = _model_factory
    mod.KaldiRecognizer = _FakeRecognizer
    mod.SetLogLevel = lambda *_a: None
    monkeypatch.setitem(sys.modules, "vosk", mod)
    return state


def _chunk(value: int = 6000, n: int = 1600) -> AudioChunk:
    arr = np.full(n, value, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


def _silent_chunk(n: int = 1600) -> AudioChunk:
    return AudioChunk(pcm=b"\x00\x00" * n, sample_rate=16000, timestamp_ns=0)


async def _run_detect(provider: VoskKwsProvider, chunks: list[AudioChunk]) -> list[str]:
    async def _iter() -> AsyncIterator[AudioChunk]:
        for c in chunks:
            yield c

    fired: list[str] = []

    async def _drive() -> None:
        async for kw in provider.detect(_iter()):
            fired.append(kw)

    await asyncio.wait_for(_drive(), timeout=5.0)
    return fired


# --- detection loop -------------------------------------------------------------


async def test_partial_hit_with_confirm_fires_the_keyword(fake_vosk) -> None:
    # partial hit at chunk 3, then 0.6 s (6 chunks) of tail land in the ring
    # before the confirm runs — the free decoder must see the WHOLE phrase.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    fired = await _run_detect(p, [_chunk() for _ in range(12)])
    assert fired == ["nova"]
    assert p.stats()["fired"] == 1


async def test_confirm_rejection_suppresses_the_fire(fake_vosk) -> None:
    # The free ear hears unrelated speech — grammar pulled noise onto the phrase.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    fake_vosk_model_free_text = "das ist etwas ganz anderes"  # i18n-allow: utterance under test
    # model instance is created lazily inside detect; patch via the factory state
    fired: list[str] = []

    async def _late_set() -> None:
        # wait until the model exists, then set its free text before the hit
        for _ in range(50):
            if fake_vosk["model"] is not None:
                fake_vosk["model"].free_text = fake_vosk_model_free_text
                return
            await asyncio.sleep(0.01)

    async def _drive() -> None:
        async def _iter() -> AsyncIterator[AudioChunk]:
            for _ in range(12):
                await asyncio.sleep(0.005)
                yield _chunk()

        async for kw in p.detect(_iter()):
            fired.append(kw)

    await asyncio.wait_for(asyncio.gather(_late_set(), _drive()), timeout=5.0)
    assert fired == []
    assert p.stats()["suppressed_confirm"] >= 1


async def test_near_silent_candidate_is_gated_on_energy(fake_vosk) -> None:
    # AP-27: silence suppression happens on raw RMS at the match site — even
    # though the fake grammar "hears" the phrase, an all-zero window must gate.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    fired = await _run_detect(p, [_silent_chunk() for _ in range(12)])
    assert fired == []
    assert p.stats()["gated_rms"] >= 1


def test_confirm_infrastructure_error_fails_open(fake_vosk, monkeypatch) -> None:
    # A broken confirm must never eat a real wake (mirrors the echo-confirm
    # contract on the stt_match path): _verify_candidate returns True on ANY
    # infrastructure exception.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")

    def _boom():
        raise RuntimeError("confirm infra down")

    monkeypatch.setattr(p, "_ensure_model", _boom)
    assert p._verify_candidate(np.full(1600, 0.2, dtype=np.float32)) is True


def test_low_rescore_confidence_suppresses(fake_vosk) -> None:
    # Live forensic 2026-07-06: the streaming PARTIAL has no confidence (its
    # 1.00 placeholder passed the gate), so room speech fired constantly.
    # The verify re-score must supply a REAL confidence and gate on it.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    p._ensure_model()
    fake_vosk["model"].grammar_conf = 0.2  # weak re-hear -> not a wake
    assert p._verify_candidate(np.full(48000, 0.2, dtype=np.float32)) is False


def test_free_words_outside_the_span_cannot_confirm(fake_vosk) -> None:
    # The sound confirm must judge what was said AT the candidate's position;
    # a sound-close pair elsewhere in the 3 s window must not count.
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova")
    p._ensure_model()
    m = fake_vosk["model"]
    m.free_text = "ganz andere worte hier hey nowa"  # i18n-allow: utterance under test
    # free words start at 0.5s and stride 0.35s -> "hey nowa" sits ~1.9-2.6s,
    # far outside the grammar span (0.5-1.15s +-0.3) -> localised confirm
    # sees only unrelated words and rejects.
    assert p._verify_candidate(np.full(48000, 0.2, dtype=np.float32)) is False


async def test_cooldown_suppresses_immediate_refire(fake_vosk) -> None:
    p = VoskKwsProvider("Hey Nova", model_path="fake", keyword="nova", cooldown_s=60.0)
    fired = await _run_detect(p, [_chunk() for _ in range(24)])
    assert fired == ["nova"]  # second grammar hit lands inside the cooldown
    assert p.stats()["suppressed_cooldown"] >= 1
