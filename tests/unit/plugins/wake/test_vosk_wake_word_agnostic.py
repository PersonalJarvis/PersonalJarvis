"""The wake verify must never depend on the free decoder SPELLING the wake word.

AP-27 in the Vosk path. Forensic (2026-07-13, 159 real captured "Hey Ruben"
calls from data/wake_debug, replayed through the production two-stage detector):

  * The free (unconstrained) decoder spelled the phrase in only 28 % of genuine
    calls. The rest came out as sound-alike garbage — "herum", "erhoben",
    "hey room", "hey oben", "hey ruhm", "heroes".
  * Rejecting on that garbage threw away 38 % of ALL real wakes; end-to-end
    recall sat at 32 % (the user had to repeat the wake word four or five
    times), while false accepts were 0/400 — the gate was far past the point of
    diminishing precision.
  * No spelling threshold can fix it: the free transcript "herr oben" was
    produced BOTH by a genuine call and by background chatter. Spelling is not
    a discriminator for an out-of-vocabulary proper noun, and every wake word is
    out-of-vocabulary for some installed language model.

The discriminator that DOES hold is the SHAPE of what the free ear heard at the
candidate position, which never asks how the wake word is written:

  * a wake call is short and stands alone     (measured: 0.72 s, 2 words)
  * room speech is a longer stream of words   (measured: 1.29 s, 5 words)

Both bounds derive from the configured phrase, so they hold for ANY phrase in
ANY supported language — the product requirement these tests exist to pin.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from jarvis.plugins.wake.vosk_kws_provider import (
    VoskKwsProvider,
    candidate_shape_ok,
    sound_confirm,
)


def _w(word: str, start: float, end: float, conf: float = 0.5) -> dict:
    return {"word": word, "start": start, "end": end, "conf": conf}


# --- the property that must hold for EVERY wake word -----------------------


@pytest.mark.parametrize(
    ("phrase", "garbled"),
    (
        # Real free-decode output captured for genuine calls (2026-07-13).
        ("Hey Ruben", [_w("herum", 0.40, 1.02)]),
        ("Hey Ruben", [_w("hey", 0.40, 0.62), _w("room", 0.62, 1.05)]),
        ("Hey Ruben", [_w("erhoben", 0.35, 1.05)]),
        ("Hey Ruben", [_w("hey", 0.40, 0.60), _w("oben", 0.60, 1.02)]),
        # The same failure mode for other phrases/languages: an offline model
        # cannot spell an arbitrary proper noun.
        ("Hey Jarvis", [_w("age", 0.40, 0.62), _w("avis", 0.62, 1.05)]),
        ("Computer", [_w("kompott", 0.40, 1.00)]),
        ("Hola Nova", [_w("ola", 0.40, 0.70), _w("nofa", 0.70, 1.00)]),
    ),
)
def test_a_garbled_wake_is_still_a_wake(phrase: str, garbled: list[dict]) -> None:
    """The free ear mangled the wake word — the shape still says 'wake call'."""
    assert candidate_shape_ok(garbled, phrase) is True


def test_a_split_name_is_covered_by_the_spelling_path() -> None:
    """A free decoder that SPLITS the name is handled by ``sound_confirm``.

    The shape gate deliberately allows no extra token (that cost real false
    accepts), so the two paths must between them still cover the split — this
    pins that division of labour.
    """
    assert sound_confirm("hey joe avis", "Hey Jarvis") is True


def test_room_speech_is_rejected_by_its_shape() -> None:
    """A stream of confidently-recognised words is speech, not a wake call."""
    flowing = [
        _w("die", 0.20, 0.35, conf=1.0),
        _w("richtigen", 0.35, 0.75, conf=1.0),
        _w("harte", 0.75, 1.05, conf=1.0),
        _w("baums", 1.05, 1.40, conf=1.0),
        _w("gibt", 1.40, 1.70, conf=1.0),
    ]
    assert candidate_shape_ok(flowing, "Hey Ruben") is False


def test_a_confidently_recognised_other_word_is_not_a_wake() -> None:
    """The free ear KNOWS this word — so it was not an unknown wake word."""
    known = [_w("google", 0.40, 1.00, conf=1.0)]
    assert candidate_shape_ok(known, "Hey Ruben") is False


def test_an_overlong_utterance_is_not_a_wake_call() -> None:
    """Two words, but spoken over 1.8 s — the grammar stretched real speech."""
    stretched = [_w("herr", 0.20, 1.00), _w("oben", 1.00, 2.00)]
    assert candidate_shape_ok(stretched, "Hey Ruben") is False


def test_silence_can_never_pass_the_shape_gate() -> None:
    assert candidate_shape_ok([], "Hey Ruben") is False


def test_the_bound_scales_with_the_configured_phrase() -> None:
    """A three-token phrase legitimately takes longer than a one-token one."""
    three = [
        _w("gud", 0.2, 0.6),
        _w("morgen", 0.6, 1.1),
        _w("atlas", 1.1, 1.6),
    ]
    assert candidate_shape_ok(three, "Good Morning Atlas") is True
    # the very same audio shape is far too long for a single-word wake
    assert candidate_shape_ok(three, "Computer") is False


# --- the two paths are complementary, and the bonus path may only ACCEPT ----


def test_sound_confirm_stays_a_bonus_path_and_never_rejects() -> None:
    """A correctly-spelled free transcript still confirms instantly."""
    assert sound_confirm("hey ruben", "Hey Ruben") is True


def test_the_shape_gate_catches_what_spelling_cannot() -> None:
    """The mishearings that spelling rules cannot rescue still fire.

    ``sound_confirm`` chases these with ever-looser similarity floors; each
    loosening buys one mishearing and risks the next false wake. The shape gate
    needs none of them, because it never looks at the spelling at all.
    """
    for heard in ("hey ho", "heroes", "harry", "hey euro"):
        assert sound_confirm(heard, "Hey Ruben") is False, heard
        assert candidate_shape_ok([_w(heard, 0.40, 1.02)], "Hey Ruben") is True, heard


# --- end-to-end through the real verify -------------------------------------


class _StubRec:
    """A KaldiRecognizer stub returning a scripted decode."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def AcceptWaveform(self, pcm: bytes) -> bool:  # noqa: N802 - vosk API
        return True

    def FinalResult(self) -> str:  # noqa: N802 - vosk API
        return json.dumps(self._result)


