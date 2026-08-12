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
    SHAPE_SPEECH,
    SHAPE_UNDECIDED,
    VoskKwsProvider,
    candidate_shape_ok,
    candidate_shape_verdict,
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


# --- the name must actually have been SPOKEN (live false-wake class) --------


def test_a_prefix_with_no_name_after_it_is_not_a_wake() -> None:
    """LIVE false wake (2026-07-13 11:05): 'hey ho' fired for 'Hey Ruben'.

    The free ear heard the prefix and then a 0.12 s grunt — the NAME was never
    spoken, so the grammar had stretched a bare interjection onto the phrase.
    Spelling and sound-similarity cannot catch this (measured: room speech
    scores HIGHER against "ruben" than genuine calls do). What does catch it,
    word-agnostically, is that nothing name-sized was uttered where the name
    belongs.
    """
    assert candidate_shape_ok(
        [_w("hey", 0.40, 0.62), _w("ho", 0.62, 0.74)], "Hey Ruben"
    ) is False


def test_a_bare_prefix_is_not_a_wake() -> None:
    """The free ear heard only "hey" — there is no core at all."""
    assert candidate_shape_ok([_w("hey", 0.40, 0.65)], "Hey Ruben") is False


def test_a_real_name_body_still_passes_however_it_is_spelled() -> None:
    """The genuine calls keep firing: a name-sized sound IS there."""
    assert candidate_shape_ok(
        [
            _w("hey", 0.40, 0.60, conf=1.0),
            _w("room", 0.60, 1.03, conf=0.63),
        ],
        "Hey Ruben",
    ) is True
    assert candidate_shape_ok([_w("herum", 0.40, 1.02)], "Hey Ruben") is True


def test_a_confident_prefix_does_not_hide_a_confident_other_core() -> None:
    """Only uncertainty in the arbitrary core is positive wake evidence."""
    assert candidate_shape_ok(
        [
            _w("hey", 0.40, 0.60, conf=1.0),
            _w("google", 0.60, 1.03, conf=1.0),
        ],
        "Hey Ruben",
    ) is False


def test_an_unprefixed_phrase_counts_its_whole_body_as_core() -> None:
    """"Computer" has no prefix to strip — the word itself is the core."""
    assert candidate_shape_ok([_w("kompott", 0.40, 0.95)], "Computer") is True
    assert candidate_shape_ok([_w("kom", 0.40, 0.48)], "Computer") is False


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


def _stub_take(grammar: dict, free: dict, competition: dict | None = None):
    """A ``_take_verify_rec`` stand-in routing by recognizer kind.

    ``competition`` defaults to the grammar decode — the common genuine case
    where the phrase also wins against the "<prefix> [unk]" competitor.
    """

    def _take(model_path, kind):  # noqa: ANN001
        if kind == "grammar":
            return _StubRec(grammar)
        if kind == "competition":
            return _StubRec(competition if competition is not None else grammar)
        return _StubRec(free)

    return _take


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

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free))
    assert p._verify_window(_loud_window(), fail_open=True) is True


def test_verify_still_rejects_room_speech_forced_onto_the_phrase(monkeypatch) -> None:
    """The precision contract the free-ear check was added for stays intact.

    Since the span-duration clipping (2026-08-12) this fixture sits at the
    duration bound and is decided by the span-trimmed competition; on
    unrelated flowing speech the decoder keeps the alternative ("[unk]"
    scripted — the measured outcome, 0/48 negative fires on the bench).
    """
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
    competition = {"text": "[unk]", "result": []}

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free, competition))
    assert p._verify_window(_loud_window(), fail_open=True) is False


# --- shape acceptances must WIN the acoustic competition (2026-07-17) --------


_ATLAS_GRAMMAR = {
    "text": "hey atlas",
    "result": [
        {"word": "hey", "start": 0.40, "end": 0.62, "conf": 1.0},
        {"word": "atlas", "start": 0.62, "end": 1.05, "conf": 1.0},
    ],
}
# The free ear heard a name-shaped SOMETHING it could not spell — exactly the
# output a call of a DIFFERENT name produces too.
_ATLAS_FREE = {
    "text": "hey holden",
    "result": [
        _w("hey", 0.40, 0.62, conf=0.9),
        _w("holden", 0.62, 1.05, conf=0.6),
    ],
}


