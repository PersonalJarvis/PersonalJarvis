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


# ---------------------------------------------------------------------------
# Custom (user-editable) system prompt override.
#
# The user can replace the packaged JARVIS persona with their own Markdown from
# the Settings UI and reset back to the shipped default with one click. The
# override is a sidecar file (``data/custom_system_prompt.md``); the packaged
# ``JARVIS_PERSONA.md`` is never mutated, so "reset to default" is just a delete
# and the default is always recoverable.
#
# Unlike the default block (cached for the process lifetime above), the override
# is read FRESH on every call so an edit takes effect on the next turn without a
# restart — ``_build_system_prompt`` reassembles the prompt each turn anyway.
# ---------------------------------------------------------------------------

_CUSTOM_PROMPT_FILENAME = "custom_system_prompt.md"


def custom_prompt_path() -> Path:
    """Path to the user's custom system-prompt override file.

    Lives under the canonical ``DATA_DIR`` (alongside core memory) so it is
    user-writable, git-ignored, and portable to a headless VPS. Resolved at call
    time from ``jarvis.core.config.DATA_DIR`` so tests can redirect it.
    """
    from jarvis.core import config as core_config

    return core_config.DATA_DIR / _CUSTOM_PROMPT_FILENAME


def read_custom_prompt() -> str | None:
    """Return the stored custom prompt, or ``None`` when there is no usable one.

    A missing file, an unreadable file, or a whitespace-only file all count as
    "no custom prompt" — the caller falls back to the packaged default. The
    returned text is stripped of surrounding whitespace.
    """
    path = custom_prompt_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("custom_system_prompt.md not readable (%s) — using default.", exc)
        return None
    text = text.strip()
    return text or None


def has_custom_prompt() -> bool:
    """True when a non-empty custom prompt override is in effect."""
    return read_custom_prompt() is not None


def default_persona_prompt() -> str:
    """The packaged default persona block (from ``JARVIS_PERSONA.md``)."""
    return load_persona_prompt()


def load_effective_persona_prompt() -> str:
    """The persona block the brain should use: custom override if set, else default."""
    custom = read_custom_prompt()
    if custom is not None:
        return custom
    return default_persona_prompt()


def save_custom_prompt(text: str) -> None:
    """Persist a custom system prompt atomically (tempfile + ``os.replace``).

    Written UTF-8 without a BOM (AP-7: a BOM breaks downstream readers). The
    text is stripped of surrounding whitespace before writing.
    """
    import os
    import tempfile

    body = (text or "").strip()
    path = custom_prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".custom_system_prompt.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def reset_custom_prompt() -> bool:
    """Delete the custom override so the brain reverts to the packaged default.

    Returns True when a file was removed, False when there was nothing to remove
    (idempotent — a double reset is not an error).
    """
    path = custom_prompt_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
