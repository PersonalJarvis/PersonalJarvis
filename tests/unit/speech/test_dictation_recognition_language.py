"""What language the recogniser is ASKED for, before it transcribes anything.

Root cause these lock (2026-07-29, measured against openai/whisper-large-v3
through OpenRouter): ``auto`` reaches a provider as "no language field", which
means "detect it yourself". A dictation is uploaded in ~4 s segments, and on a
segment that short the model does not merely mislabel the language — it
TRANSLATES. The same German recording came back verbatim when posted whole and
as fluent English when posted in segments, and re-running one segment flipped
between the two. Every user-visible symptom followed from that: German speech
delivered as English words, and a history whose language column read "en"
because by then the text really was English.

Nothing downstream can repair it — a translated sentence IS English to any
text-based detector — so the decision has to be made before the call.
"""

from __future__ import annotations

from jarvis.speech.pipeline import (
    accept_recognition_reading,
    resolve_recognition_language,
)


class TestWhatTheRecogniserIsAskedFor:
    def test_a_fresh_session_still_asks_the_provider_to_detect(self):
        """Auto-detect is the shipped default and stays the starting point."""
        assert (
            resolve_recognition_language(pinned="auto", session_language="") == "auto"
        )

    def test_once_the_session_knows_the_language_it_says_so(self):
        """THE FIX: stop re-asking a 4 s clip a question already answered."""
        assert resolve_recognition_language(pinned="auto", session_language="de") == "de"

    def test_a_user_pin_outranks_the_session_reading(self):
        """The pin is the one signal a person can set; overruling it would
        leave them no way to be right."""
        assert resolve_recognition_language(pinned="de", session_language="en") == "de"

    def test_an_empty_pin_is_treated_as_auto(self):
        assert resolve_recognition_language(pinned="", session_language="") == "auto"
        assert resolve_recognition_language(pinned="  ", session_language="es") == "es"

    def test_the_answer_is_case_insensitive_and_trimmed(self):
        assert resolve_recognition_language(pinned=" DE ", session_language="") == "de"
        assert resolve_recognition_language(pinned="auto", session_language=" ES ") == "es"

    def test_every_supported_language_is_carried_the_same_way(self):
        """No de/en bias — a locale is a locale (CLAUDE.md §1)."""
        for code in ("de", "en", "es"):
            assert (
                resolve_recognition_language(pinned="auto", session_language=code)
                == code
            )


class TestWhichReadingsMaySteerASession:
    """A reading that pins the WRONG language costs the whole dictation, so the
    gate is deliberately strict: only a confident, placeable reading counts."""

    def test_a_confident_reading_is_accepted(self):
        assert accept_recognition_reading(language="de", probability=0.99) == "de"

    def test_an_unsure_reading_is_refused(self):
        """The detector drops sharply on noise and silence; that is the case
        this rejects, and it costs nothing on real speech (~1.0)."""
        assert accept_recognition_reading(language="de", probability=0.2) == ""

    def test_a_non_answer_is_refused_however_confident(self):
        for tag in ("", "   ", "auto", "unknown", "und"):
            assert accept_recognition_reading(language=tag, probability=1.0) == ""

    def test_a_missing_or_broken_probability_is_refused_not_assumed(self):
        assert accept_recognition_reading(language="de", probability=None) == ""
        assert accept_recognition_reading(language="de", probability="very") == ""

    def test_an_accepted_reading_comes_back_as_a_lowercase_code(self):
        assert accept_recognition_reading(language=" DE ", probability=0.9) == "de"


class TestTheBugItself:
    def test_a_german_session_never_asks_for_english_again(self):
        """The reported failure, end to end.

        Segment 1 is transcribed with no language field (the gamble). The
        preview reads the AUDIO as German and the session accepts it — so every
        later segment is asked for German instead of gambling again. That is
        what stops segment 2 coming back translated.
        """
        session = ""
        assert resolve_recognition_language(pinned="auto", session_language=session) == "auto"

        session = accept_recognition_reading(language="de", probability=1.0)

        for _later_segment in range(5):
            assert (
                resolve_recognition_language(pinned="auto", session_language=session)
                == "de"
            )

    def test_a_bilingual_user_is_followed_not_frozen(self):
        """A static pin was the 2026-06-14 bug (English audio written as German
        gibberish) and is not what this restores: the reading is renewed from
        the audio, so switching language mid-session still lands."""
        session = accept_recognition_reading(language="de", probability=1.0)
        assert resolve_recognition_language(pinned="auto", session_language=session) == "de"

        session = accept_recognition_reading(language="en", probability=0.97)
        assert resolve_recognition_language(pinned="auto", session_language=session) == "en"