def _loud_window(seconds: float = 2.0) -> np.ndarray:
    rng = np.random.default_rng(7)
    return (rng.standard_normal(int(16_000 * seconds)) * 0.15).astype(np.float32)


def test_verify_fires_on_a_wake_the_free_ear_could_not_spell(monkeypatch) -> None:
    """The live BUG: 'Hey Ruben' heard as 'herum' was thrown away."""
    p = VoskKwsProvider("Hey Ruben", model_path="fake", keyword="ruben")

    grammar = {
        "text": "hey ruben",
        "result": [
            {"word": "hey", "start": 0.40, "end": 0.62, "conf": 1.0},
            {"word": "ruben", "start": 0.62, "end": 1.05, "conf": 1.0},
        ],
    }
    free = {"text": "herum", "result": [_w("herum", 0.40, 1.02, conf=0.6)]}

    def _take(model_path, kind):  # noqa: ANN001
        return _StubRec(grammar if kind == "grammar" else free)

    monkeypatch.setattr(p, "_take_verify_rec", _take)
    assert p._verify_window(_loud_window(), fail_open=True) is True


def test_verify_still_rejects_room_speech_forced_onto_the_phrase(monkeypatch) -> None:
    """The precision contract the free-ear check was added for stays intact."""
    p = VoskKwsProvider("Hey Ruben", model_path="fake", keyword="ruben")

    grammar = {
        "text": "hey ruben",
        "result": [
            {"word": "hey", "start": 0.30, "end": 0.60, "conf": 1.0},
            {"word": "ruben", "start": 0.60, "end": 1.60, "conf": 1.0},
        ],
    }
    free = {
        "text": "die richtigen harte baums gibt",
        "result": [
            _w("die", 0.20, 0.35, conf=1.0),
            _w("richtigen", 0.35, 0.75, conf=1.0),
            _w("harte", 0.75, 1.05, conf=1.0),
            _w("baums", 1.05, 1.40, conf=1.0),
            _w("gibt", 1.40, 1.70, conf=1.0),
        ],
    }

    def _take(model_path, kind):  # noqa: ANN001
        return _StubRec(grammar if kind == "grammar" else free)

    monkeypatch.setattr(p, "_take_verify_rec", _take)
    assert p._verify_window(_loud_window(), fail_open=True) is False
