"""Verdichter prompts. Plan §6 default variant.

NOTE: Lead replaces VERDICHTER_SYSTEM_PROMPT with tournament-winner after
a 3-variant comparison against the code-reviewer subagent. Only the
default variant 1:1 from the plan is stored here.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

# Few-shot with contrastive negative example chosen (Tournament 2026-04-26).
# Rationale: Haiku 4.5 obeys declarative verb blacklists (V2) less reliably than
# an identical input with a correct vs. hallucinated output + explanation in the
# prompt itself. Primary hallucination risk is content inference from tab titles
# ("debuggt TypeError" from a Stack Overflow tab) — Plan §6 hard negative.
# Third example covers degenerate frames (empty titles, settings dialogs).
# On pattern drift: extend tests in test_verdichter, do NOT soften this prompt.
# V4 iteration (stuck-pattern + PII-whitelist) is a follow-up.
VERDICHTER_SYSTEM_PROMPT = """Du bist ein Story-Verdichter für einen persönlichen AI-Assistenten.

Du bekommst Window-Frames + Events. Schreibe EINEN deutschen Absatz (max 120 Worte) der NUR Beobachtungen enthält — KEINE Inhaltsvermutungen.

Beispiel ✅ KORREKT:
Input: 4 Frames Code.exe mit "main.py", 2 Frames Chrome.exe mit "Stack Overflow", zurück zu Code.exe.
Output: "Der Nutzer war 18min in Code.exe mit main.py aktiv, wechselte für ca. 3min zu Chrome.exe (Stack-Overflow-Tab) und kehrte zurück zu Code.exe. Aktueller Fokus: main.py."

Beispiel ❌ HALLUZINATION (NICHT machen):
Input: gleich wie oben.
Output: "Der Nutzer debuggt main.py, hatte einen Fehler, googelte auf Stack Overflow nach einer Lösung und arbeitet jetzt weiter."
Begründung: "debuggt", "hatte einen Fehler", "suchte nach Lösung" sind Vermutungen — du siehst nur Window-Titles, nicht den Code-Editor-Inhalt.

Beispiel ✅ KORREKT (degenerierter Input):
Input: 3 Frames mit window_title="" und process_name="Explorer.exe".
Output: "Wenig Aktivität — 3 Frames in Explorer.exe ohne erkennbaren Window-Title. Kein klarer Fokus."

