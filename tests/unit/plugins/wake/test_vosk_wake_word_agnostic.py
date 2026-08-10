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

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free))
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


def test_an_unprefixed_phrase_has_no_competition_to_lose() -> None:
    """"Computer" offers no prefix anchor — the check always stands."""
    p = VoskKwsProvider("Computer", model_path="fake", keyword="computer")
    assert p._competition_grammar is None
    assert p._shape_competition_ok(b"", None) is True


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
    it, which is what a forced grammar hit on conversation actually looks like
    — so they are still counted and the candidate is still rejected.
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

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(grammar, free))
    assert p._verify_window(_loud_window(), fail_open=True) is False


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


# --- a wake call STANDS ALONE (live forensic 2026-08-10) ---------------------
#
# Three fires in 94 s while the user dictated German to a coding agent: the
# grammar forced flowing speech onto 'Hey George' and the verify let it
# through — twice via the acoustic competition, with unrelated German words
# ('herr tracks', 'aufbürdet' — i18n-allow: live-log recognition content)
# at the span, both conf 1.0), once via the SPELLING path itself (the en
# model heard a literal 'hey george' inside German flow). What every genuine
# call has and all three fires lack: silence immediately BEFORE the phrase.
# Mid-speech no-fire is the accepted product trade-off, so lead-in speech is
# a hard rejection for BOTH confirm paths — word-agnostic (timings only).


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


def test_a_mid_speech_shape_fire_is_rejected_by_lead_in_speech(monkeypatch) -> None:
    """LIVE (2026-08-10 19:59:27): free ear 'aufbürdet' (i18n-allow:
    recognition content quoted from the live log), shape UNDECIDED, the
    competition kept the phrase at conf 1.0 mid-stream — and it fired."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {
        "text": "und dann machen wir aufbürdet",  # i18n-allow: recognition content under test
        "result": [*_GEORGE_LEAD_IN, _w("aufbürdet", 1.45, 2.20, conf=1.0)],  # i18n-allow
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is False
    assert p.stats()["suppressed_lead_speech"] == 1


def test_a_mid_speech_spelled_phrase_is_rejected_by_lead_in_speech(
    monkeypatch,
) -> None:
    """LIVE (2026-08-10 19:58:34): the en free ear SPELLED 'hey george' out of
    flowing German — the accept-only spelling path cannot be the way around
    the isolation rule, or mentioning the wake word mid-sentence fires."""
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
    assert p._verify_window(_loud_window(3.0), fail_open=True) is False


def test_an_isolated_call_still_fires_with_nothing_before_it(monkeypatch) -> None:
    """The genuine quiet-room call is untouched: no lead-in words at all."""
    p = VoskKwsProvider("Hey George", model_path="fake", keyword="george")
    free = {
        "text": "gorsch",
        "result": [_w("gorsch", 1.50, 2.15, conf=0.6)],
    }

    monkeypatch.setattr(p, "_take_verify_rec", _stub_take(_GEORGE_GRAMMAR, free))
    assert p._verify_window(_loud_window(3.0), fail_open=True) is True
    assert p.stats()["suppressed_lead_speech"] == 0


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


def test_trailing_command_speech_is_never_lead_in(monkeypatch) -> None:
    """Only the LEADING side is gated: the wake+command breath (2026-07-25)
    has all its extra words AFTER the phrase and keeps firing."""
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
