"""Missing or untrusted termination evidence must never become invented facts."""
from __future__ import annotations

import json

import pytest

from jarvis.sessions.diagnostics import session_termination_diagnostics
from jarvis.sessions.formatter import format_session_markdown, format_session_plain
from jarvis.sessions.models import VoiceEventRow, VoiceSessionRow


def _session() -> VoiceSessionRow:
    return VoiceSessionRow(id="session", started_ms=1000, ended_ms=5000, hangup_reason="shutdown")


def _event(kind: str, ts_ms: int, **payload: object) -> VoiceEventRow:
    return VoiceEventRow(session_id="session", ts_ms=ts_ms, kind=kind, payload=payload)


def test_historical_export_keeps_known_reason_and_missing_evidence() -> None:
    session = _session()
    result = session_termination_diagnostics(session, [])
    assert result["hangup_reason"] == "shutdown"
    assert result["producer"] is None
    assert result["idle_timeout_s"] is None
    assert result["end_call_signal"] is None
    for renderer in (format_session_plain, format_session_markdown):
        output = renderer(session, [])
        assert "shutdown" in output
        assert "not recorded" in output
        assert "end_call_signal: false" not in output


def test_snapshot_keeps_false_and_zero_but_never_exports_unknown_raw_content() -> None:
    events = [_event("VoiceSessionEnded", 5000, detail=json.dumps({
        "producer": "speech.pipeline.idle_timeout",
        "hangup_pattern_matched": False, "idle_timeout_s": 0,
        "single_turn_mode": False, "end_call_signal": True,
        "raw_brain_response": "PRIVATE RESPONSE", "api_key": "PRIVATE CREDENTIAL",
    }))]
    output = format_session_plain(_session(), [], iter(events))
    assert "hangup_pattern_matched: false" in output
    assert "idle_timeout_s: 0" in output
    assert "single_turn_mode: false" in output
    assert "end_call_signal: true" in output
    assert "PRIVATE" not in output
    assert "Session termination diagnostics (not spoken):" in output


@pytest.mark.parametrize("detail", ["", "bad json", "[]", '{"x":1}', "[" * 2000, "x" * 20_000])
def test_malformed_or_old_detail_does_not_break_export(detail: str) -> None:
    event = _event("VoiceSessionEnded", 5000, source_layer="pipeline.stop", detail=detail)
    output = format_session_plain(_session(), [], [event])
    assert "shutdown" in output
    assert "pipeline.stop" in output
    assert "hangup_pattern_matched: false" not in output


def test_event_times_exclude_other_sessions_and_after_hangup_readbacks() -> None:
    events = [
        _event("UtteranceCaptured", 1200),
        _event("ToolCallStarted", 1400),
        _event("ToolCallCompleted", 4000),
        _event("LatencySpan", 4010, phase="tts_request_sent", duration_ms=0),
        _event("AudioOutFirst", 4100),
        _event("LatencySpan", 4400, phase="tts_stream_done", duration_ms=390),
        _event("SpeechSpoken", 4500),
        _event("SystemStateChanged", 4600, previous="SPEAKING", new_state="LISTENING"),
        _event("VoiceSessionEnded", 5000),
        _event("SpeechSpoken", 5500),
        _event("LatencySpan", 5600, phase="tts_stream_done", duration_ms=1500),
        VoiceEventRow(session_id="other", ts_ms=4900, kind="UtteranceCaptured"),
    ]
    result = session_termination_diagnostics(_session(), reversed(events))
    assert result["last_user_utterance_end_ms"] == 1200
    assert result["last_tool_start_ms"] == 1400
    assert result["last_tool_end_ms"] == 4000
    assert result["last_assistant_audio_start_ms"] == 4100
    assert result["last_assistant_audio_confirmed_ms"] == 4500
    assert result["last_tts_request_ms"] == 4010
    assert result["last_tts_stream_done_ms"] == 4400
    assert result["last_state_transition"] == "SPEAKING -> LISTENING"
    assert result["last_state_transition_ms"] == 4600
    assert result["session_end_ms"] == 5000
    assert result["tool_active"] is None


def test_oversized_json_number_is_unknown_instead_of_breaking_export() -> None:
    event = _event("VoiceSessionEnded", 5000, detail=json.dumps({"idle_timeout_s": 10**400}))
    result = session_termination_diagnostics(_session(), [event])
    assert result["idle_timeout_s"] is None
    assert "idle_timeout_s: not recorded" in format_session_plain(_session(), [], [event])


def test_late_termination_event_cannot_replace_the_recorded_end_snapshot() -> None:
    events = [
        _event("VoiceSessionEnded", 5000, detail=json.dumps({"producer": "pipeline.stop"})),
        _event("VoiceSessionEnded", 5001, detail=json.dumps({"producer": "late.unrelated"})),
    ]
    result = session_termination_diagnostics(_session(), events)
    assert result["producer"] == "pipeline.stop"
    output = format_session_plain(_session(), [], events)
    assert "pipeline.stop" in output
    assert "late.unrelated" not in output
