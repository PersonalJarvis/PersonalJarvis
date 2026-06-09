"""Markdown- und Plain-Text-Renderer fuer Voice-Sessions.

Zwei Output-Formate:

- ``markdown`` — strukturiert mit Headings/Listen, fuer Copy in Chat-
  Apps, Issues, Notion/Obsidian. Nutzt Emojis als visuelle Anker
  (🎤 User, 🧠 Jarvis, 🔧 Tool, ⏱ Latenz) — sind Single-Codepoints,
  kompatibel mit allen UTF-8-Targets.

- ``plain`` — die *schlichte* Gespraechs-Transkription, die ein Mensch
  zum Weitergeben kopiert. Reiner Dialog mit ``Du:`` / ``Jarvis:`` als
  Sprecher, eine schlanke Kopfzeile (Datum + Dauer). Keine Emojis, keine
  Markdown-Marker und keine Pro-Turn-Telemetrie (Tier/Provider/Tokens/
  Kosten/Latenz) — diese Maschinen-Details leben im ``json``-Export.
  Beim Einfuegen in Chat, Notiz oder E-Mail entsteht so kein "AI-Slop".

Beide Renderer arbeiten auf den gleichen ``VoiceSessionRow`` +
``VoiceTurnRow``-Inputs aus ``store.py``. Die rohen ``VoiceEventRow``
sind nicht im Output — das ist Detail-Replay-Daten und nicht fuer
Copy-Paste-Konsum gedacht.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models import VoiceSessionRow, VoiceTurnRow


def format_session_markdown(
    session: VoiceSessionRow,
    turns: Iterable[VoiceTurnRow],
) -> str:
    """Markdown-Version fuer reichen Copy-Paste-Konsum."""
    turns_list = list(turns)
    lines: list[str] = []

    # --- Header ---
    started = _fmt_dt(session.started_ms)
    duration = _fmt_duration(session.started_ms, session.ended_ms)
    lines.append(f"# Voice-Session — {started}")
    lines.append("")
    lines.append(f"- **Dauer:** {duration}")
    lines.append(f"- **Turns:** {session.turn_count}")
    lines.append(f"- **Sprache:** {session.language}")
    if session.wake_keyword:
        lines.append(f"- **Wake-Word:** {session.wake_keyword}")
    if session.hangup_reason:
        lines.append(f"- **Beendet durch:** {_pretty_hangup(session.hangup_reason)}")
    if session.total_cost_usd > 0:
        lines.append(f"- **Kosten:** ${session.total_cost_usd:.4f}")
    if session.total_tokens_in or session.total_tokens_out:
        lines.append(
            f"- **Tokens:** {session.total_tokens_in} in · {session.total_tokens_out} out"
        )
    if session.providers_used:
        lines.append(f"- **Provider:** {', '.join(session.providers_used)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Turns ---
    if not turns_list:
        lines.append("_(keine Turns aufgezeichnet)_")
        return "\n".join(lines)

    for t in turns_list:
        lines.append(f"## Turn {t.idx + 1}  ·  {_fmt_dt(t.started_ms, time_only=True)}")
        lines.append("")

        if t.user_text:
            lines.append(f"**🎤 User** _(de={t.user_lang})_")
            lines.append("")
            lines.append(f"> {t.user_text}")
            lines.append("")

        # Brain-Meta-Zeile, nur wenn was substantielles da ist
        meta_parts: list[str] = []
        if t.tier:
            meta_parts.append(f"Tier: `{t.tier}`")
        if t.provider:
            meta_parts.append(f"Provider: `{t.provider}`")
        if t.model:
            meta_parts.append(f"Model: `{t.model}`")
        if t.tokens_in or t.tokens_out:
            meta_parts.append(f"{t.tokens_in}+{t.tokens_out} tok")
        if t.cost_usd > 0:
            meta_parts.append(f"${t.cost_usd:.4f}")
        if meta_parts:
            lines.append(f"**🧠 Jarvis dachte:** {' · '.join(meta_parts)}")
            lines.append("")

        if t.tool_calls:
            tools_pretty = ", ".join(f"`{tc}`" for tc in t.tool_calls)
            lines.append(f"**🔧 Tools:** {tools_pretty}")
            lines.append("")

        if t.jarvis_text:
            lines.append(f"**🔊 Jarvis sagte** _(de={t.jarvis_lang})_")
            lines.append("")
            lines.append(f"> {t.jarvis_text}")
            lines.append("")

        if t.latency_total_ms > 0 or t.think_ms > 0 or t.speak_ms > 0:
            parts: list[str] = []
            if t.latency_total_ms > 0:
                parts.append(f"Gesamt {_fmt_ms(t.latency_total_ms)}")
            if t.think_ms > 0:
                parts.append(f"nachgedacht {_fmt_ms(t.think_ms)}")
            if t.speak_ms > 0:
                parts.append(f"gesprochen {_fmt_ms(t.speak_ms)}")
            lines.append(f"**⏱ Latenz:** {' · '.join(parts)}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_session_plain(
    session: VoiceSessionRow,
    turns: Iterable[VoiceTurnRow],
) -> str:
    """Schlichte Gespraechs-Transkription — reiner Dialog, kein Slop.

    Aufbau: eine Kopfzeile (``Voice-Session · Datum · Dauer``), dann jede
    Aeusserung mit ``Du:`` bzw. ``Jarvis:`` als Sprecher, durch Leerzeilen
    getrennt. Bewusst *weggelassen*: Emojis, Markdown, sowie jede
    Pro-Turn-Telemetrie (Tier/Provider/Model/Tokens/Kosten/Latenz/Tools).
    Wer diese Maschinen-Details braucht, nutzt den ``json``-Export.
    """
    turns_list = list(turns)
    lines: list[str] = []

    # --- Schlanke Kopfzeile: "Voice-Session · 07.06.2026, 19:24 · 1 min 42 s"
    header_bits = [_fmt_dt_human(session.started_ms)]
    duration = _fmt_duration(session.started_ms, session.ended_ms)
    if duration:
        header_bits.append(duration)
    lines.append("Voice-Session · " + " · ".join(header_bits))
    lines.append("")

    if not turns_list:
        lines.append("(keine Turns aufgezeichnet)")
        return "\n".join(lines).rstrip() + "\n"

    # Reiner Dialog: pro Turn die vorhandenen Aeusserungen, durch Leer-
    # zeilen getrennt. Turns ohne Antwort (z.B. "auflegen") erzeugen keine
    # leere "Jarvis:"-Zeile.
    blocks: list[str] = []
    for t in turns_list:
        if t.user_text:
            blocks.append(f"Du: {t.user_text}")
        if t.jarvis_text:
            blocks.append(f"Jarvis: {t.jarvis_text}")

    if blocks:
        lines.append("\n\n".join(blocks))

    return "\n".join(lines).rstrip() + "\n"


# --- Helpers ----------------------------------------------------------


def _local_dt(ts_ms: int) -> datetime:
    """Wall-clock-ms -> lokale ``datetime`` (eine Stelle fuer die TZ-Logik)."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone()


