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


def _pipeline(*, primed: bool = True):
    """A bare pipeline for the anchor logic.

    ``primed`` marks the history seed as already done, so these tests exercise
    the arithmetic on readings they control instead of on whatever the machine
    running them happens to have dictated. The seeding itself is tested
    separately, against a history built for the purpose.
    """
    from jarvis.speech.pipeline import SpeechPipeline

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_anchor_primed = primed
    return pipe


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


def test_the_anchor_is_seeded_from_the_stored_history(tmp_path, monkeypatch) -> None:
    """After an app start the anchor must not be empty.

    Without seeding, the first short dictation of every session is back to
    asking two seconds of audio which language it is — the exact case this
    exists to avoid, reintroduced once per restart. The history already holds
    the answer.

    Only LONG entries are allowed to seed it, for the same reason only long
    dictations vote: a short one is what the provider gets wrong, so letting it
    seed would let the guess teach itself.
    """
    from jarvis.dictation.history import DictationHistory
    from jarvis.speech.pipeline import SpeechPipeline

    path = tmp_path / "history.json"
    store = DictationHistory(path)
    # Two long German dictations and a pile of short ones mis-tagged English —
    # the real shape of the bug this seed is for.
    for _ in range(6):
        store.add(raw_text="x", text="x", language="en", duration_s=1.5)
    for _ in range(2):
        store.add(raw_text="y", text="y", language="de", duration_s=12.0)

    monkeypatch.setattr(
        "jarvis.dictation.history.default_history_path", lambda: path
    )
    pipe = SpeechPipeline.__new__(SpeechPipeline)

    assert pipe._recent_dictation_language() == "de"


def test_seeding_happens_once_even_when_the_history_is_unreadable(
    tmp_path, monkeypatch
) -> None:
    """A broken history costs an opening guess, never a dictation."""
    from jarvis.speech.pipeline import SpeechPipeline

    calls: list[int] = []

    def _boom():
        calls.append(1)
        raise OSError("history is on a disconnected drive")

    monkeypatch.setattr("jarvis.dictation.history.default_history_path", _boom)
    pipe = SpeechPipeline.__new__(SpeechPipeline)

    assert pipe._recent_dictation_language() == ""
    assert pipe._recent_dictation_language() == ""
    assert len(calls) == 1, "the seed must not be retried on every dictation"


class TestBreakingOutOfAWrongLanguage:
    """The escape hatch that was missing, and the loop it left running.

    A session's language is seeded from the stored history, so on every
    dictation after the first it is already set — and the only code that
    accepted an audio reading ran under ``if not session_language``. A recogniser
    handed the wrong language TRANSLATES rather than mislabels, so the wrong
    result was stored as a row in that language, which fed the anchor, which
    seeded the next session. Measured on the live history: 13 consecutive
    dictations over ~2 h came back English from a German speaker.
    """

    def test_a_confident_sustained_disagreement_overrules_the_anchor(self) -> None:
        from jarvis.speech.pipeline import accept_recognition_correction

        # One reading is not enough, however sure it is.
        assert not accept_recognition_correction(
            current="en", language="de", probability=0.99, streak=1
        )
        # A second consecutive one is.
        assert accept_recognition_correction(
            current="en", language="de", probability=0.99, streak=2
        )

    def test_an_unsure_reading_never_overrules_it(self) -> None:
        """The bar is higher than for a first reading, on purpose.

        The anchor exists so a short clip cannot redirect a session; an escape
        hatch that opened as easily as the first reading would hand that back.
        """
        from jarvis.speech.pipeline import (
            _RECOGNITION_PIN_MIN_PROBABILITY,
            accept_recognition_correction,
        )

        # Confident enough to SET a language from nothing, not to overturn one.
        assert not accept_recognition_correction(
            current="en",
            language="de",
            probability=_RECOGNITION_PIN_MIN_PROBABILITY,
            streak=5,
        )

    def test_agreeing_readings_are_not_corrections(self) -> None:
        from jarvis.speech.pipeline import accept_recognition_correction

        assert not accept_recognition_correction(
            current="de", language="de", probability=1.0, streak=9
        )

    def test_a_non_answer_never_counts_as_a_disagreement(self) -> None:
        """"unknown" is the detector saying it could not tell, not a language."""
        from jarvis.speech.pipeline import accept_recognition_correction

        for tag in ("", "auto", "unknown", "und", "nn"):
            assert not accept_recognition_correction(
                current="en", language=tag, probability=1.0, streak=5
            ), tag

    def test_a_provider_reporting_no_confidence_is_refused(self) -> None:
        from jarvis.speech.pipeline import accept_recognition_correction

        assert not accept_recognition_correction(
            current="en", language="de", probability=None, streak=5
        )