def test_a_shape_acceptance_that_loses_the_competition_does_not_fire(
    monkeypatch,
) -> None:
    """LIVE false wake (2026-07-17): 'hey nova' fired (shape) for 'Hey Jarvis'.

    The forced grammar had no way to say "hey + some OTHER word". Once the
    competitor grammar hears exactly that, the shape acceptance must fall.
    """
    p = VoskKwsProvider("Hey Atlas", model_path="fake", keyword="atlas")
    competition = {"text": "hey [unk]", "result": []}

    monkeypatch.setattr(
        p, "_take_verify_rec", _stub_take(_ATLAS_GRAMMAR, _ATLAS_FREE, competition)
    )
    assert p._verify_window(_loud_window(), fail_open=True) is False
    assert p.stats()["suppressed_shape_competition"] == 1


def test_a_shape_acceptance_that_wins_the_competition_still_fires(
    monkeypatch,
) -> None:
    """A genuine garbled wake keeps firing: the phrase wins its own audio."""
    p = VoskKwsProvider("Hey Atlas", model_path="fake", keyword="atlas")

    monkeypatch.setattr(
        p, "_take_verify_rec", _stub_take(_ATLAS_GRAMMAR, _ATLAS_FREE)
    )
    assert p._verify_window(_loud_window(), fail_open=True) is True


def test_the_spelling_path_never_consults_the_competition(monkeypatch) -> None:
    """A free ear that SPELLED the phrase fires even when the competitor
    grammar would disagree — the spelling path stays accept-only (AP-27)."""
    p = VoskKwsProvider("Hey Atlas", model_path="fake", keyword="atlas")
    free = {
        "text": "hey atlas",
        "result": [
            _w("hey", 0.40, 0.62, conf=0.9),
            _w("atlas", 0.62, 1.05, conf=0.8),
        ],
    }
    competition = {"text": "hey [unk]", "result": []}

    monkeypatch.setattr(
        p, "_take_verify_rec", _stub_take(_ATLAS_GRAMMAR, free, competition)
    )
    assert p._verify_window(_loud_window(), fail_open=True) is True


def test_a_broken_competition_pass_fails_open(monkeypatch) -> None:
    """The extra check must never make the detector deaf."""
    p = VoskKwsProvider("Hey Atlas", model_path="fake", keyword="atlas")

    class _Boom:
        def AcceptWaveform(self, pcm: bytes) -> bool:  # noqa: N802 - vosk API
            raise RuntimeError("competition recognizer broke")

        def FinalResult(self) -> str:  # noqa: N802 - vosk API
            raise RuntimeError("competition recognizer broke")

    def _take(model_path, kind):  # noqa: ANN001
        if kind == "competition":
            return _Boom()
        return _StubRec(_ATLAS_GRAMMAR if kind == "grammar" else _ATLAS_FREE)

    monkeypatch.setattr(p, "_take_verify_rec", _take)
    assert p._verify_window(_loud_window(), fail_open=True) is True


def test_an_unprefixed_phrase_competes_against_the_free_hypothesis(
    monkeypatch,
) -> None:
    """"Computer" has no prefix anchor, but since 2026-08-12 it still competes
    — against the free ear's own hypothesis and "[unk]". A shape acceptance
    whose audio the decoder rather explains as the hypothesis is rejected;
    one the decoder keeps as the phrase stands. (Before this, an unprefixed
    phrase's competition ALWAYS stood, which left the live 'pedro' class —
    a random word firing through the shape path — open for those phrases.)
    """
    p = VoskKwsProvider("Computer", model_path="fake", keyword="computer")
    assert p._competition_grammar is None  # still no static prefix competitor

    grammar = {
        "text": "computer",
        "result": [{"word": "computer", "start": 0.40, "end": 0.95, "conf": 1.0}],
    }
    free = {"text": "kompott", "result": [_w("kompott", 0.40, 0.95, conf=0.6)]}

    lost = {"text": "kompott", "result": []}
    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free, lost))
    assert p._verify_window(_loud_window(), fail_open=True) is False

    p2 = VoskKwsProvider("Computer", model_path="fake", keyword="computer")
    monkeypatch.setattr(p2, "_take_verify_rec", _stub_take(grammar, free))
    assert p2._verify_window(_loud_window(), fail_open=True) is True


