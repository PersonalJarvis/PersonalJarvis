"""Persona loader: extracts the system-prompt block from `JARVIS_PERSONA.md`.

`JARVIS_PERSONA.md` is the authoritative handbook for the voice persona
(speech patterns, OUTPUT RULES, INTERACTION PATTERNS). The actual system
prompt is the plain text inside the first code fence (```) after the line
`## System-Prompt`. Everything around it (docs, router notes, sources) is
maintainer metadata — never sent to the LLM.

A second, much shorter block lives after `## System-Prompt (Compact)`: the
distilled persona for small self-hosted realtime brains, where the full 15k
characters cost multiple seconds of prompt processing EVERY turn (7.8 s
measured against qwen2.5:7b, 2026-08-07). Missing compact section falls back
to the full block — honest, never a silent truncation.

This loader:

1. Reads the MD file once and caches the result (`lru_cache`).
2. Extracts the first fence after the requested marker section.
3. Returns the plain text — ready for `_build_system_prompt()`.

Robust against:
- Missing file → empty string (silent fallback, no exception on the
  brain-init path).
- Missing code fence → empty string.
- CRLF line endings → normalized.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_PERSONA_MD_FILENAME = "JARVIS_PERSONA.md"
_SECTION_MARKER = "## System-Prompt"
_COMPACT_SECTION_MARKER = "## System-Prompt (Compact)"
_FENCE = "```"


def _persona_md_path() -> Path:
    """Path to the authoritative `JARVIS_PERSONA.md` in the `jarvis/brain/` package."""
    return Path(__file__).resolve().parent / _PERSONA_MD_FILENAME


def _extract_fence_after_marker(content: str, marker: str = _SECTION_MARKER) -> str:
    """The first ```...``` block after the given marker line.

    When the marker or the fence is missing, the function returns an empty
    string — the caller decides whether that is tolerable.
    """
    normalized = content.replace("\r\n", "\n")
    marker_idx = normalized.find(marker)
    if marker_idx == -1:
        return ""
    tail = normalized[marker_idx:]
    open_idx = tail.find(_FENCE)
    if open_idx == -1:
        return ""
    body_start = open_idx + len(_FENCE)
    # Skip the line break after the opening fence.
    newline_after_open = tail.find("\n", body_start)
    if newline_after_open == -1:
        return ""
    body_start = newline_after_open + 1
    close_idx = tail.find(_FENCE, body_start)
    if close_idx == -1:
        return ""
    return tail[body_start:close_idx].rstrip()


def _read_persona_md() -> str:
    path = _persona_md_path()
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("JARVIS_PERSONA.md missing at %s — persona layer empty.", path)
        return ""
    except OSError as exc:
        log.warning("JARVIS_PERSONA.md unreadable (%s) — persona layer empty.", exc)
        return ""


@lru_cache(maxsize=1)
def load_persona_prompt() -> str:
    """System-prompt block from `JARVIS_PERSONA.md` — cached for the process."""
    block = _extract_fence_after_marker(_read_persona_md())
    if not block:
        log.warning(
            "JARVIS_PERSONA.md: section '%s' or code fence missing — persona layer empty.",
            _SECTION_MARKER,
        )
    return block


@lru_cache(maxsize=1)
def load_compact_persona_prompt() -> str:
    """The distilled persona for small self-hosted brains — cached.

    Falls back to the FULL block when the compact section is absent: a
    persona that silently shrank to nothing would change the voice, while a
    temporarily slower prompt merely changes latency.
    """
    compact = _extract_fence_after_marker(_read_persona_md(), _COMPACT_SECTION_MARKER)
    if compact:
        return compact
    log.info(
        "JARVIS_PERSONA.md: no '%s' section — compact requests use the full persona.",
        _COMPACT_SECTION_MARKER,
    )
    return load_persona_prompt()


def invalidate_cache() -> None:
    """Invalidate the caches — for tests or hot reload after an MD edit."""
    load_persona_prompt.cache_clear()
    load_compact_persona_prompt.cache_clear()


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


def base_persona_prompt(*, compact: bool = False) -> str:
    """The persona WITHOUT the active mode layered on: custom override, else default.

    The custom override wins EVEN in compact mode — the user chose those
    exact words for their assistant, and user autonomy outranks the latency
    optimization (the trade-off is documented on the settings surface).

    Callers that build the brain's prompt want
    :func:`load_effective_persona_prompt` instead; this one exists for the
    settings surface, which edits the base text and must not show the user a
    mode block they cannot edit there.
    """
    custom = read_custom_prompt()
    if custom is not None:
        return custom
    if compact:
        return load_compact_persona_prompt()
    return default_persona_prompt()


def load_effective_persona_prompt(*, compact: bool = False) -> str:
    """The full persona the brain should use: the base plus the active mode.

    This is the ONE place the assistant's character is assembled, which is why
    a mode switch reaches the typing brain (``BrainManager._build_system_prompt``)
    and the speaking brain (``jarvis.realtime.session``) at the same moment and
    without a restart — both read this function fresh on every turn.

    The mode is appended, never substituted. The base carries the standing rules
    about honesty and about what is safe to say aloud; a mode that replaced it
    would drop those rules the moment somebody picked a chattier character, and
    the regression would read as a personality quirk rather than a safety bug.

    The default ``assistant`` mode contributes an empty block, so an install
    that never touches modes produces byte-identical prompts to before.
    """
    base = base_persona_prompt(compact=compact)
    try:
        from .modes import active_prompt_block

        block = active_prompt_block()
    except Exception as exc:  # noqa: BLE001 - a mode must never break a turn
        log.warning("Mode layer unavailable (%s) — base persona only.", exc)
        return base
    if not block:
        return base
    return f"{base}\n\n{block}" if base else block


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
