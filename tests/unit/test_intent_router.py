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
    "merk dir ich heiße Sam",  # i18n-allow
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
