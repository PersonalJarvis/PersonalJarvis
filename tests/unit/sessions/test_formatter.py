"""Tests for the voice-session transcript renderers.

Focus: the ``plain`` renderer is the *clean* conversation transcript a
human copies to share — pure dialogue, no emojis, no Markdown markers and
none of the per-turn telemetry (tier/provider/tokens/cost/latency). Those
guards exist because the old plain output leaked ``[USER]``/``[BRAIN]``
developer tags and the markdown output leaks emojis — both read as
"AI-slop" when pasted into a chat or note.
"""
from __future__ import annotations

from jarvis.sessions.formatter import (
    format_session_markdown,
    format_session_plain,
)
from jarvis.sessions.models import VoiceEventRow, VoiceSessionRow, VoiceTurnRow


def _spoken(
    turn_id: str,
    text: str,
    kind: str,
    ts_ms: int = 1_717_780_001_000,
    detail: str | None = None,
) -> VoiceEventRow:
    payload: dict[str, object] = {"text": text, "language": "de", "spoken_kind": kind}
    if detail is not None:
        payload["detail"] = detail
    return VoiceEventRow(
        session_id="sess-1",
        turn_id=turn_id,
        ts_ms=ts_ms,
        kind="SpeechSpoken",
        payload=payload,
    )


def _response(
    turn_id: str,
    text: str,
    ts_ms: int = 1_717_780_002_000,
) -> VoiceEventRow:
    return VoiceEventRow(
        session_id="sess-1",
        turn_id=turn_id,
        ts_ms=ts_ms,
        kind="ResponseGenerated",
        payload={"text": text, "language": "de"},
    )

# Emojis the markdown renderer uses as visual anchors — none may leak into
# the clean ``plain`` transcript.
_EMOJI_ANCHORS = ["\U0001f3a4", "\U0001f9e0", "\U0001f50a", "⏱", "\U0001f527"]


def _session(**over: object) -> VoiceSessionRow:
    base: dict[str, object] = dict(
        id="sess-1",
        started_ms=1_717_780_000_000,
        ended_ms=1_717_780_102_000,  # +102s -> "1 min 42 s"
        hangup_reason="voice_pattern",
        turn_count=2,
        total_cost_usd=0.1984,
        total_tokens_in=98_487,
        total_tokens_out=118,
        providers_used=["gemini"],
        language="de",
        wake_keyword="hey_jarvis",
    )
    base.update(over)
    return VoiceSessionRow(**base)  # type: ignore[arg-type]


def _turn(idx: int, user: str, jarvis: str, **over: object) -> VoiceTurnRow:
    base: dict[str, object] = dict(
        id=f"turn-{idx}",
        session_id="sess-1",
        idx=idx,
        started_ms=1_717_780_000_000 + idx * 1000,
        user_text=user,
        jarvis_text=jarvis,
        tier="deep",
        provider="gemini",
        model="gemini-3.1-pro-preview",
        tokens_in=98_487,
        tokens_out=118,
        cost_usd=0.1984,
        latency_total_ms=89_760,
        think_ms=72_800,
        speak_ms=13_660,
        tool_calls=["gmail-search"],
    )
    base.update(over)
    return VoiceTurnRow(**base)  # type: ignore[arg-type]


def _example_turns() -> list[VoiceTurnRow]:
    return [
        _turn(
            0,
            "Kannst du für mich bitte einmal meine Gmails durchsuchen?",
            "Einen Augenblick. Die Gmail-Suche wurde leider wegen eines "
            "Timeouts abgelehnt. Soll ich es noch einmal versuchen?",
        ),
        _turn(1, "auflegen", ""),
    ]


# --- The clean ``plain`` transcript ----------------------------------------


def test_plain_has_no_emoji_anchors() -> None:
    out = format_session_plain(_session(), _example_turns())
    for emoji in _EMOJI_ANCHORS:
        assert emoji not in out, f"emoji {emoji!r} leaked into clean transcript"


def test_plain_has_no_markdown_markers() -> None:
    out = format_session_plain(_session(), _example_turns())
    assert "# " not in out  # no headings
    assert "**" not in out  # no bold
    assert "> " not in out  # no blockquotes
    assert "`" not in out  # no inline code


