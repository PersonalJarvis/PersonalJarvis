"""Persona-Phrasen-Pool fuer die Speech-Pipeline.

2026-04-24: Alle Standard-Phrasen entfernt (User-Wunsch — klangen peinlich,
weil Jarvis sie undifferenziert in jedem Turn einstreute). Die Daten-Struktur
bleibt erhalten, damit Importe, ``iter_all_start_ack()`` und ``PhrasePicker``
weiterhin ohne Anpassung funktionieren — die Call-Sites in ``pipeline.py``
sind defensiv (``if pcm:`` / ``if phrase:``), ueberspringen bei leeren Pools
also schlicht das Abspielen.

Wenn du Phrasen zurueckhaben willst: einfach die passenden Listen wieder
fuellen (z.B. ``PHRASES["wake"]["de"] = ["Ja?"]``).
"""
from __future__ import annotations

import random
from collections import deque
from typing import Literal

Category = Literal["start_ack", "working", "completion", "pushback", "wake"]
Lang = Literal["de", "en"]


PHRASES: dict[str, dict[str, list[str]]] = {
    "start_ack":  {"de": [], "en": []},
    "working":    {"de": [], "en": []},
    "completion": {"de": [], "en": []},
    "pushback":   {"de": [], "en": []},
    "wake":       {"de": [], "en": []},
}


class PhrasePicker:
    """Wählt zufällig eine Phrase aus einer Kategorie, vermeidet unmittelbare Wiederholung.

    Pro (Kategorie, Sprache) wird eine Deque der zuletzt gespielten Phrasen geführt.
    Neue Auswahl kommt immer aus (alle - letzte_N). Wenn die Kategorie weniger als
    N+1 Phrasen hat, wird das Anti-Repeat-Fenster kleiner.
    """

    def __init__(self, anti_repeat_window: int = 3) -> None:
        self._window = anti_repeat_window
        self._recent: dict[tuple[str, str], deque[str]] = {}

    def pick(self, category: Category, lang: Lang = "de") -> str:
        pool = PHRASES.get(category, {}).get(lang, [])
        if not pool:
            # Fallback: andere Sprache
            other: Lang = "en" if lang == "de" else "de"
            pool = PHRASES.get(category, {}).get(other, [])
        if not pool:
            return ""
        key = (category, lang)
        window = min(self._window, max(1, len(pool) - 1))
        recent = self._recent.setdefault(key, deque(maxlen=window))
        candidates = [p for p in pool if p not in recent]
        if not candidates:
            candidates = pool
        choice = random.choice(candidates)
        recent.append(choice)
        return choice


def iter_all_start_ack() -> list[tuple[str, str]]:
    """Alle Start-Ack-Phrasen fürs Pre-Rendering: [(lang, phrase), ...]."""
    return [
        (lang, phrase)
        for lang in ("de", "en")
        for phrase in PHRASES["start_ack"][lang]
    ]