def test_the_competition_grammar_derives_from_the_configured_phrase() -> None:
    """Any prefixed phrase gets its own "<prefix> [unk]" competitor."""
    p = VoskKwsProvider("Hallo Vega", model_path="fake", keyword="vega")
    assert p._competition_grammar is not None
    assert json.loads(p._competition_grammar) == [
        "hallo vega",
        "hallo [unk]",
        "[unk]",
    ]


# --- the wake spoken in ONE breath with the command (2026-07-25) -------------


def test_a_wake_followed_immediately_by_the_command_still_fires(monkeypatch) -> None:
    """The natural way people address an assistant must not be the hard case.

    The shape gate localises the free ear's words to the phrase span, and that
    window extends 0.3 s PAST the phrase. So any command word starting inside
    that trailing slack is counted into the candidate and pushes the token
    count over the phrase's own (``_SHAPE_TOKEN_SLACK`` is 0). For an
    out-of-vocabulary name the spelling path cannot cover that gap either, so
    BOTH confirm routes used to fail at once — an isolated call plus a pause
    fired, the same call followed by the request did not.

    Fixed 2026-08-04 without moving any bound: an over-budget token count now
    makes the candidate ``SHAPE_UNDECIDED`` instead of vetoing it, and the
    acoustic competition decides. That is a purely acoustic judgement, so the
    earlier objection — narrowing the shape WINDOW costs precision because the
    token bound relies on surrounding words being counted — no longer applies:
    the surrounding words are still counted, they just no longer get the last
    word. Precision is held by
    ``test_room_speech_across_the_phrase_span_is_still_rejected`` (hard-rejected
    on duration) and by the competition tests above.
    """
    p = VoskKwsProvider("Hey Ruben", model_path="fake", keyword="ruben")

    grammar = {
        "text": "hey ruben",
        "result": [
            {"word": "hey", "start": 0.40, "end": 0.62, "conf": 1.0},
            {"word": "ruben", "start": 0.62, "end": 1.05, "conf": 1.0},
        ],
    }
    # The free ear could not spell the name ("ruben" -> "erhoben") AND the
    # user kept talking straight through — the command words begin 0.06 s after
    # the phrase ends, well inside the old trailing slack.
    free = {
        "text": "erhoben wie ist das wetter",  # i18n-allow: recognition content under test
        "result": [
            _w("erhoben", 0.40, 1.02, conf=0.6),
            _w("wie", 1.11, 1.28, conf=1.0),
            _w("ist", 1.28, 1.42, conf=1.0),
            _w("das", 1.42, 1.58, conf=1.0),
            _w("wetter", 1.58, 1.95, conf=1.0),
        ],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free))
    assert p._verify_window(_loud_window(), fail_open=True) is True


def test_room_speech_across_the_phrase_span_is_still_rejected(monkeypatch) -> None:
    """The narrower shape window must not become a way in for room speech.

    Here the surrounding words OVERLAP the phrase span rather than following
    it, which is what a forced grammar hit on conversation actually looks
    like. Since the span-duration clipping (2026-08-12) such a candidate is
    UNDECIDED rather than hard-rejected, and the span-trimmed competition
    decides: on unrelated flowing speech the decoder keeps the alternative
    ("[unk]" scripted here — the measured outcome for unrelated audio, 0/20
    kept in the 2026-08-04 calibration and 0/48 negative fires in the
    2026-08-12 bench).
    """
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
    competition = {"text": "[unk]", "result": []}

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free, competition))
    assert p._verify_window(_loud_window(), fail_open=True) is False
    assert p.stats()["suppressed_shape_competition"] == 1


