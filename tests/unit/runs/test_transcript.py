"""The Run Inspector's full-transcription lens.

`build_transcript` turns a turn's raw voice_events into a gap-less, UNTRUNCATED,
chronological transcript that weaves the user's utterance, every Jarvis phrase
(reply + intermediate/announcement/clarify sentences), status events, tool/CU
outcomes, and system outputs (exit codes, denials, errors) into one ordered
stream. This is the requirement the event-kind Timeline panel does not meet:
the Timeline truncates text to 80 chars and is not role-tagged."""
from jarvis.runs.analyzer import build_transcript
from jarvis.runs.constants import (
    ROLE_ERROR,
    ROLE_JARVIS,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    TRANSCRIPT_ROLES,
)
from jarvis.runs.model import RunTurn, TranscriptLine
from jarvis.sessions.models import VoiceEventRow


def _ev(kind, ts_ms=0, **payload):
    return VoiceEventRow(session_id="s", turn_id="t1", ts_ms=ts_ms, kind=kind, payload=payload)


def test_transcript_roles_complete_and_stable():
    assert TRANSCRIPT_ROLES == (ROLE_USER, ROLE_JARVIS, ROLE_SYSTEM, ROLE_TOOL, ROLE_ERROR)
    assert set(TRANSCRIPT_ROLES) == {"user", "jarvis", "system", "tool", "error"}


def test_transcript_line_defaults():
    line = TranscriptLine(role="jarvis", kind="ResponseGenerated", text="hi")
    assert line.offset_ms == 0 and line.spoken_kind is None


def test_run_turn_has_empty_transcript_by_default():
    assert RunTurn(idx=0, trace_id="t1").transcript == []


def test_transcript_weaves_roles_in_chronological_order():
    events = [
        _ev("ResponseGenerated", ts_ms=30, text="Alles bestens, Boss."),
        _ev("TranscriptFinal", ts_ms=10, text="Was geht ab?"),
        _ev("SystemStateChanged", ts_ms=20, previous="LISTENING", new_state="PROCESSING"),
        _ev("SpeechSpoken", ts_ms=40, text="Einen Moment noch.", spoken_kind="progress"),
        _ev("ActionExecuted", ts_ms=50, tool_name="cli_gcloud", success=True),
        _ev("ActionDenied", ts_ms=60, tool_name="rm", reason="blacklist: destructive"),
    ]
    lines = build_transcript(events, turn_started_ms=0)
    assert [l.role for l in lines] == [
        ROLE_USER, ROLE_SYSTEM, ROLE_JARVIS, ROLE_JARVIS, ROLE_TOOL, ROLE_ERROR
    ]
    assert lines[0].text == "Was geht ab?" and lines[0].offset_ms == 10
    assert lines[2].text == "Alles bestens, Boss."
    assert lines[3].spoken_kind == "progress"
    assert "cli_gcloud" in lines[4].text
    assert "blacklist" in lines[5].text


def test_transcript_text_is_not_truncated():
    long = "x" * 500
    lines = build_transcript([_ev("ResponseGenerated", text=long)])
    assert lines[0].text == long  # full text, unlike the 80-char Timeline summary


def test_transcript_surfaces_system_output_exit_code():
    # The non-spoken CU-failure diagnostic ("exit 5 · ...") rides on SpeechSpoken.detail.
    events = [
        _ev("SpeechSpoken", ts_ms=10, text="Das hat leider nicht geklappt.",
            spoken_kind="cu_failure", detail="exit 5 · harness reported failure"),
    ]
    lines = build_transcript(events)
    texts = [l.text for l in lines]
    assert any("nicht geklappt" in t for t in texts)        # the spoken phrase
    diag = next(l for l in lines if "exit 5" in l.text)     # the system diagnostic
    assert diag.role == ROLE_SYSTEM


def test_transcript_skips_endpoint_marker_detail():
    # SpeechSpoken.detail "endpoint=silence" is telemetry, not a system output line.
    events = [_ev("SpeechSpoken", ts_ms=10, text="Ja?", spoken_kind="clarify",
                  detail="endpoint=silence")]
    lines = build_transcript(events)
    assert len(lines) == 1 and lines[0].role == ROLE_JARVIS


def test_transcript_error_event_is_error_role():
    events = [_ev("ErrorOccurred", ts_ms=5, layer="brain", error_type="Timeout",
                  message="provider chain unreachable", recoverable=False)]
    lines = build_transcript(events)
    assert lines[0].role == ROLE_ERROR
    assert "Timeout" in lines[0].text and "unreachable" in lines[0].text
