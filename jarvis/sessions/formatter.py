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

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from .constants import SPOKEN_KIND_WITHHELD
from .models import VoiceEventRow, VoiceSessionRow, VoiceTurnRow


@dataclass(frozen=True)
class _JarvisOutput:
    ts_ms: int
    seq: int
    text: str
    kind: str
    detail: str | None = None
    is_reply: bool = False


def _events_by_turn(events: Iterable[VoiceEventRow] | None) -> dict[str, list[VoiceEventRow]]:
    grouped: dict[str, list[VoiceEventRow]] = {}
    for e in events or []:
        grouped.setdefault(e.turn_id or "", []).append(e)
    return grouped


def _jarvis_outputs_for_turn(
    turn: VoiceTurnRow,
    events: Iterable[VoiceEventRow] | None,
) -> list[_JarvisOutput]:
    """Return every Jarvis output for a turn in the order it was produced.

    ``SpeechSpoken`` is authoritative for output accepted by the audible path.
    ``ResponseGenerated`` remains a compatibility fallback for sessions recorded
    before reply playback confirmation existed. Rendering by timestamp prevents
    the plain transcript from showing a preamble after the final answer.
    """
    items: list[_JarvisOutput] = []
    generated_replies: list[_JarvisOutput] = []
    saw_confirmed_reply = False
    fallback_seq = 1_000_000_000
    for e in events or []:
        if e.kind == "SpeechSpoken":
            text = str(e.payload.get("text", "")).strip()
            if not text:
                continue
            raw_detail = e.payload.get("detail")
            detail = str(raw_detail).strip() if raw_detail else None
            spoken_kind = str(e.payload.get("spoken_kind", "other"))
            is_reply = spoken_kind == "reply"
            saw_confirmed_reply = saw_confirmed_reply or is_reply
            items.append(
                _JarvisOutput(
                    ts_ms=e.ts_ms,
                    seq=e.seq or 0,
                    text=text,
                    kind=spoken_kind,
                    detail=detail,
                    is_reply=is_reply,
                )
            )
            continue
        if e.kind == "ResponseGenerated":
            text = str(e.payload.get("text", "")).strip()
            if not text:
                continue
            generated_replies.append(
                _JarvisOutput(
                    ts_ms=e.ts_ms,
                    seq=e.seq or 0,
                    text=text,
                    kind="reply",
                    is_reply=True,
                )
            )

    if not saw_confirmed_reply:
        items.extend(generated_replies)

    if turn.jarvis_text and not generated_replies and not saw_confirmed_reply:
        items.append(
            _JarvisOutput(
                ts_ms=turn.ended_ms or turn.started_ms,
                seq=fallback_seq,
                text=turn.jarvis_text,
                kind="reply",
                is_reply=True,
            )
        )

    return sorted(_fold_withheld_twins(items), key=lambda it: (it.ts_ms, it.seq))


def _fold_withheld_twins(items: list[_JarvisOutput]) -> list[_JarvisOutput]:
    """Drop a ``withheld`` documentation event whose text a reply re-spoke.

    A scrub cancel publishes the withheld provider rendering AND hands the same
    text to the surface TTS, which confirms it as a real reply — two events,
    one utterance (live forensic 2026-07-17 10:04). Rendering both makes the
    transcript read as Jarvis repeating itself verbatim. The audible reply
    wins; the twin's abort detail is folded onto it so the markdown export
    keeps the forensic trace. A withheld event with no spoken twin still
    renders — it is then the only honest record of what the user heard.
    """
    reply_texts = {it.text for it in items if it.is_reply}
    folded_details: dict[str, str] = {}
    kept: list[_JarvisOutput] = []
    for it in items:
        if not it.is_reply and it.kind == SPOKEN_KIND_WITHHELD and it.text in reply_texts:
            if it.detail:
                folded_details.setdefault(it.text, it.detail)
            continue
        kept.append(it)
    result: list[_JarvisOutput] = []
    for it in kept:
        if it.is_reply and it.detail is None and it.text in folded_details:
            result.append(replace(it, detail=folded_details.pop(it.text)))
        else:
            result.append(it)
    return result


def format_session_markdown(
    session: VoiceSessionRow,
    turns: Iterable[VoiceTurnRow],
    events: Iterable[VoiceEventRow] | None = None,
) -> str:
    """Markdown-Version fuer reichen Copy-Paste-Konsum."""
    turns_list = list(turns)
    events_map = _events_by_turn(events)
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

    for display_index, t in enumerate(turns_list, start=1):
        lines.append(f"## Turn {display_index}  ·  {_fmt_dt(t.started_ms, time_only=True)}")
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

        outputs = _jarvis_outputs_for_turn(t, events_map.get(t.id, []))
        if outputs:
            # Which voice actually spoke (user request 2026-07-17) — small
            # print next to the spoken block; the speaker can differ from the
            # brain provider (e.g. a surface-TTS readback in a realtime turn).
            voice_note = ""
            if getattr(t, "voice_name", ""):
                spoken_by = t.voice_name
                if getattr(t, "voice_provider", ""):
                    spoken_by += f" @ {t.voice_provider}"
                voice_note = f" · _Stimme: `{spoken_by}`_"
            lines.append(f"**🔊 Jarvis sagte** _(de={t.jarvis_lang})_{voice_note}")
            lines.append("")
            for output in outputs:
                if output.is_reply and t.awaiting_confirmation:
                    # The reply is a pending yes/no confirmation, not a normal
                    # answer — tag it like the other english spoken_kind tags so
                    # the transcript does not read it as a settled response.
                    lines.append(f"> _(awaiting confirmation)_ {output.text}")
                elif output.is_reply:
                    lines.append(f"> {output.text}")
                else:
                    lines.append(f"> _({output.kind})_ {output.text}")
                if output.detail:
                    # Technical diagnostic that was NOT spoken (e.g. a failed
                    # Computer-Use exit code) — kept for debugging.
                    lines.append(f"> _detail:_ `{output.detail}`")
                lines.append("")
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
    events: Iterable[VoiceEventRow] | None = None,
) -> str:
    """Schlichte Gespraechs-Transkription — reiner Dialog, kein Slop.

    Aufbau: eine Kopfzeile (``Voice-Session · Datum · Dauer``), dann jede
    Aeusserung mit ``Du:`` bzw. ``Jarvis:`` als Sprecher, durch Leerzeilen
    getrennt. Bewusst *weggelassen*: Emojis, Markdown, sowie jede
    Pro-Turn-Telemetrie (Tier/Provider/Model/Tokens/Kosten/Latenz/Tools).
    Wer diese Maschinen-Details braucht, nutzt den ``json``-Export.

    Every VOICED non-reply phrase (timeout / clarify / announcement text from
    the SpeechSpoken events) also appears here as a ``Jarvis:`` line — without
    a kind tag, to keep the dialogue clean. That way even the plain export
    documents everything the user heard.
    """
    turns_list = list(turns)
    events_map = _events_by_turn(events)
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
        for output in _jarvis_outputs_for_turn(t, events_map.get(t.id, [])):
            # The clean transcript reads as dialogue — the technical ``detail``
            # (exit codes, harness reasons) stays out to avoid AI-slop.
            blocks.append(f"Jarvis: {output.text}")

    if blocks:
        lines.append("\n\n".join(blocks))

    return "\n".join(lines).rstrip() + "\n"


# --- Helpers ----------------------------------------------------------


def _local_dt(ts_ms: int) -> datetime:
    """Wall-clock-ms -> lokale ``datetime`` (eine Stelle fuer die TZ-Logik)."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).astimezone()


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