def test_a_bare_interjection_plus_a_command_is_still_not_a_wake() -> None:
    """The "hey ho" class (live 2026-07-13) sits INSIDE the phrase span, so
    narrowing the trailing edge cannot resurrect it: the core body after the
    known prefix is still too short to be a name."""
    assert candidate_shape_ok(
        [_w("hey", 0.40, 0.60, conf=1.0), _w("ho", 0.60, 0.68, conf=0.5)],
        "Hey Ruben",
    ) is False


# --- the shape gate's own spelling assumptions (measured 2026-08-04) ---------
#
# Live on the maintainer's install (17:01-17:02): 3 candidates, 2 suppressed,
# 1 fired — "I have to say it three times". Replaying the phrases through the
# real en model with a German and a US voice showed the free ear's output for a
# genuine call, and with it the two assumptions the shape gate still made.


@pytest.mark.parametrize(
    ("phrase", "heard"),
    (
        # A German-accented wake PREFIX is re-tokenised into confidently known
        # filler, so a two-token call arrives as three or four tokens.
        ("Hey Ben", [_w("have", 0.48, 0.69, conf=1.0),
                     _w("you", 0.69, 0.78, conf=1.0),
                     _w("been", 0.78, 1.08, conf=1.0)]),
        ("Hey Ruben", [_w("have", 0.48, 0.72, conf=1.0),
                       _w("you", 0.72, 0.89, conf=1.0),
                       _w("been", 0.94, 1.23, conf=1.0)]),
        ("Hey Atlas", [_w("have", 0.48, 0.66, conf=1.0),
                       _w("you", 0.66, 0.75, conf=1.0),
                       _w("at", 0.75, 0.88, conf=1.0),
                       _w("last", 0.88, 1.14, conf=1.0)]),
        # A name that IS an ordinary word: the free ear spells it confidently,
        # which the confidence rule read as proof of some OTHER word.
        ("Hey Ben", [_w("hey", 0.48, 0.72, conf=1.0),
                     _w("ben", 0.72, 1.05, conf=1.0)]),
        ("Hey Claude", [_w("hey", 0.48, 0.69, conf=1.0),
                        _w("cloud", 0.69, 1.17, conf=1.0)]),
    ),
)
def test_a_contested_shape_is_undecided_not_a_veto(phrase, heard) -> None:
    """Neither assumption may THROW AWAY a candidate on its own."""
    assert candidate_shape_verdict(heard, phrase) == SHAPE_UNDECIDED
    # ...and the narrow boolean question still answers "not on its own".
    assert candidate_shape_ok(heard, phrase) is False


@pytest.mark.parametrize(
    ("phrase", "heard"),
    (
        # Too much sound for the phrase's own tokens: flowing speech.
        ("Hey Ruben", [_w("herr", 0.20, 1.00), _w("oben", 1.00, 2.00)]),
        # Nothing name-sized where the name belongs ("hey ho", live 2026-07-13).
        ("Hey Ruben", [_w("hey", 0.40, 0.62), _w("ho", 0.62, 0.74)]),
        # The free ear heard no speech at all at the span.
        ("Hey Ruben", []),
    ),
)
def test_the_duration_questions_stay_hard_rejections(phrase, heard) -> None:
    """The two bounds that measure SOUND, not words, can never be overruled."""
    assert candidate_shape_verdict(heard, phrase) == SHAPE_SPEECH


def test_a_german_prefix_split_wake_fires_through_the_competition(monkeypatch) -> None:
    """The live bug: "Hey Ben" heard as "have you been" was thrown away.

    Measured against the real en model: the German voice's "Hey Ben" free-decodes
    to "have you been" (3 confidently known tokens), which failed the token
    count AND the confidence rule, while "been" is too far from "ben" for the
    spelling path. Both routes closed on a genuine call.
    """
    p = VoskKwsProvider("Hey Ben", model_path="fake", keyword="ben")
    grammar = {
        "text": "hey ben",
        "result": [
            {"word": "hey", "start": 0.48, "end": 0.78, "conf": 1.0},
            {"word": "ben", "start": 0.78, "end": 1.08, "conf": 1.0},
        ],
    }
    free = {
        "text": "have you been",
        "result": [
            _w("have", 0.48, 0.69, conf=1.0),
            _w("you", 0.69, 0.78, conf=1.0),
            _w("been", 0.78, 1.08, conf=1.0),
        ],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free))
    assert p._verify_window(_loud_window(), fail_open=True) is True