def test_plain_has_no_developer_tags() -> None:
    out = format_session_plain(_session(), _example_turns())
    for tag in ("[USER]", "[JARVIS]", "[BRAIN]", "[TOOLS]"):
        assert tag not in out


def test_markdown_labels_awaiting_confirmation_reply() -> None:
    # A turn that ended on a two-turn confirmation tags its reply as a pending
    # yes/no question — consistent with the english spoken_kind tags (preamble,
    # clarify) the markdown renderer already uses (forensic 2026-06-19: the
    # confirmation question was indistinguishable from a normal reply).
    turn = _turn(
        0,
        "schick eine Mail an Tom",
        "Soll ich die E-Mail wirklich senden? Sag ja oder nein.",
        awaiting_confirmation=True,
    )
    out = format_session_markdown(_session(), [turn])
    assert "awaiting confirmation" in out
    assert "Soll ich die E-Mail wirklich senden" in out


def test_markdown_normal_reply_has_no_awaiting_label() -> None:
    turn = _turn(0, "wie spät ist es", "Es ist 15 Uhr.")
    out = format_session_markdown(_session(), [turn])
    assert "awaiting confirmation" not in out


def test_plain_has_no_telemetry() -> None:
    out = format_session_plain(_session(), _example_turns())
    # No tokens / cost / provider / tier / latency clutter, and none of the
    # raw numbers behind them.
    for needle in (
        "tokens",
        "token=",
        "tier",
        "provider",
        "model=",
        "$",
        "tok",
        "98487",
        "gemini",
        "0.1984",
        "latency",
        "ms",
    ):
        assert needle not in out, f"telemetry token {needle!r} leaked"


def test_plain_uses_speaker_labels() -> None:
    out = format_session_plain(_session(), _example_turns())
    assert "Du: Kannst du für mich bitte einmal meine Gmails durchsuchen?" in out
    assert "Jarvis: Einen Augenblick." in out


def test_plain_header_is_a_single_slim_line() -> None:
    out = format_session_plain(_session(), _example_turns())
    first = out.splitlines()[0]
    assert first.startswith("Voice-Session")
    assert "1 min 42 s" in first  # duration
    assert "." in first  # a date is present (dd.mm.yyyy)


def test_plain_preserves_umlauts() -> None:
    turns = [_turn(0, "Mach eine Übung für Köln, groß und süß", "Natürlich.")]
    out = format_session_plain(_session(), turns)
    assert "Übung für Köln, groß und süß" in out
    assert "Natürlich." in out
    # ASCII-mangling must NOT happen.
    assert "Uebung" not in out and "gross" not in out


def test_plain_turn_without_reply_renders_cleanly() -> None:
    out = format_session_plain(_session(), _example_turns())
    assert "Du: auflegen" in out
    # The reply-less turn must not emit a dangling empty "Jarvis:" line.
    assert "Jarvis: \n" not in out
    assert not out.rstrip().endswith("Jarvis:")


def test_plain_empty_session_is_graceful() -> None:
    out = format_session_plain(_session(turn_count=0), [])
    assert out.strip()  # non-empty
    for emoji in _EMOJI_ANCHORS:
        assert emoji not in out


# --- Markdown renderer stays the rich (emoji) variant ----------------------


def test_markdown_renderer_unchanged_still_has_emojis() -> None:
    out = format_session_markdown(_session(), _example_turns())
    # Guard: the rich markdown export keeps its visual anchors; only the
    # clean ``plain`` path was supposed to change.
    assert "\U0001f3a4" in out  # 🎤
    assert "## Turn 1" in out


# --- The spoken track (every voiced non-reply phrase) ----------------------


def test_markdown_includes_the_spoken_track_with_kind_label() -> None:
    # A turn whose only audible output was a canned timeout phrase — no normal
    # reply. It must still surface in the rich export, tagged by kind.
    turns = [_turn(0, "Wie spät ist es?", "")]
    spoken = [_spoken("turn-0", "Das hat zu lange gedauert.", "timeout")]
    out = format_session_markdown(_session(), turns, spoken)
    assert "Das hat zu lange gedauert." in out
    assert "timeout" in out  # the kind is surfaced as a tag