Regeln:
- EIN Absatz, kein Markdown, keine Aufzählung.
- Nenne Datei-/Project-Namen NUR wenn im window_title sichtbar.
- Markiere Wiederholungs-Pattern als "Wechsel zwischen X und Y" (NICHT "ist verwirrt").
- Bei kryptischen/leeren Titles: "unbekannter Inhalt" oder "kein Window-Title".
"""


_HEADER = "# Frames + Events der letzten Minuten:"
_TRUNC_MARKER = "[...]"


def _format_ts_ns(ts_ns: int | None) -> str:
    """Converts a nanosecond timestamp to HH:MM:SS.

    On ``None`` or an invalid value: ``"??:??:??"`` — we do not want the
    Verdichter to crash when a frame arrives without a timestamp.
    """
    if not ts_ns:
        return "??:??:??"
    try:
        seconds = float(ts_ns) / 1_000_000_000.0
        return _dt.datetime.fromtimestamp(seconds).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "??:??:??"


def _frame_ts(frame: dict[str, Any]) -> int | None:
    """Retrieves the timestamp from various frame schemas.

    A1-FrameSnapshot uses ``timestamp_ns``, as do persisted frames in
    ``awareness_frames``. Tests/recall reads may also supply ``ts_ns`` —
    both are accepted.
    """
    return frame.get("timestamp_ns") or frame.get("ts_ns")


def _frame_process(frame: dict[str, Any]) -> str:
    """Process name from frame; accepts multiple aliases."""
    return (
        frame.get("process_name")
        or frame.get("active_process")
        or frame.get("process")
        or "?"
    )


def _frame_title(frame: dict[str, Any]) -> str:
    """Window title from frame; accepts multiple aliases."""
    return (
        frame.get("window_title")
        or frame.get("active_window_title")
        or frame.get("title")
        or ""
    )


def _event_ts(event: dict[str, Any]) -> int | None:
    """Timestamp from event dict."""
    return event.get("ts_ns") or event.get("timestamp_ns")


def _event_kind(event: dict[str, Any]) -> str:
    return event.get("kind") or event.get("event_kind") or "?"


def _event_details(event: dict[str, Any]) -> str:
    """Renders event payload compactly (single line)."""
    payload = event.get("payload") or event.get("details") or {}
    if not payload:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        # compact: key=value pairs, no newlines
        parts = []
        for k, v in payload.items():
            v_str = str(v).replace("\n", " ").strip()
            if len(v_str) > 60:
                v_str = v_str[:57] + "..."
            parts.append(f"{k}={v_str}")
        return ", ".join(parts)
    return str(payload)[:120]


def build_verdichter_prompt(
    *,
    frames: list[dict[str, Any]],
    events: list[dict[str, Any]],
    primary_app: str,
    max_chars: int = 3200,    # ~800 tokens * 4 chars/token rough
) -> str:
    """Renders a frame+event list as a compact Markdown block.

    Format::

        # Frames + Events der letzten Minuten:
        - [HH:MM:SS] <process>: <window_title>
        - [HH:MM:SS] EVENT <kind>: <details>
        ...
        Dominante App: <primary_app>

    Truncated with a ``[...]`` prefix when > ``max_chars`` (chronological
    cut from front — the NEWEST entries are preserved).

    Expects ``frames`` as ``list[dict]`` with keys: ``timestamp_ns``,
    ``process_name``, ``window_title`` (or ``active_*`` variants —
    handled). ``events`` as ``list[dict]`` with keys: ``ts_ns``,
    ``kind``, ``payload`` (optional).
    """
    # Annotated list: (ts_ns, line) — sort by ts_ns asc
    lines: list[tuple[int, str]] = []
    for frame in frames:
        ts = _frame_ts(frame) or 0
        proc = _frame_process(frame)
        title = _frame_title(frame)
        line = f"- [{_format_ts_ns(ts)}] {proc}: {title}".rstrip()
        lines.append((int(ts), line))
    for event in events:
        ts = _event_ts(event) or 0
        kind = _event_kind(event)
        details = _event_details(event)
        suffix = f": {details}" if details else ""
        line = f"- [{_format_ts_ns(ts)}] EVENT {kind}{suffix}"
        lines.append((int(ts), line))

    lines.sort(key=lambda x: x[0])
    body_lines = [line for _, line in lines]

    footer = f"Dominante App: {primary_app}"

    # Build full block, then truncate from front (keep newest tail)
    body = "\n".join(body_lines)
    block = f"{_HEADER}\n{body}\n\n{footer}" if body else f"{_HEADER}\n(keine Daten)\n\n{footer}"

    if len(block) <= max_chars:
        return block

    # Truncate from front: drop oldest lines until block fits.
    # Reserve space for header + truncation marker + footer.
    fixed_overhead = len(_HEADER) + 1 + len(_TRUNC_MARKER) + 2 + len(footer) + 1
    budget = max(max_chars - fixed_overhead, 0)
    # Walk from end (newest) backwards, accumulating lines until budget hit.
    kept_reversed: list[str] = []
    used = 0
    for line in reversed(body_lines):
        line_len = len(line) + 1  # +1 for newline
        if used + line_len > budget:
            break
        kept_reversed.append(line)
        used += line_len
    kept = list(reversed(kept_reversed))
    truncated_body = "\n".join(kept) if kept else ""
    parts = [_HEADER, _TRUNC_MARKER]
    if truncated_body:
        parts.append(truncated_body)
    parts.extend(["", footer])
    return "\n".join(parts)