def test_a_contested_shape_that_loses_the_competition_does_not_fire(
    monkeypatch,
) -> None:
    """Undecided means the DECODER decides — not that the candidate is waved in.

    Measured on real audio: the competitor grammar keeps the phrase for 8/8
    genuine calls and 0/20 unrelated utterances, so this is where room speech
    that survives the two duration bounds is stopped.
    """
    p = VoskKwsProvider("Hey Ben", model_path="fake", keyword="ben")
    grammar = {
        "text": "hey ben",
        "result": [
            {"word": "hey", "start": 0.48, "end": 0.78, "conf": 1.0},
            {"word": "ben", "start": 0.78, "end": 1.08, "conf": 1.0},
        ],
    }
    free = {
        "text": "have you been",
        "result": [
            _w("have", 0.48, 0.69, conf=1.0),
            _w("you", 0.69, 0.78, conf=1.0),
            _w("been", 0.78, 1.08, conf=1.0),
        ],
    }
    competition = {"text": "hey [unk]", "result": []}

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free, competition))
    assert p._verify_window(_loud_window(), fail_open=True) is False
    assert p.stats()["suppressed_shape_competition"] == 1


# --- a wake spoken MID-SENTENCE must fire (maintainer mandate 2026-08-12) ----
#
# REVERSAL of the 2026-08-10 "a wake call stands alone" rule. The hard lead-in
# silence gate made the wake word dead in the middle and at the end of a
# longer sentence, and after normal speech the user had to wait seconds of
# quiet before the detector would listen again — both explicitly rejected by
# the maintainer on 2026-08-12 ("no dead time; the phrase fires embedded in a
# flowing sentence"). Precision against the 2026-08-10 mid-dictation fires is
# now owed by CONTENT, not position: the acoustic competition judges the
# SPAN-TRIMMED audio against the free ear's own hypothesis. Bench evidence
# (scripts/vosk_wake_bench.py, real de+en models, synthesized de+en voices):
# 0/144 negative fires across three phrases while every mid-/end-of-sentence
# positive for "Hey George" fires.


_GEORGE_LEAD_IN = [
    # Flowing dictation right up to the phrase's doorstep. i18n-allow:
    # recognition content under test.
    _w("und", 0.30, 0.45, conf=1.0),
    _w("dann", 0.45, 0.70, conf=1.0),
    _w("machen", 0.70, 1.05, conf=1.0),
    _w("wir", 1.05, 1.18, conf=1.0),
]
_GEORGE_GRAMMAR = {
    "text": "hey george",
    "result": [
        {"word": "hey", "start": 1.50, "end": 1.72, "conf": 1.0},
        {"word": "george", "start": 1.72, "end": 2.15, "conf": 1.0},
    ],
}