def test_markdown_shows_technical_detail_under_a_readback() -> None:
    # A failed Computer-Use readback speaks a humanized sentence, but the rich
    # markdown export must also surface the technical reason (exit code + raw
    # harness detail) for debugging (user request 2026-06-16).
    turns = [_turn(0, "open discord and check the news", "")]
    spoken = [
        _spoken(
            "turn-0",
            "Das am Bildschirm hat nicht geklappt.",
            "completion",
            detail="exit 5 · 5 guard-blocked actions this mission",
        )
    ]
    out = format_session_markdown(_session(), turns, spoken)
    assert "Das am Bildschirm hat nicht geklappt." in out
    assert "exit 5" in out
    assert "guard-blocked actions" in out


def test_plain_omits_technical_detail_to_stay_clean() -> None:
    # The clean dialogue transcript must NOT leak the cryptic exit code — that
    # is exactly the AI-slop the plain renderer exists to avoid.
    turns = [_turn(0, "open discord", "")]
    spoken = [
        _spoken(
            "turn-0",
            "Das am Bildschirm hat nicht geklappt.",
            "completion",
            detail="exit 5 · 5 guard-blocked actions this mission",
        )
    ]
    out = format_session_plain(_session(), turns, spoken)
    assert "Das am Bildschirm hat nicht geklappt." in out
    assert "exit 5" not in out
    assert "guard-blocked" not in out


def test_plain_includes_spoken_phrases_as_clean_dialogue() -> None:
    turns = [_turn(0, "Wie spät ist es?", "")]
    spoken = [_spoken("turn-0", "Das hat zu lange gedauert.", "timeout")]
    out = format_session_plain(_session(), turns, spoken)
    # The voiced phrase reads as part of the dialogue — no kind tag, no slop.
    assert "Jarvis: Das hat zu lange gedauert." in out
    assert "timeout" not in out  # plain export stays clean of meta-tags


def test_plain_orders_preamble_before_final_response_by_timestamp() -> None:
    turns = [_turn(0, "Was steht heute an?", "Ich konnte das gerade nicht abrufen.")]
    events = [
        _spoken(
            "turn-0",
            "Ich rufe deine heutigen Termine und die aktuellen Nachrichten ab.",
            "preamble",
            ts_ms=1_717_780_001_000,
        ),
        _response(
            "turn-0",
            "Ich konnte das gerade nicht abrufen.",
            ts_ms=1_717_780_020_000,
        ),
    ]
    out = format_session_plain(_session(), turns, events)
    preamble_pos = out.index("Jarvis: Ich rufe deine heutigen Termine")
    final_pos = out.index("Jarvis: Ich konnte das gerade nicht abrufen.")
    assert preamble_pos < final_pos


def test_plain_orders_late_readback_after_final_response() -> None:
    turns = [_turn(0, "Starte die Recherche", "Ich kuemmere mich darum.")]
    events = [
        _response("turn-0", "Ich kuemmere mich darum.", ts_ms=1_717_780_002_000),
        _spoken(
            "turn-0",
            "Die Recherche ist fertig.",
            "completion",
            ts_ms=1_717_780_030_000,
        ),
    ]
    out = format_session_plain(_session(), turns, events)
    final_pos = out.index("Jarvis: Ich kuemmere mich darum.")
    readback_pos = out.index("Jarvis: Die Recherche ist fertig.")
    assert final_pos < readback_pos


def test_markdown_orders_spoken_and_final_response_by_timestamp() -> None:
    turns = [_turn(0, "Was steht heute an?", "Ich konnte das gerade nicht abrufen.")]
    events = [
        _spoken("turn-0", "Ich rufe deine Termine ab.", "preamble", ts_ms=1000),
        _response("turn-0", "Ich konnte das gerade nicht abrufen.", ts_ms=2000),
    ]
    out = format_session_markdown(_session(), turns, events)
    assert out.index("Ich rufe deine Termine ab.") < out.index(
        "Ich konnte das gerade nicht abrufen."
    )


def test_spoken_track_is_grouped_under_its_own_turn() -> None:
    turns = [_turn(0, "Erste Frage", "Erste Antwort"), _turn(1, "Zweite Frage", "")]
    spoken = [_spoken("turn-1", "Bin noch dran.", "progress")]
    out = format_session_markdown(_session(), turns, spoken)
    # The progress nudge belongs to turn 2, not turn 1.
    turn2_section = out.split("## Turn 2")[1]
    assert "Bin noch dran." in turn2_section
    assert "Bin noch dran." not in out.split("## Turn 2")[0]
