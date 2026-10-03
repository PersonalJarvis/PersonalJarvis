"""Unit tests for the shared hang-up intent module (jarvis/speech/hangup.py)."""

from __future__ import annotations

import pytest

from jarvis.speech.hangup import (
    END_CALL_SIGNAL,
    HANGUP_RE,
    HangupConfirmation,
    contains_end_signal,
    is_legacy_farewell,
    strip_end_signal,
    supports_semantic_hangup,
    user_asked_to_hang_up,
)


@pytest.mark.parametrize(
    "phrase",
    [
        # German explicit commands
        "auflegen",
        "leg auf",
        "lege auf",
        "auf leg",
        "legen sie auf",
        "aufgelegt",
        "tschüss",  # i18n-allow
        "tschuess",
        "beenden",
        "gespräch beenden",  # i18n-allow
        "auf wiederhören",  # i18n-allow
        "auf wiedersehen",
        "bis später",  # i18n-allow
        "gute nacht",
        "jarvis aus",
        "schluss jetzt",
        # English explicit commands
        "hang up",
        "hangup",
        "goodbye",
        "good bye",
        "good night",
        "goodnight",
        "bye bye",
        "stop jarvis",
        "exit",
        "quit",
        "ciao",
        "end the call",
    ],
)
def test_hangup_re_matches_explicit_commands(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is not None


@pytest.mark.parametrize(
    "phrase",
    [
        # Live 2026-06-09: Whisper transcribed the closing command "auflegen"
        # as "Auffliegen" (confidence 0.68) and "Aufflegen" (0.57). Neither
        # matched HANGUP_RE — "auffliegen" carries no "leg" substring and
        # "aufflegen" has a doubled "f" the "aufleg" patterns reject — so both
        # fell through to the brain (which then hallucinated) and the user had
        # to repeat "auflegen" three times before the session ended.
        "auffliegen",
        "Auffliegen",
        "auffliegt",
        "aufliegen",
        "aufflegen",
        "Aufflegen",
        "aufflegt",
        "auf jetzt",
        "Okay, auf jetzt.",
    ],
)
def test_hangup_re_matches_auflegen_mishearings(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is not None


@pytest.mark.parametrize(
    "phrase",
    [
        # Ambiguous-polite phrases are delegated to the brain (stay-on bias),
        # so the INSTANT regex must NOT fire on them.
        "vielen dank",
        "danke jarvis",
        "danke schön",  # i18n-allow
        "thanks jarvis",
        "das war's",
        # Normal speech must never match.
        "wie geht es dir",
        "erzähl mir was",  # i18n-allow
        "kannst du das nochmal machen",
        "geh mal auf die seite",  # i18n-allow
        "öffne die datei",  # i18n-allow
        # Live 2026-07-07: Groq garbled the 448 ms wake-phrase tail right
        # after a vosk wake into "Let's get up!" (English, conf 0.69) and the
        # former "English mis-hearings of auflegen" aliases instantly hung up
        # the freshly opened session ("the taskbar aborts right after the
        # wake word"). Ordinary English phrases must NEVER be hang-up
        # commands; a genuinely misheard "auflegen" is covered by the German
        # mishear family and the brain's END_CALL_SIGNAL path.
        "Let's get up!",
        "let us get up",
        "just get up",
        # Live 2026-07-12: OpenAI Realtime transcribed a language-switch
        # request with the words "auf jetzt". The former unanchored STT
        # mishearing alias treated those words inside ordinary speech as the
        # one-word closing command and killed the session before the brain ran.
        "Antworte auf jetzt nur noch auf Englisch.",  # i18n-allow: bug transcript
    ],
)
def test_hangup_re_ignores_ambiguous_and_normal_speech(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is None


def test_contains_end_signal_detects_token() -> None:
    assert contains_end_signal("Bis später, Ruben. [[END_CALL]]") is True  # i18n-allow
    assert contains_end_signal("Bis später, Ruben.") is False  # i18n-allow
    assert contains_end_signal("") is False
    assert contains_end_signal(None) is False  # type: ignore[arg-type]


def test_strip_end_signal_removes_token_and_trims() -> None:
    assert strip_end_signal("Bis später, Ruben. [[END_CALL]]") == "Bis später, Ruben."  # i18n-allow
    assert strip_end_signal("[[END_CALL]]") == ""
    assert strip_end_signal("Auf Wiedersehen.") == "Auf Wiedersehen."


def test_end_call_signal_is_the_documented_token() -> None:
    assert END_CALL_SIGNAL == "[[END_CALL]]"


@pytest.mark.parametrize(
    "phrase",
    [
        "goodbye, ruben",
        "goodbye ruben",
        "auf wiedersehen, ruben",
        "auf wiedersehen ruben",
        "goodbye, sir",
        "goodbye sir",
    ],
)
def test_is_legacy_farewell_matches_old_exact_phrases(phrase: str) -> None:
    assert is_legacy_farewell(phrase) is True


def test_is_legacy_farewell_rejects_other_text() -> None:
    assert is_legacy_farewell("auf wiedersehen ruben war mir ein vergnügen") is False  # i18n-allow
    assert is_legacy_farewell("hallo ruben") is False
    assert is_legacy_farewell("") is False


@pytest.mark.parametrize("text", [
    "Ich glaube wir sind durch", "Danke, das war's für heute.",  # i18n-allow
    "I think we're done here, thanks.", "That is all for now.",
    "I need to go", "No more questions", "Eso es todo por hoy, gracias.",
    "Creo que hemos terminado", "No necesito nada más",
])
def test_semantic_closure_requires_positive_user_evidence(text: str) -> None:
    assert supports_semantic_hangup(text)


@pytest.mark.parametrize("text", [
    "Explain the selected text on my screen", "Translate that please",
    "The task is complete", "The build is done", "Thanks", "Vielen Dank",  # i18n-allow
    "We are not done", "We're done with step one, now continue with step two",
    'Translate "we are done" into German', "We're done?", "No hemos terminado",
    "Ich glaube wir sind nicht fertig", "Das war's mit dem Fehler, mach weiter",  # i18n-allow
])
def test_task_completion_and_quoted_closings_are_not_session_closure(text: str) -> None:
    assert not supports_semantic_hangup(text)


@pytest.mark.parametrize("text", [
    "Leg auf", "auflegen bitte", "Tschüss Jarvis", "hang up", "Goodbye",  # i18n-allow
    "Danke, das war alles", "That's it for today", "Cuelga", "Adiós",  # i18n-allow
])
def test_end_call_gate_accepts_the_users_own_closing(text: str) -> None:
    assert user_asked_to_hang_up(text)


@pytest.mark.parametrize("text", [
    "", None, "Ja", "Ja.", "Yes", "Sí", "Okay", "Alles klar", "Mach das",  # i18n-allow
    "Danke", "Thanks", "Hey George, hallo",  # i18n-allow
])
def test_end_call_gate_refuses_a_model_hang_up_without_user_evidence(text) -> None:
    # Live 2026-10-01: the model answered a spoken "Ja" with end_call.
    assert not user_asked_to_hang_up(text)


@pytest.mark.parametrize("text", [
    "Don't hang up", "Please don't hang up", 'Translate "hang up"',
    "How do I quit the editor?", "Say goodbye to the error",
    "Nicht auflegen", "Bitte das Programm beenden",  # i18n-allow
    "Wenn ich auflegen sage, frag bitte nach",  # i18n-allow
    "Ich will die Datei ablegen", "Das darf nicht auffliegen",  # i18n-allow
])
def test_closing_words_inside_a_sentence_are_not_commands(text):
    assert HANGUP_RE.search(text) is None
    assert not user_asked_to_hang_up(text)


def test_hangup_requires_our_question_and_a_separate_turn():
    guard = HangupConfirmation()
    assert guard.observe("Yes", "unrelated-approval") == ""
    assert guard.observe("hang up", "request") == "request"
    assert guard.observe("Yes", "no-question-delivered") == ""
    guard.arm("request")
    assert guard.observe("hang up", "request") == "waiting"
    assert guard.observe("yes", "answer") == "confirmed"
    assert guard.observe("yes", "another-answer") == ""


@pytest.mark.parametrize("answer", ["No", "Yes, but keep talking", "Explain this", "Maybe", "Okay"])
def test_any_non_confirmation_revokes_pending_hangup(answer):
    guard = HangupConfirmation()
    guard.arm("request")
    assert guard.observe(answer, "answer") != "confirmed"
    assert guard.observe("yes", "later-approval") == ""


def test_hangup_confirmation_expires_and_cannot_cross_call_reset():
    guard = HangupConfirmation()
    guard.arm("request")
    guard.expires_at = 0
    assert guard.observe("yes", "late-answer") == ""
    guard.arm("request")
    guard.reset()
    assert guard.observe("yes", "new-call") == ""


def test_same_turn_correction_cannot_confirm_or_leave_a_stale_request():
    guard = HangupConfirmation()
    guard.arm("request")
    assert guard.observe("hang up, no, keep talking", "request") == ""
    assert guard.observe("yes", "answer") == ""
