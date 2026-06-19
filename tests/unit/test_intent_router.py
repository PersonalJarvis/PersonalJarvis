"""Unit-Tests für Intent-Level-Router (fast/deep/code Provider-Selection).

Nicht zu verwechseln mit dem Phase-5-Tier-Router (`jarvis/brain/router.py`),
der Action-Targets (trivial/direct_action/spawn_worker) klassifiziert.
"""
from __future__ import annotations

import pytest

from jarvis.brain.intent_router import classify


@pytest.mark.parametrize("text", [
    "öffne notepad",
    "öffne ein Terminal",
    "spawn 5 terminals",
    "mach den Browser auf",
    "starte Chrome",
    "klick auf submit",
    "merk dir ich heiße Harald",
    "sag hi",
    "wie spät ist es?",
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
    "erklär mir wie Retrieval-Augmented-Generation funktioniert",
    "plane mir eine Architektur für ein Multi-Agent-System",
    "vergleich Haiku gegen Opus für Reasoning",
    "schreib mir eine Email an den Kunden, warum wir zwei Wochen Verzug haben",
    "überleg dir gründlich was die richtige Strategie ist",
    "fasse das Video in 3 Punkten zusammen",
    "baue mir ein Konzept für eine Voice-App, die offline funktioniert",
    "warum zeigt mein Build einen N+1 Query-Fehler trotz eager-loading?",
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
    "code review für diese PR",
    "debug den Pipeline-Stall",
])
def test_code_intents(text):
    d = classify(text)
    assert d.level == "code", f"{text!r} => {d}"


def test_empty_defaults_to_fast():
    assert classify("").level == "fast"
    assert classify("   ").level == "fast"


def test_long_unknown_falls_to_deep():
    long_text = ("Ich frage mich schon länger, wie man das " * 5) + "machen könnte?"
    assert classify(long_text).level == "deep"