def test_a_mid_sentence_spelled_wake_fires(monkeypatch) -> None:
    """The free ear SPELLED the phrase inside flowing speech — that IS the
    wake word, spoken mid-sentence, and under the 2026-08-12 mandate it
    activates. (This exact fixture was the isolation gate's canonical
    suppression case before the reversal.)"""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {
        "text": "und dann machen wir hey george",  # i18n-allow: recognition content under test
        "result": [
            *_GEORGE_LEAD_IN,
            _w("hey", 1.50, 1.72, conf=1.0),
            _w("george", 1.72, 2.15, conf=1.0),
        ],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is True


def test_mid_sentence_room_speech_loses_the_competition(monkeypatch) -> None:
    """The 2026-08-10 false-fire class ('aufbürdet' at the span, i18n-allow:
    recognition content quoted from the live log) stays dead — by content,
    not position: the shape is contested and the span-trimmed competition
    hears the alternative, so the candidate falls."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {
        "text": "und dann machen wir aufbürdet",  # i18n-allow: recognition content under test
        "result": [*_GEORGE_LEAD_IN, _w("aufbürdet", 1.45, 2.20, conf=1.0)],  # i18n-allow
    }
    competition = {"text": "[unk]", "result": []}

    monkeypatch.setattr(
        p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free, competition)
    )
    assert p._verify_window(_loud_window(3.0), fail_open=True) is False
    assert p.stats()["suppressed_shape_competition"] == 1


def test_a_bare_random_word_loses_the_competition(monkeypatch) -> None:
    """LIVE (2026-08-12): the free ear heard a bare 'pedro' — one word, no
    prefix at all — and 'Hey George' fired through the shape/undecided path
    because the full-ring competition rubber-stamped it. With the span-trimmed
    hypothesis-aware competition the decoder keeps 'pedro', and no part of a
    multi-part wake phrase on its own (nor any random word) can activate."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {"text": "pedro", "result": [_w("pedro", 1.60, 2.10, conf=1.0)]}
    competition = {"text": "pedro", "result": []}

    monkeypatch.setattr(
        p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free, competition)
    )
    assert p._verify_window(_loud_window(3.0), fail_open=True) is False
    assert p.stats()["suppressed_shape_competition"] == 1


def test_the_competition_judges_span_trimmed_audio_with_the_hypothesis(
    monkeypatch,
) -> None:
    """The two mechanics the 2026-08-12 precision rests on, pinned:

    * the competition decodes ONLY the candidate span's audio (±0.3 s pad),
      never the whole ring — over the full ring the forced alignment could
      place the phrase anywhere and confirmed nearly everything;
    * the free ear's hypothesis joins the grammar alternatives, so the
      decoder chooses between the phrase and what was actually heard.
    """
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {"text": "pedro", "result": [_w("pedro", 1.60, 2.10, conf=1.0)]}
    seen: dict = {}

    class _RecordingRec(_StubRec):
        def SetGrammar(self, grammar: str) -> None:  # noqa: N802 - vosk API
            seen["alternatives"] = json.loads(grammar)

        def AcceptWaveform(self, pcm: bytes) -> bool:  # noqa: N802 - vosk API
            seen["pcm_bytes"] = len(pcm)
            return True

    def _take(model_path, kind):  # noqa: ANN001
        if kind == "competition":
            return _RecordingRec({"text": "pedro", "result": []})
        return _StubRec(_GEORGE_GRAMMAR if kind == "grammar" else free)

    monkeypatch.setattr(p, "_take_verify_rec", _take)
    assert p._verify_window(_loud_window(3.0), fail_open=True) is False
    # span [1.50, 2.15] padded ±0.3 -> [1.20, 2.45] = 1.25 s of 3.0 s audio
    # (± a couple of samples of float rounding, never the whole ring).
    assert abs(seen["pcm_bytes"] - int(1.25 * 16_000) * 2) <= 4
    assert seen["alternatives"] == [
        "hey george",
        "hey [unk]",
        "pedro",
        "[unk]",
    ]


def test_an_isolated_call_still_fires_with_nothing_before_it(monkeypatch) -> None:
    """The genuine quiet-room call is untouched: no lead-in words at all."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {
        "text": "gorsch",
        "result": [_w("gorsch", 1.50, 2.15, conf=0.6)],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is True


def test_a_call_after_a_natural_pause_still_fires(monkeypatch) -> None:
    """~0.75 s of quiet before the phrase is a natural pause, not a staged one
    — earlier speech beyond the lead window must not eat the wake."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {
        "text": "fertig gorsch",  # i18n-allow: recognition content under test
        "result": [
            # ends 0.75 s before the phrase — a natural pause
            _w("fertig", 0.30, 0.75, conf=1.0),  # i18n-allow
            _w("gorsch", 1.50, 2.15, conf=0.6),
        ],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is True


