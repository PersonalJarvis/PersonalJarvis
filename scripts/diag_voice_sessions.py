"""Audit recorded voice sessions against the known failure classes.

Reads the flight recorder (``data/flight_recorder/YYYY-MM-DD.jsonl``), splits
it into voice sessions, and checks every session for the failure patterns
confirmed in the 2026-07-13 realtime forensics (BUG-047..BUG-050):

- ``promise-without-action`` — a turn ends on a deferred-action claim while
  its ``tool_calls`` are empty (the model promised and nothing ran).
- ``voice-identity-break`` — a non-realtime layer spoke (classic pipeline
  TTS) while a live realtime session owned the voice.
- ``tool-retry-loop`` — the same tool failed three or more times inside one
  session (the model grinding against a broken/lying tool).
- ``exhausted-brain-turn`` — a brain turn died on ``budget_exceeded`` or
  returned no text at all (outside the legitimate suppress paths).
- ``silent-turn`` — a user turn completed without any assistant text.

Usage::

    python scripts/diag_voice_sessions.py            # today's sessions
    python scripts/diag_voice_sessions.py --date 2026-07-13
    python scripts/diag_voice_sessions.py --last 3   # newest N sessions only

Read-only developer diagnostic: no app, network, or config access.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jarvis.brain.action_honesty import has_deferred_action_claim  # noqa: E402

_MAX_PREVIEW_CHARS = 90
_RETRY_LOOP_THRESHOLD = 3
# Brain turns that legitimately produce no user-facing text.
_SUPPRESSED_FINISH_REASONS = frozenset({"suppress_response", "voice_confirm_pending"})


@dataclass(slots=True)
class Finding:
    severity: str  # "high" | "warn" | "info"
    kind: str
    detail: str


@dataclass(slots=True)
class Session:
    session_id: str
    started_ts_ns: int
    events: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def turn_count(self) -> int:
        return sum(1 for e in self.events if e.get("event") == "VoiceTurnCompleted")


def _preview(text: Any) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= _MAX_PREVIEW_CHARS:
        return cleaned
    return cleaned[: _MAX_PREVIEW_CHARS - 1] + "…"


def _split_sessions(events: list[dict[str, Any]]) -> list[Session]:
    """Assign events to sessions by id where present, else by time window."""
    sessions: list[Session] = []
    active: Session | None = None
    for event in events:
        payload = event.get("payload") or {}
        name = event.get("event", "")
        if name == "VoiceSessionStarted":
            active = Session(
                session_id=str(payload.get("session_id", "?")),
                started_ts_ns=int(event.get("ts_ns", 0)),
            )
            sessions.append(active)
        if active is not None:
            event_session = payload.get("session_id")
            if event_session is None or event_session == active.session_id:
                active.events.append(event)
        if name == "VoiceSessionEnded" and active is not None:
            if payload.get("session_id") in (None, active.session_id):
                active = None
    return sessions


def _check_promise_without_action(session: Session) -> None:
    for event in session.events:
        if event.get("event") != "VoiceTurnCompleted":
            continue
        payload = event.get("payload") or {}
        jarvis_text = str(payload.get("jarvis_text", "") or "")
        tool_calls = payload.get("tool_calls") or ()
        if jarvis_text and not tool_calls and has_deferred_action_claim(jarvis_text):
            session.findings.append(
                Finding(
                    severity="high",
                    kind="promise-without-action",
                    detail=(
                        "turn ended on a future-work claim with no tool call: "
                        f"\"{_preview(jarvis_text)}\""
                    ),
                )
            )


def _check_voice_identity(session: Session) -> None:
    realtime_live = False
    for event in session.events:
        name = event.get("event", "")
        if name == "RealtimeSessionReady":
            realtime_live = True
        elif name == "VoiceSessionEnded":
            realtime_live = False
        elif name == "SpeechSpoken" and realtime_live:
            layer = str(event.get("layer", "") or "")
            if layer and not layer.startswith("realtime."):
                payload = event.get("payload") or {}
                session.findings.append(
                    Finding(
                        severity="high",
                        kind="voice-identity-break",
                        detail=(
                            f"layer {layer!r} spoke into the live realtime call: "
                            f"\"{_preview(payload.get('text'))}\""
                        ),
                    )
                )


def _check_tool_retry_loops(session: Session) -> None:
    failures: Counter[str] = Counter()
    last_error: dict[str, str] = {}
    for event in session.events:
        if event.get("event") != "ActionExecuted":
            continue
        payload = event.get("payload") or {}
        if payload.get("success"):
            continue
        tool = str(payload.get("tool_name", "?"))
        failures[tool] += 1
        last_error[tool] = _preview(payload.get("error"))
    for tool, count in failures.items():
        if count >= _RETRY_LOOP_THRESHOLD:
            session.findings.append(
                Finding(
                    severity="warn",
                    kind="tool-retry-loop",
                    detail=(
                        f"{tool} failed {count}x (last error: "
                        f"{last_error.get(tool, '?')})"
                    ),
                )
            )


def _check_exhausted_brain_turns(session: Session) -> None:
    for event in session.events:
        if event.get("event") != "BrainTurnCompleted":
            continue
        payload = event.get("payload") or {}
        finish = str(payload.get("finish_reason", "") or "")
        text_len = int(payload.get("text_len", 0) or 0)
        if finish == "budget_exceeded":
            session.findings.append(
                Finding(
                    severity="high",
                    kind="exhausted-brain-turn",
                    detail=(
                        "iteration budget exhausted before an answer "
                        f"(tokens_in={payload.get('tokens_in')})"
                    ),
                )
            )
        elif text_len == 0 and finish not in _SUPPRESSED_FINISH_REASONS:
            session.findings.append(
                Finding(
                    severity="warn",
                    kind="empty-brain-turn",
                    detail=f"brain turn produced no text (finish_reason={finish!r})",
                )
            )


def _check_silent_turns(session: Session) -> None:
    for event in session.events:
        if event.get("event") != "VoiceTurnCompleted":
            continue
        payload = event.get("payload") or {}
        user_text = str(payload.get("user_text", "") or "").strip()
        jarvis_text = str(payload.get("jarvis_text", "") or "").strip()
        # A one/two-word interjection ("Hm?") legitimately gets no reply.
        if jarvis_text or len(user_text.split()) < 3:
            continue
        session.findings.append(
            Finding(
                severity="info",
                kind="silent-turn",
                detail=f"no assistant text for: \"{_preview(user_text)}\"",
            )
        )


_CHECKS = (
    _check_promise_without_action,
    _check_voice_identity,
    _check_tool_retry_loops,
    _check_exhausted_brain_turns,
    _check_silent_turns,
)


def audit_events(events: list[dict[str, Any]]) -> list[Session]:
    """Split raw flight-recorder events and run every check per session."""
    sessions = _split_sessions(events)
    for session in sessions:
        for check in _CHECKS:
            check(session)
    return sessions


def _load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _format_report(sessions: list[Session]) -> str:
    lines: list[str] = []
    total_findings = 0
    for session in sessions:
        header = (
            f"session {session.session_id[:8]}  turns={session.turn_count}  "
            f"findings={len(session.findings)}"
        )
        lines.append(header)
        for finding in session.findings:
            total_findings += 1
            lines.append(f"  [{finding.severity:4}] {finding.kind}: {finding.detail}")
    lines.append(
        f"\n{len(sessions)} session(s), {total_findings} finding(s)."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="flight-recorder day to audit (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=0,
        help="audit only the newest N sessions (default: all)",
    )
    args = parser.parse_args(argv)

    recorder_path = REPO_ROOT / "data" / "flight_recorder" / f"{args.date}.jsonl"
    if not recorder_path.exists():
        print(f"no flight recorder file for {args.date}: {recorder_path}")
        return 2

    sessions = audit_events(_load_events(recorder_path))
    if args.last > 0:
        sessions = sessions[-args.last :]
    print(_format_report(sessions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
