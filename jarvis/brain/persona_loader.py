"""Persona-Loader: extrahiert den System-Prompt-Block aus `JARVIS_PERSONA.md`.

`JARVIS_PERSONA.md` ist das autoritative Handbuch für die Jarvis-Voice-Persona
(10 Sprechmuster, OUTPUT RULES, INTERACTION PATTERNS). Der eigentliche
System-Prompt liegt als Plain-Text innerhalb des ersten Code-Fence (```) nach
der Zeile `## System-Prompt`. Alles drumherum (Doku, Router-Empfehlung,
Quellen) ist Metadaten — nicht für das LLM.

Dieser Loader:

1. Liest die MD-Datei einmal und cached das Ergebnis (`lru_cache`).
2. Extrahiert den ersten Fence nach der Marker-Section.
3. Liefert den Plain-Text zurück — bereit für `_build_system_prompt()`.

Robust gegen:
- Fehlende Datei → leerer String (silent fallback, keine Exception in der
  Brain-Init-Pfad).
- Fehlender Code-Fence → leerer String.
- CRLF-Line-Endings → werden normalisiert.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_PERSONA_MD_FILENAME = "JARVIS_PERSONA.md"
_SECTION_MARKER = "## System-Prompt"
_FENCE = "```"


def _persona_md_path() -> Path:
    """Pfad zur autoritativen `JARVIS_PERSONA.md` im `jarvis/brain/`-Package."""
    return Path(__file__).resolve().parent / _PERSONA_MD_FILENAME


def _extract_fence_after_marker(content: str) -> str:
    """Holt den ersten ```...```-Block nach der Zeile `## System-Prompt`.

    Wenn der Marker oder der Fence fehlt, liefert die Funktion einen leeren
    String — der Aufrufer entscheidet, ob das tolerabel ist.
    """
    normalized = content.replace("\r\n", "\n")
    marker_idx = normalized.find(_SECTION_MARKER)
    if marker_idx == -1:
        return ""
    tail = normalized[marker_idx:]
    open_idx = tail.find(_FENCE)
    if open_idx == -1:
        return ""
    body_start = open_idx + len(_FENCE)
    # Zeilenumbruch nach dem Opening-Fence überspringen.
    newline_after_open = tail.find("\n", body_start)
    if newline_after_open == -1:
        return ""
    body_start = newline_after_open + 1
    close_idx = tail.find(_FENCE, body_start)
    if close_idx == -1:
        return ""
    return tail[body_start:close_idx].rstrip()


@lru_cache(maxsize=1)
def load_persona_prompt() -> str:
    """System-Prompt-Block aus `JARVIS_PERSONA.md` — gecacht über Prozess-Leben.

    Return: Prompt-Text oder leerer String wenn Datei/Fence nicht gefunden.
    """
    path = _persona_md_path()
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("JARVIS_PERSONA.md fehlt an %s — Persona-Layer leer.", path)
        return ""
    except OSError as exc:
        log.warning("JARVIS_PERSONA.md nicht lesbar (%s) — Persona-Layer leer.", exc)
        return ""

    block = _extract_fence_after_marker(content)
    if not block:
        log.warning(
            "JARVIS_PERSONA.md: Section '%s' oder Code-Fence fehlt — Persona-Layer leer.",
            _SECTION_MARKER,
        )
    return block


def invalidate_cache() -> None:
    """Cache invalidieren — für Tests oder Hot-Reload nach MD-Edit."""
    load_persona_prompt.cache_clear()