def test_a_garbled_wake_after_flowing_speech_still_fires(monkeypatch) -> None:
    """The dead-time complaint, pinned from the other side: speech ending at
    the phrase's doorstep must not eat a wake whose shape and competition are
    clean. Under the removed isolation gate this exact fixture ('wir' ends
    0.02 s before the span pad) was a guaranteed rejection."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {
        "text": "und dann machen wir gorsch",  # i18n-allow: recognition content under test
        "result": [*_GEORGE_LEAD_IN, _w("gorsch", 1.50, 2.15, conf=0.6)],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is True


def test_trailing_command_speech_is_never_lead_in(monkeypatch) -> None:
    """The wake+command breath (2026-07-25) has all its extra words AFTER the
    phrase and keeps firing."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {
        "text": "gorsch wie ist das wetter",  # i18n-allow: recognition content under test
        "result": [
            _w("gorsch", 1.50, 2.15, conf=0.6),
            _w("wie", 2.55, 2.70, conf=1.0),
            _w("ist", 2.70, 2.82, conf=1.0),
            _w("das", 2.82, 2.95, conf=1.0),
        ],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is True


def test_the_sibling_rescue_faces_the_same_competition(monkeypatch) -> None:
    """The sibling rescue verifies through the SAME window check (the
    fail-closed ``_early_check``): a sibling whose span audio loses the
    competition must not resurrect a candidate the primary model already
    rejected. Secondary rescue paths are exactly where earlier gates in this
    file leaked, so this is pinned."""
    p = VoskKwsProvider(
        "Hey George",
        model_path="primary",
        model_paths=["primary", "sibling"],
        keyword="george",
    )
    free = {
        "text": "und dann machen wir aufbürdet",  # i18n-allow
        "result": [*_GEORGE_LEAD_IN, _w("aufbürdet", 1.45, 2.20, conf=1.0)],  # i18n-allow
    }
    competition = {"text": "[unk]", "result": []}

    def _take(model_path, kind):  # noqa: ANN001
        assert model_path == "sibling", "the rescue must verify on the sibling"
        if kind == "competition":
            return _StubRec(competition)
        return _StubRec(free if kind == "free" else _GEORGE_GRAMMAR)

    monkeypatch.setattr(p, "_take_verify_rec", _take)
    assert p._early_check(_loud_window(3.0), "sibling") is False


# --- embedded-wake re-score mechanics (2026-08-12) ---------------------------