def _fmt_dt(ts_ms: int, *, time_only: bool = False) -> str:
    dt = _local_dt(ts_ms)
    if time_only:
        return dt.strftime("%H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dt_human(ts_ms: int) -> str:
    """Schlankes deutsches Datum fuer die Kopfzeile: ``07.06.2026, 19:24``."""
    return _local_dt(ts_ms).strftime("%d.%m.%Y, %H:%M")


def _fmt_duration(started_ms: int, ended_ms: int | None) -> str:
    if ended_ms is None:
        return "läuft noch"
    secs = (ended_ms - started_ms) / 1000.0
    if secs < 60:
        return f"{secs:.1f} s"
    mins = int(secs // 60)
    rem = secs - mins * 60
    if mins < 60:
        return f"{mins} min {rem:.0f} s"
    hours = mins // 60
    mins = mins % 60
    return f"{hours} h {mins} min"


def _fmt_ms(ms: int) -> str:
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms / 1000.0:.2f} s"


_HANGUP_LABELS = {
    "voice_pattern": "Sprachbefehl (\"auflegen\")",
    "hotkey": "Hotkey (F1+F2)",
    "idle_timeout": "Inaktivität",
    "shutdown": "App-Shutdown",
    "error": "Fehler",
}


def _pretty_hangup(reason: str) -> str:
    return _HANGUP_LABELS.get(reason, reason or "unbekannt")


__all__ = ["format_session_markdown", "format_session_plain"]
