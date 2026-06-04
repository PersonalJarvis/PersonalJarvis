"""Capability-honest summary helpers for the Critic loop.

``summarise_from_tool_calls`` builds a one-line German TTS summary from
real tool-call evidence extracted from worker output.  It is the ONLY
path that may feed ``summary_de`` on the success (approve) branch — the
raw worker text-claim is explicitly forbidden as a TTS source because it
is hearsay (BUG-LIVE-02 / LIVE-VERIFY-2026-05-15).

Design constraints:
- No LLM calls (AP-11 compliance — latency mandate).
- Deterministic: same input → same output, safe for replay.
- If ``calls`` is empty the caller should NOT be on the success path at
  all (``enforce_capability_honesty`` catches that upstream), but we
  return a safe German fallback rather than crashing.
"""
from __future__ import annotations

import re
from typing import Sequence


# Maximum characters for a TTS summary line (mirrors readback.MAX_VOICE_CHARS).
_MAX_SUMMARY_CHARS: int = 200

# Recognised tool-name patterns → human-readable German action phrase.
# Order matters: more-specific patterns first.
_TOOL_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"send_mail|send_email|gmail\.send", re.I), "E-Mail gesendet"),
    (re.compile(r"create_event|calendar\.add|add_event", re.I), "Termin eingetragen"),
    (re.compile(r"file_write|write_file|Write\b", re.I), "Datei geschrieben"),
    (re.compile(r"Edit\b|str_replace_editor|MultiEdit", re.I), "Datei bearbeitet"),
    (re.compile(r"run_shell|exec\b|bash\b", re.I), "Befehl ausgefuehrt"),
    (re.compile(r"search_web|web_search|browse", re.I), "Suche durchgefuehrt"),
    (re.compile(r"Read\b|read_file", re.I), "Datei gelesen"),
    (re.compile(r"Glob\b|Grep\b", re.I), "Dateien durchsucht"),
]

_FALLBACK_PHRASE: str = "Aufgabe ausgefuehrt"


def summarise_from_tool_calls(calls: Sequence[str]) -> str:
    """Return a one-line German summary derived solely from tool-call evidence.

    Args:
        calls: Sequence of tool-name strings extracted from worker output
               (e.g. ``["Write", "Edit"]`` or ``["send_mail"]``).  May be
               empty — callers upstream (``enforce_capability_honesty``)
               are responsible for ensuring this is only called when at
               least one call is present.

    Returns:
        A short German summary string suitable for TTS, capped at
        ``_MAX_SUMMARY_CHARS`` characters.  Never an empty string.
    """
    if not calls:
        return _FALLBACK_PHRASE

    # Deduplicate while preserving first-seen order.
    seen: set[str] = set()
    unique: list[str] = []
    for c in calls:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    # Map each unique tool name to a phrase.
    phrases: list[str] = []
    used_phrases: set[str] = set()
    for tool_name in unique:
        matched = False
        for pattern, phrase in _TOOL_PHRASES:
            if pattern.search(tool_name):
                if phrase not in used_phrases:
                    phrases.append(phrase)
                    used_phrases.add(phrase)
                matched = True
                break
        if not matched and tool_name not in used_phrases:
            # Use the raw tool name as a last-resort phrase (still real evidence).
            phrases.append(tool_name)
            used_phrases.add(tool_name)

    if not phrases:
        return _FALLBACK_PHRASE

    summary = "; ".join(phrases) + "."
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS].rstrip(" ;") + "."
    return summary


__all__ = ["summarise_from_tool_calls"]