def test_the_rescore_scores_the_adjacent_run_not_scattered_phrase_words(
    monkeypatch,
) -> None:
    """A ring decode like 'hey hey george hey [unk]' contains the phrase ONCE,
    plus stray forced 'hey's elsewhere in the window. The old check pooled
    EVERY phrase word in the decode: the min-conf swallowed a distant weak
    'hey' and the span stretched seconds wide, so an embedded wake could
    never pass. Only an adjacent in-order token run counts now — which is
    also the stricter reading: scattered parts ('hey ... george') are not
    the phrase, a multi-part wake is one unit."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    grammar = {
        "text": "hey hey george hey [unk]",
        "result": [
            _w("hey", 0.20, 0.40, conf=0.30),   # stray forced hit, weak
            _w("hey", 1.50, 1.72, conf=1.0),    # the call
            _w("george", 1.72, 2.15, conf=1.0),
            _w("hey", 2.50, 2.60, conf=0.40),   # stray forced hit, weak
        ],
    }
    free = {
        "text": "hey george",
        "result": [_w("hey", 1.50, 1.72, conf=1.0), _w("george", 1.72, 2.15, conf=0.8)],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is True


def test_scattered_phrase_parts_are_not_the_phrase(monkeypatch) -> None:
    """'hey ... george' with other audio between the parts must NOT re-score:
    the parts of a multi-part wake count only spoken together, in order."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    scattered = {
        "text": "hey [unk] george",
        "result": [
            _w("hey", 0.20, 0.40, conf=1.0),
            _w("[unk]", 0.60, 1.50, conf=1.0),
            _w("george", 1.72, 2.15, conf=1.0),
        ],
    }
    free = {
        "text": "hey george",
        "result": [_w("hey", 0.20, 0.40, conf=1.0), _w("george", 1.72, 2.15, conf=0.8)],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(scattered, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is False


def test_an_embedded_wake_is_rescored_over_the_trailing_cut(monkeypatch) -> None:
    """Over the full ring the grammar absorbs an embedded phrase into the
    surrounding "[unk]"s and the re-score goes deaf (bench 2026-08-12:
    pos_mid/pos_end recall was 0 before this). The trailing 1.8 s cut
    re-hears it; its word times are shifted back into full-window seconds so
    every downstream span check (energy, free-word localisation, trimmed
    competition) still lines up."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    ring_gres = {"text": "hey [unk] [unk]", "result": [_w("hey", 0.3, 0.5, conf=1.0)]}
    # Times below are CUT-relative; the cut starts at 3.0 - 1.8 = 1.2 s.
    tail_gres = {
        "text": "hey george",
        "result": [_w("hey", 0.35, 0.55, conf=1.0), _w("george", 0.55, 1.00, conf=1.0)],
    }
    # The free decode runs over the FULL window — times are window-relative;
    # the spelled phrase sits at 1.55-2.20 s, exactly where the shifted tail
    # span must land for the localisation to find it.
    free = {
        "text": "machen wir hey george",  # i18n-allow: recognition content under test
        "result": [
            _w("machen", 0.90, 1.25, conf=1.0),  # i18n-allow
            _w("wir", 1.25, 1.40, conf=1.0),
            _w("hey", 1.55, 1.75, conf=1.0),
            _w("george", 1.75, 2.20, conf=0.8),
        ],
    }
    grammar_calls = {"n": 0}

    def _take(model_path, kind):  # noqa: ANN001
        if kind == "grammar":
            grammar_calls["n"] += 1
            return _StubRec(ring_gres if grammar_calls["n"] == 1 else tail_gres)
        return _StubRec(free)

    monkeypatch.setattr(p, "_take_verify_rec", _take)
    assert p._verify_window(_loud_window(3.0), fail_open=True) is True
    assert grammar_calls["n"] == 2  # the cut ran only after the ring failed


# --- diagnosability: a suppressed wake must leave a trace (2026-07-25) --------


def test_a_suppressed_candidate_is_reported_then_rate_limited(monkeypatch) -> None:
    """A dropped candidate is the ONLY evidence separating "never heard" from
    "heard and thrown away" — the distinction a "sometimes it does not react"
    report turns on. Every rejection used to be DEBUG-only, so that evidence
    did not exist in production.

    Report the first few at INFO and then every 20th: diagnosable without
    turning a candidate storm into a log flood.
    """
    p = VoskKwsProvider("Hey Ruben", model_path="fake", keyword="ruben")
    seen: list[int] = []
    monkeypatch.setattr(
        "jarvis.plugins.wake.vosk_kws_provider.log.info",
        lambda *a, **k: seen.append(1),
    )
    monkeypatch.setattr(
        "jarvis.plugins.wake.vosk_kws_provider.log.debug", lambda *a, **k: None
    )

    for _ in range(25):
        p._log_suppression("test reason %d", 1)

    # 5 early + the 20th = 6 surfaced out of 25.
    assert len(seen) == 6, f"expected 6 surfaced suppressions, got {len(seen)}"


def test_suppression_logging_never_feeds_a_decision() -> None:
    """The counter is diagnostics. If a threshold ever read it, the log would
    have become an uncalibrated rejection path — the exact AP-27 shape."""
    p = VoskKwsProvider("Hey Ruben", model_path="fake", keyword="ruben")
    p._suppress_log_count = 10_000
    # A verify decision must be reachable and unaffected by the counter value.
    assert candidate_shape_ok(
        [_w("hey", 0.40, 0.62, conf=0.9), _w("erhoben", 0.62, 1.05, conf=0.6)],
        "Hey Ruben",
    ) is True
