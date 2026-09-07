"""Read termination evidence without guessing missing historical measurements."""
from __future__ import annotations

import json
import math
from collections.abc import Iterable

from .models import VoiceEventRow, VoiceSessionRow

_SNAPSHOT_FIELDS = (
    "hangup_pattern_matched", "hangup_pattern", "end_call_signal",
    "legacy_farewell_matched", "single_turn_mode", "continue_listening_after_response",
    "idle_timeout_s", "idle_deadline_monotonic", "last_activity_monotonic",
    "last_answer_floor_monotonic", "turn_state", "previous_turn_state",
    "assistant_work_active", "tool_active", "screen_active", "computer_use_active",
    "input_error_type", "input_replay_overrun",
)
_EVENT_TIMESTAMPS = {
    "UtteranceCaptured": "last_user_utterance_end_ms",
    "TranscriptFinal": "last_user_transcript_final_ms",
    "ToolCallStarted": "last_tool_start_ms",
    "ToolCallCompleted": "last_tool_end_ms",
    "BrainTurnStarted": "last_assistant_generation_start_ms",
    "BrainTurnCompleted": "last_assistant_generation_end_ms",
    "AudioOutFirst": "last_assistant_audio_start_ms",
    "SpeechSpoken": "last_assistant_audio_confirmed_ms",
}
_LATENCY_TIMESTAMPS = {
    "tts_request_sent": "last_tts_request_ms",
    "tts_stream_done": "last_tts_stream_done_ms",
}


def _scalar(value: object) -> str | int | float | bool | None:
    if isinstance(value, str):
        return " ".join(value.split())[:240] or None
    if isinstance(value, (int, float)):
        try:
            if math.isfinite(value):
                return value
        except OverflowError:
            # JSON allows integers too large for the float finiteness check.
            # Such a value is not a plausible timestamp or setting.
            return None
    return None


def session_termination_diagnostics(
    session: VoiceSessionRow, events: Iterable[VoiceEventRow],
) -> dict[str, object]:
    """Merge a terminal snapshot with recorded event times, never raw content.

    Monotonic seconds are labelled separately from wall-clock epoch milliseconds.
    Event timestamps are publication times; audio confirmation is not a guessed
    TTS synthesis or hardware-playback boundary. Missing evidence stays null.
    """
    relevant = sorted(
        (
            event for event in events
            if event.session_id == session.id
            and (session.ended_ms is None or event.ts_ms <= session.ended_ms)
        ),
        key=lambda event: (event.ts_ms, event.seq or 0),
    )
    ended = next((event for event in reversed(relevant) if event.kind == "VoiceSessionEnded"), None)
    snapshot: dict[str, object] = {}
    if ended is not None:
        detail = ended.payload.get("detail")
        if isinstance(detail, str) and len(detail) <= 16_384:
            try:
                decoded = json.loads(detail)
            except (ValueError, RecursionError):
                # Legacy/malformed diagnostics must not break transcript export.
                decoded = None
            if isinstance(decoded, dict):
                snapshot = {
                    field: value for field, value in decoded.items()
                    if field in _SNAPSHOT_FIELDS or field == "producer"
                }
    producer = _scalar(snapshot.get("producer"))
    if not producer and ended is not None:
        producer = _scalar(ended.payload.get("source_layer"))
    result: dict[str, object] = {
        "hangup_reason": session.hangup_reason or None,
        "producer": producer,
        "snapshot_recorded": bool(snapshot),
    }
    for field in _SNAPSHOT_FIELDS:
        result[field] = _scalar(snapshot.get(field))
    for field in (*_EVENT_TIMESTAMPS.values(), *_LATENCY_TIMESTAMPS.values()):
        result[field] = None
    result["last_state_transition"] = None
    result["last_state_transition_ms"] = None
    result["session_end_ms"] = session.ended_ms
    for event in relevant:
        field = _EVENT_TIMESTAMPS.get(event.kind)
        if field:
            result[field] = event.ts_ms
        if event.kind == "LatencySpan":
            phase = event.payload.get("phase")
            if isinstance(phase, str) and phase in _LATENCY_TIMESTAMPS:
                result[_LATENCY_TIMESTAMPS[phase]] = event.ts_ms
        if event.kind == "SystemStateChanged":
            previous = _scalar(event.payload.get("previous"))
            current = _scalar(event.payload.get("new_state"))
            if previous and current:
                result["last_state_transition"] = f"{previous} -> {current}"
                result["last_state_transition_ms"] = event.ts_ms
    return result


def diagnostic_value(value: object) -> str:
    if value is None:
        return "not recorded"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
