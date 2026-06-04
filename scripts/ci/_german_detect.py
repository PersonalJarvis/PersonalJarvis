#!/usr/bin/env python3
"""German-text heuristic shared by the CI language-policy gate.

The repo's Output Language Policy (CLAUDE.md, HIGHEST PRIORITY) requires every
committed artifact to be English. This module provides ``looks_german`` — a
lightweight, dependency-free heuristic the ``check_no_new_german`` gate runs over
newly added diff lines.

Design notes:
  * Umlauts (äöüß) are a near-perfect German signal in this codebase and flag a
    line on their own.
  * "Strong" tokens are words that effectively never appear as a standalone token
    in English source/text — a single hit flags the line.
  * "Weak" tokens are articles/conjunctions/prepositions that can collide with
    English false friends (e.g. "die", "den"), so TWO distinct hits are required.
  * Matching is whole-word (regex word boundaries), so German fragments inside
    English identifiers (e.g. "der" in "render") never match.

This is a heuristic, not a parser: it tolerates rare misses and is paired with a
``# i18n-allow`` inline escape plus a path allowlist in the gate itself.
"""
from __future__ import annotations

import re

_UMLAUTS = frozenset("äöüßÄÖÜ")

# A SINGLE occurrence flags the line. Includes common ASCII-transliterations
# (ue/oe/ae/ss) so de-umlauted German is still caught.
_STRONG: frozenset[str] = frozenset(
    {
        "nicht", "kein", "keine", "keinen", "keiner", "keinem",
        "wurde", "wurden", "wird", "werden",
        "muss", "müssen", "muessen", "kann", "können", "koennen",
        "soll", "sollte", "sollten",
        "öffnen", "oeffnen", "schließen", "schliessen", "schließe",
        "geöffnet", "geschlossen",
        "löschen", "loeschen", "gelöscht", "geloescht",
        "fehler", "fehlgeschlagen", "erfolgreich", "ungültig", "ungueltig",
        "einstellung", "einstellungen", "benutzer", "abbrechen",
        "speichern", "gespeichert",
        "zurück", "zurueck", "hinzufügen", "hinzufuegen",
        "auswählen", "auswaehlen",
        "für", "fuer", "über", "ueber", "ausführen", "ausfuehren", "ausgeführt",
        "verfügbar", "verfuegbar", "verbindung", "verbunden", "getrennt",
        "willkommen", "achtung", "warnung", "vorschau", "nachricht", "anfrage",
        "fortfahren", "weiter", "fertig", "läuft", "laeuft",
        "geladen", "lädt", "laedt",
        "gestartet", "beendet", "aktualisieren", "anzeigen", "bereit",
        "wähle", "waehle", "gerät", "geräte", "geraete",
        "sprache", "sprachen",
        "übersetzen", "uebersetzen", "gespräch", "gespraech",
        "verlauf", "eintrag", "anrede", "pronomen",
    }
)

# At least TWO distinct hits required (English false-friend safety margin).
_WEAK: frozenset[str] = frozenset(
    {
        "der", "die", "das", "den", "dem", "des",
        "ein", "eine", "einen", "einem", "einer",
        "und", "oder", "aber", "sondern",
        "mit", "von", "vom", "zur", "zum", "beim", "aus", "nach", "bei",
        "auf", "unter",
        "ist", "sind", "waren", "haben", "hatte",
        "wenn", "weil", "dass", "damit", "sich",
        "auch", "noch", "schon", "nur", "sehr", "immer",
    }
)

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]+")


def looks_german(text: str) -> bool:
    """Return True if ``text`` looks like German prose/labels.

    Conservative by construction: umlaut OR one strong token OR two distinct
    weak tokens. Whole-word matching avoids substring false positives.
    """
    if any(ch in _UMLAUTS for ch in text):
        return True
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return False
    if any(w in _STRONG for w in words):
        return True
    weak_hits = {w for w in words if w in _WEAK}
    return len(weak_hits) >= 2
