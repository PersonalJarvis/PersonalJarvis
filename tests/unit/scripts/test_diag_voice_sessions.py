"""The voice-session auditor must flag every confirmed failure class."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "diag_voice_sessions", REPO_ROOT / "scripts" / "diag_voice_sessions.py"
)
diag = importlib.util.module_from_spec(_SPEC)
sys.modules["diag_voice_sessions"] = diag
_SPEC.loader.exec_module(diag)


def _session_events(session_id: str, inner: list[dict]) -> list[dict]:
    return [
        {
            "ts_ns": 1,
            "event": "VoiceSessionStarted",
            "layer": "speech.pipeline",
            "payload": {"session_id": session_id},
        },
        {
            "ts_ns": 2,
            "event": "RealtimeSessionReady",
            "layer": "realtime.fake",
            "payload": {"session_id": session_id},
        },
        *inner,
        {
            "ts_ns": 99,
            "event": "VoiceSessionEnded",
            "layer": "speech.pipeline",
            "payload": {"session_id": session_id},
        },
    ]


def _kinds(sessions) -> set[str]:
    return {f.kind for s in sessions for f in s.findings}


def test_promise_without_action_is_flagged() -> None:
    events = _session_events(
        "s1",
        [
            {
                "ts_ns": 10,
                "event": "VoiceTurnCompleted",
                "layer": "realtime.fake",
                "payload": {
                    "session_id": "s1",
                    "user_text": "List my wiki files.",
                    "jarvis_text": (
                        "Einen Moment, ich werfe einen Blick in dein Wiki "  # i18n-allow: forensic fixture
                        "und sage dir gleich, was drinsteht."  # i18n-allow: forensic fixture
                    ),
                    "tool_calls": [],
                },
            }
        ],
    )
    assert "promise-without-action" in _kinds(diag.audit_events(events))


def test_backed_promise_is_not_flagged() -> None:
    events = _session_events(
        "s1",
        [
            {
                "ts_ns": 10,
                "event": "VoiceTurnCompleted",
                "layer": "realtime.fake",
                "payload": {
                    "session_id": "s1",
                    "user_text": "List my wiki files.",
                    "jarvis_text": "Ich schaue gleich in dein Wiki.",  # i18n-allow: fixture
                    "tool_calls": ["jarvis_action"],
                },
            }
        ],
    )
    assert "promise-without-action" not in _kinds(diag.audit_events(events))


def test_classic_voice_inside_live_realtime_call_is_flagged() -> None:
    events = _session_events(
        "s1",
        [
            {
                "ts_ns": 10,
                "event": "SpeechSpoken",
                "layer": "speech.pipeline",
                "payload": {"text": "I am searching your wiki right now."},
            }
        ],
    )
    assert "voice-identity-break" in _kinds(diag.audit_events(events))


def test_realtime_voice_is_not_flagged() -> None:
    events = _session_events(
        "s1",
        [
            {
                "ts_ns": 10,
                "event": "SpeechSpoken",
                "layer": "realtime.openai-realtime",
                "payload": {"text": "Here is your wiki."},
            }
        ],
    )
    assert "voice-identity-break" not in _kinds(diag.audit_events(events))


def test_tool_retry_loop_and_budget_exhaustion_are_flagged() -> None:
    failure = {
        "event": "ActionExecuted",
        "layer": "",
        "payload": {
            "tool_name": "run_shell",
            "success": False,
            "error": "Not found: [WinError 2]",
        },
    }
    events = _session_events(
        "s1",
        [
            {**failure, "ts_ns": 10},
            {**failure, "ts_ns": 11},
            {**failure, "ts_ns": 12},
            {
                "ts_ns": 13,
                "event": "BrainTurnCompleted",
                "layer": "",
                "payload": {
                    "finish_reason": "budget_exceeded",
                    "text_len": 0,
                    "tokens_in": 404606,
                },
            },
        ],
    )
    kinds = _kinds(diag.audit_events(events))
    assert "tool-retry-loop" in kinds
    assert "exhausted-brain-turn" in kinds


def test_clean_session_yields_no_findings() -> None:
    events = _session_events(
        "s1",
        [
            {
                "ts_ns": 10,
                "event": "VoiceTurnCompleted",
                "layer": "realtime.fake",
                "payload": {
                    "session_id": "s1",
                    "user_text": "What time is it?",
                    "jarvis_text": "It is nine in the evening.",
                    "tool_calls": [],
                },
            },
            {
                "ts_ns": 11,
                "event": "BrainTurnCompleted",
                "layer": "",
                "payload": {"finish_reason": "voice_confirm_pending", "text_len": 0},
            },
        ],
    )
    sessions = diag.audit_events(events)
    assert len(sessions) == 1
    assert sessions[0].findings == []
