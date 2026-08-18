"""Unit tests for the intent-level router (fast/deep/code provider selection).

Not to be confused with the Phase-5 tier router (`jarvis/brain/router.py`),
which classifies action targets (trivial/direct_action/spawn_worker).
"""
from __future__ import annotations

import pytest

from jarvis.brain.intent_router import classify


@pytest.mark.parametrize("text", [
    "öffne notepad",  # i18n-allow
    "öffne ein Terminal",  # i18n-allow
    "spawn 5 terminals",
    "mach den Browser auf",  # i18n-allow
    "starte Chrome",
    "klick auf submit",
    "merk dir ich heiße Harald",  # i18n-allow
    "sag hi",
    "wie spät ist es?",  # i18n-allow
    "hallo",
    "danke",
    "open notepad",
    "launch wt",
    "show me the time",
])
def test_fast_intents(text):
    d = classify(text)
    assert d.level == "fast", f"{text!r} => {d}"


@pytest.mark.parametrize("text", [
    "recherchiere mir die aktuelle Studienlage zu GPT-5",
    "analysier das Design meines Prompts",
    "erklär mir wie Retrieval-Augmented-Generation funktioniert",  # i18n-allow
    "plane mir eine Architektur für ein Multi-Agent-System",  # i18n-allow
    "vergleich Haiku gegen Opus für Reasoning",  # i18n-allow
    "schreib mir eine Email an den Kunden, warum wir zwei Wochen Verzug haben",  # i18n-allow
    "überleg dir gründlich was die richtige Strategie ist",  # i18n-allow
    "fasse das Video in 3 Punkten zusammen",
    "baue mir ein Konzept für eine Voice-App, die offline funktioniert",  # i18n-allow
    "warum zeigt mein Build einen N+1 Query-Fehler trotz eager-loading?",  # i18n-allow
    "think hard about the tradeoffs",
    "analyze this architecture",
])
def test_deep_intents(text):
    d = classify(text)
    assert d.level == "deep", f"{text!r} => {d}"


@pytest.mark.parametrize("text", [
    "implementier mir eine LRU-Cache-Klasse",
    "refactor den UserService",
    "fix bug im Login-Handler",
    "code review für diese PR",  # i18n-allow
    "debug den Pipeline-Stall",
])
def test_code_intents(text):
    d = classify(text)
    assert d.level == "code", f"{text!r} => {d}"


def test_empty_defaults_to_fast():
    assert classify("").level == "fast"
    assert classify("   ").level == "fast"


def test_long_unknown_falls_to_deep():
    long_text = ("Ich frage mich schon länger, wie man das " * 5) + "machen könnte?"  # i18n-allow
    assert classify(long_text).level == "deep"


# ---------------------------------------------------------------------------
# No silent downgrade (audit PR-07).
#
# A ~40-pattern keyword list ran over the whole utterance, so any hit anywhere
# routed the turn to the cheap fast model — "Ok, kannst du mir helfen …" matched  # i18n-allow
# \bok\b and got Haiku. The user was never told; only a debug log recorded it.
# A keyword may no longer demote a substantive request: FAST needs either a
# closed-set one-shot or a short verb-first command.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    # A one-shot word that merely OPENS a real request.
    "Ok, kannst du mir helfen, meine Steuererklärung zu sortieren?",  # i18n-allow
    "Okay, was hältst du von dieser Investitionsstrategie?",  # i18n-allow
    "Hi, ich brauche Rat zu meinem Arbeitsvertrag",  # i18n-allow
    "Danke, und wie geht das jetzt technisch weiter?",  # i18n-allow
    # An action verb inside a request that needs reasoning.
    "such mal eine Lösung für mein Speicherleck im Renderer",  # i18n-allow
    "zeig mir, welche Konsequenzen es hätte, wenn ich das Team halbiere",  # i18n-allow
    "list the tradeoffs between a monolith and microservices for my team",
    "save me from this mess: the deploy keeps failing and I do not know why",
    "run me through the whole onboarding process step by step",
    "open question: should we migrate the database or not",
    "close reading of this contract clause, please",
    # A noun that happens to be a keyword.
    "Ich will das Datum meiner Hochzeit auf einen Tag legen, an dem alle können",  # i18n-allow
    # Plain substantive turns with no keyword at all.
    "Sollte ich kündigen?",  # i18n-allow
    "Erzähl mir was über Quantencomputer",  # i18n-allow
    "Wie funktioniert eigentlich ein Dieselmotor",  # i18n-allow
    "Was kostet mich das ungefähr im Monat?",  # i18n-allow
])
def test_substantive_requests_keep_the_capable_model(text):
    d = classify(text)
    assert d.level != "fast", f"{text!r} was downgraded => {d}"


@pytest.mark.parametrize("text", [
    "hallo",
    "danke schön",  # i18n-allow
    "alles klar",  # i18n-allow
    "thanks a lot",
    "gracias",
    "bis später",  # i18n-allow
    "wie spät ist es?",  # i18n-allow
    "what time is it",
])
def test_closed_set_one_shots_stay_fast(text):
    d = classify(text)
    assert d.level == "fast", f"{text!r} => {d}"
    assert d.reason == "one-shot"


@pytest.mark.parametrize("text", [
    "öffne notepad",  # i18n-allow
    "starte Chrome",
    "klick auf submit",
    "ok, öffne notepad",  # i18n-allow — a lead-in does not hide the command
    "open notepad",
    "show me the time",
])
def test_short_verb_first_commands_stay_fast(text):
    d = classify(text)
    assert d.level == "fast", f"{text!r} => {d}"


def test_unknown_utterance_defaults_to_the_capable_model():
    # The old router defaulted an unrecognised turn to Haiku ("speed > depth").
    assert classify("Mein Nachbar hat wieder was Merkwürdiges gebaut").level == "deep"  # i18n-allow
    assert classify("Sag mal, was meinst du dazu, ehrlich gesagt").level == "deep"  # i18n-allow


def test_a_command_with_a_subclause_is_not_a_one_shot():
    assert classify("öffne den Editor, wenn die Datei existiert").level == "deep"  # i18n-allow
    assert classify("open the file if the build is green").level == "deep"
