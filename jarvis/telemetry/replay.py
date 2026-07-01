"""Replay CLI — renders a Computer-Use/task trace as a timeline.

Usage:
    python -m jarvis.telemetry.replay <trace_id>
    python -m jarvis.telemetry.replay <trace_id> --json
    python -m jarvis.telemetry.replay <trace_id> --data-dir data/flight_recorder

Shows one line per event with the relative time, event type, and the
interesting payload fields. With `--json`, the raw JSONL line is passed
through unchanged (handy for jq pipelines).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from .recorder import FlightRecorder

# Which payload fields are especially interesting for which event.
# Only for the human-readable output — everything else ends up as "..."
_FEATURED_FIELDS: dict[str, tuple[str, ...]] = {
    "HarnessDispatched": ("harness",),
    "HarnessProgress": ("harness", "exit_code"),
    "HarnessCompleted": ("harness", "exit_code", "duration_ms"),
    "ObservationCaptured": ("window_title", "node_count", "screenshot_hash"),
    "ActionProposed": ("tool_name", "risk_tier"),
    "ActionPlanned": ("action_kind", "target_hint"),
    "ActionExecuted": ("tool_name", "success", "duration_ms"),
    "ActionVerified": ("action_kind", "success", "reason"),
    "TaskStarted": ("task_id",),
    "TaskStepRecorded": ("task_id", "seq", "kind"),
    "TaskCompleted": ("task_id", "duration_ms"),
    "TaskFailed": ("task_id", "error"),
    "TaskCancelled": ("task_id", "reason"),
    "KillRequested": ("source", "reason"),
    "KillAcknowledged": ("holder", "took_ms"),
    "BudgetWarning": ("scope", "spent_eur", "limit_eur"),
    "BudgetExceeded": ("scope", "spent_eur", "limit_eur"),
    "AdminOperationRequested": ("op_type", "destructive"),
    "AdminOperationCompleted": ("op_type", "success", "duration_ms"),
    "AdminOperationRejected": ("op_type", "reason"),
}


def _fmt_payload(event: str, payload: dict[str, Any]) -> str:
    fields = _FEATURED_FIELDS.get(event)
    if not fields:
        # Fallback: briefly show the first 3 non-null fields
        items = [(k, v) for k, v in payload.items() if v not in (None, "", 0, False)]
        items = items[:3]
    else:
        items = [(k, payload.get(k)) for k in fields]
    parts = []
    for k, v in items:
        if v is None:
            continue
        if isinstance(v, str) and len(v) > 60:
            v = v[:57] + "..."
        parts.append(f"{k}={v}")
    return " ".join(parts)


def render_timeline(records: list[dict[str, Any]], *, out=None) -> None:
    # Default late-bound, so pytest's capsys redirect takes effect.
    out = out if out is not None else sys.stdout
    if not records:
        print("No events found for this trace_id.", file=out)
        return
    t0 = records[0].get("ts_ns", 0)
    for rec in records:
        rel_s = (rec.get("ts_ns", t0) - t0) / 1e9
        event = rec.get("event", "?")
        payload = rec.get("payload", {}) or {}
        line = f"[{rel_s:7.3f}s] {event:24s} {_fmt_payload(event, payload)}"
        print(line, file=out)


def render_json(records: list[dict[str, Any]], *, out=None) -> None:
    out = out if out is not None else sys.stdout
    for rec in records:
        print(json.dumps(rec, ensure_ascii=False), file=out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis.telemetry.replay",
        description="Renders the flight-recorder trace of a trace_id as a timeline.",
    )
    parser.add_argument("trace_id", help="UUID (hex or string format)")
    parser.add_argument("--data-dir",
                        default="data/flight_recorder",
                        help="Directory containing the JSONL files")
    parser.add_argument("--json", action="store_true",
                        help="Print the raw JSONL entries instead of the timeline")
    args = parser.parse_args(argv)

    try:
        trace_id = UUID(args.trace_id)
    except ValueError:
        print(f"Invalid trace_id: {args.trace_id}", file=sys.stderr)
        return 2

    recorder = FlightRecorder(data_dir=Path(args.data_dir))
    records = recorder.iter_events_for_trace(trace_id)

    if args.json:
        render_json(records)
    else:
        render_timeline(records)
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
