---
title: "ADR-0007: Flight-Recorder JSONL"
slug: adr-0007-flight-recorder-jsonl
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0007 — Flight-Recorder Format: JSONL, daily-rotated

**Status:** Accepted  (2026-04-22)
**Phase:** 5 — Control

## Context

Mandate requirement: "Every Computer-Use step (screenshot hash, proposed action, outcome) lands in the flight recorder and is replayable." Master plan §10 mentions "JSONL + SQLite tracing" — so far there is only the hook (`subscribe_all`), no recorder implementation.

## Decision

**JSONL, daily-rotated**, `data/flight_recorder/YYYY-MM-DD.jsonl`.

### Recorder
`jarvis/telemetry/recorder.py` — wildcard subscriber on `EventBus`. One event → one line. Append-only, `fsync` every 1s (not per line, to avoid I/O backpressure). File rotation at 00:00 local time. Maximum file size: 500 MB (then suffix `-2.jsonl`, …).

### Line format
```json
{"ts_ns": 1745234567890000000,
 "trace_id": "018f...",
 "event": "HarnessProgress",
 "payload": {"harness": "computer-use", "stdout": "...", "exit_code": 0, "cost_usd": 0.003},
 "layer": "L5"}
```

- `ts_ns` and `trace_id` come from the event (all frozen events have them).
- `event` = event class name.
- `payload` = `dataclasses.asdict(event)` minus `ts_ns`/`trace_id` (those are top-level).
- `layer` = static mapping from `EVENT_LAYER_MAP` (new in `events.py`).
- UUIDs are serialized as hex strings, bytes as `{"__bytes__": "<base64>"}`.
- Binary-size limit: if `payload` size > 64 KB, the affected sub-key is replaced by a path: `"screenshot_png": {"__file__": "data/flight_recorder/blobs/018f-step3.png"}` — binary data lands in the `blobs/` folder, not inline in the JSONL (readability + parsability).

### Replay CLI
`python -m jarvis.telemetry.replay <trace_id>` reads all JSONLs in the `data/flight_recorder/` folder (today's + earlier ones, as needed), filters by `trace_id`, and renders a chronological timeline:

```
[0.000s] HarnessDispatched  computer-use  prompt="Öffne Notepad..."
[0.012s] ObservationCaptured  window="Desktop"  nodes=47  screenshot=018f-step1.png
[0.340s] ActionProposed      action="click"  target="{role:Button,name:Start}"
[0.510s] ActionExecuted      success=True  duration_ms=120
[0.520s] ObservationCaptured  window="Notepad"  nodes=12  screenshot=018f-step2.png
...
```

Optionally `--json` for machine-readable output.

## Consequences

+ Streaming-friendly: every line write is atomic, a crash in the middle of an event costs at most one incomplete line (easy to tolerate).
+ Greppable, jq-able, ad-hoc analyzable.
+ Rotation prevents unbounded disk consumption.
+ Blob separation keeps the JSONL scannable (~50 MB stays under jq/vscode limits).
- No automatic cleanup policy for old JSONLs in this ADR — open, config option `retention_days = 14` in Phase 5.3.
- No indexes. Queries spanning many days are linear. For our use cases (a single trace_id, yesterday/today) this is not a problem.

## Alternatives Considered

- **SQLite-only:** Expensive for an event stream (many small writes with schema overhead, FTS5 trigger cost). JSONL is 5× faster per event. Rejected.
- **Parquet / Feather:** Columnar, requires batching → not compatible with streaming. Rejected.
- **OTel + Jaeger:** A local tracing backend is overkill, the mandate explicitly forbids externally hosted services. Rejected.
- **In-memory ring buffer + dump-on-crash:** The crash case is precisely the interesting one. Rejected.

## Open

- Retention: default `retention_days = 14`, configurable in `jarvis.toml:[telemetry.flight_recorder]`.
- PII redaction: screenshots may contain sensitive data. Opt-out flag per task: `record_screenshots = false`. Default stays `true` for debuggability.