# ---------------------------------------------------------------------------
# The anchor that carries a reading ACROSS dictations
# ---------------------------------------------------------------------------


def _pipeline():
    from jarvis.speech.pipeline import SpeechPipeline

    return SpeechPipeline.__new__(SpeechPipeline)


def test_a_fresh_pipeline_anchors_nothing() -> None:
    """No readings means auto-detect, exactly as before this existed."""
    assert _pipeline()._recent_dictation_language() == ""


def test_recent_dictations_supply_the_language_a_short_one_cannot() -> None:
    """The gap the in-session anchor leaves open, and why it matters.

    The session anchor is read from an on-device preview, which needs a local
    engine AND enough time to run. On a host without one — and on a two-second
    "carry on" anywhere — it is never set, so the upload goes out as ``auto``.
    That is the exact case the provider gets wrong: on the live history,
    dictations under 4 s came back tagged English 59-64 % of the time for a
    speaker who was overwhelmingly speaking German, and a mis-detected clip is
    not merely mislabelled, it is TRANSLATED.

    Carrying the recent reading across is what gives that short recording the
    context its own audio cannot supply.
    """
    pipe = _pipeline()
    for _ in range(3):
        pipe._remember_dictation_language("de", on_device=False)

    assert pipe._recent_dictation_language() == "de"
    # And that is what the next session starts from, so a short clip is
    # transcribed as German rather than detected from four seconds of audio.
    assert (
        resolve_recognition_language(
            pinned="auto", session_language=pipe._recent_dictation_language()
        )
        == "de"
    )


def test_one_mis_detected_dictation_cannot_redirect_the_next() -> None:
    """A majority, not the last reading — the anchor has to be steadier than
    the signal it is correcting."""
    pipe = _pipeline()
    for lang in ("de", "de", "de", "en"):
        pipe._remember_dictation_language(lang, on_device=False)

    assert pipe._recent_dictation_language() == "de"


def test_a_speaker_who_switches_language_is_followed() -> None:
    """Steady is not the same as stuck: a real switch wins within the window.

    The bilingual mandate — a static pin was the 2026-06-14 bug and must not
    come back through the anchor.
    """
    pipe = _pipeline()
    for lang in ("de", "de", "en", "en", "en"):
        pipe._remember_dictation_language(lang, on_device=False)

    assert pipe._recent_dictation_language() == "en"


def test_an_on_device_reading_outvotes_a_run_of_cloud_guesses() -> None:
    """A local decoder cannot have translated the audio, so its answer is about
    the SOUND — the one reading a translation cannot fake."""
    pipe = _pipeline()
    for _ in range(3):
        pipe._remember_dictation_language("en", on_device=False)
    pipe._remember_dictation_language("de", on_device=True)

    assert pipe._recent_dictation_language() == "de"


def test_a_tie_restores_plain_auto_detect() -> None:
    """With no majority the anchor says nothing rather than picking a side."""
    pipe = _pipeline()
    pipe._remember_dictation_language("de", on_device=False)
    pipe._remember_dictation_language("en", on_device=False)

    assert pipe._recent_dictation_language() == ""


def test_unusable_readings_are_never_recorded() -> None:
    """``auto``/``unknown`` are the absence of a reading, not a language."""
    pipe = _pipeline()
    for junk in ("", "auto", "unknown", "und", None):
        pipe._remember_dictation_language(junk, on_device=False)

    assert pipe._recent_dictation_language() == ""


def test_a_user_pin_still_outranks_the_anchor() -> None:
    """The one signal a person sets deliberately keeps winning."""
    pipe = _pipeline()
    for _ in range(3):
        pipe._remember_dictation_language("en", on_device=False)

    assert (
        resolve_recognition_language(
            pinned="de", session_language=pipe._recent_dictation_language()
        )
        == "de"
    )
