"""The reasoning trace of one turn, as a record that survives the turn.

While a turn runs, the UI watches the bus and draws the steps live — the
brain call, every tool the assistant reached for, the worker it delegated
to, what it looked at on screen, the sentence it wrote next to each call
(``ActionProposed.rationale``). Until now that picture lived only in the
window that happened to be open: reopen the chat and the answer stood there
with no sign of how it came about.

This module keeps the same events the UI reads, in a bounded ring, so the
reply path can attach the slice that belongs to its turn to the stored
message (``ChatStore.add_message(trace=…)``). The stored shape is the EVENT
LIST — ``{name, ts_ms, payload}`` — not pre-rendered steps: the frontend
already owns the one mapping from events to human-readable rows
(``lib/thinkingSteps.ts``), and a stored trace is replayed through exactly
that mapping, so live and archived traces can never drift apart.

Payloads are reduced to the handful of keys the mapping reads and every
free-text value passes :func:`jarvis.core.redact.safe_preview` — a tool's
arguments can carry anything the user typed, and the trace lands in a file.

Read-only wildcard subscriber, never on the hot path (AP-9); every hook is
wrapped so a malformed event can never reach the publisher (AP-18).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jarvis.core.redact import safe_preview

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.core.events import Event

log = logging.getLogger(__name__)

#: The event kinds a trace is made of — the same set ``reduceThinkingSteps``
#: in the frontend reads. Anything else never enters the ring.
TRACE_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "BrainTurnStarted",
        "BrainTurnCompleted",
        "ToolCallStarted",
        "ToolCallCompleted",
        "ActionProposed",
        "ActionExecuted",
        "ActionDenied",
        "ObservationCaptured",
        "ActionPlanned",
        "CUStepProfiled",
        "JarvisAgentTaskStarted",
        "JarvisAgentTaskCompleted",
        "AnnouncementRequested",
    }
)

#: Per event kind, the payload keys worth keeping. Free text is previewed.
_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "BrainTurnStarted": ("provider", "model", "intent_level"),
    "BrainTurnCompleted": ("provider", "model", "finish_reason", "tokens_in", "tokens_out"),
    "ToolCallStarted": ("tool_name", "args_preview"),
    "ToolCallCompleted": ("success", "duration_ms", "output_preview", "error"),
    "ActionProposed": ("tool_name", "args", "risk_tier", "rationale"),
    "ActionExecuted": ("tool_name", "success", "duration_ms", "error", "output_preview"),
    "ActionDenied": ("tool_name", "reason"),
    "ObservationCaptured": ("window_title",),
    "ActionPlanned": ("action_kind", "target_hint"),
    "CUStepProfiled": ("phase", "step_idx"),
    "JarvisAgentTaskStarted": ("utterance", "provider", "model"),
    "JarvisAgentTaskCompleted": ("success", "duration_s"),
    "AnnouncementRequested": ("kind", "text"),
}

#: Free-text caps: long enough to read, short enough for a row in a file.
_TEXT_MAX = 240
_ARG_MAX = 160
_ARGS_MAX_KEYS = 8
#: How many events the ring keeps. A turn rarely exceeds a few dozen; the
#: ring spans the last several turns so a slow reply still finds its slice.
DEFAULT_RING_SIZE = 600


def _scalar(value: Any, *, max_chars: int) -> Any:
    """Numbers and booleans stay typed; everything else is a redacted preview."""
    if value is None or isinstance(value, bool | int | float):
        return value
    return safe_preview(value, max_chars=max_chars)


def trace_payload_for(name: str, event: Any) -> dict[str, Any]:
    """The stored payload of one event: whitelisted keys, redacted free text."""
    out: dict[str, Any] = {}
    for key in _PAYLOAD_KEYS.get(name, ()):
        if isinstance(event, Mapping):
            if key not in event:
                continue
            value = event[key]
        else:
            if not hasattr(event, key):
                continue
            value = getattr(event, key)
        if key == "args":
            if isinstance(value, Mapping):
                args: dict[str, Any] = {}
                for i, (k, v) in enumerate(value.items()):
                    if i >= _ARGS_MAX_KEYS:
                        break
                    args[str(k)] = _scalar(v, max_chars=_ARG_MAX)
                out[key] = args
            continue
        out[key] = _scalar(value, max_chars=_TEXT_MAX)
    return out


@dataclass(frozen=True, slots=True)
class TraceEvent:
    ts_ms: int
    name: str
    trace_id: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ts_ms": self.ts_ms, "payload": self.payload}


class TurnTraceCollector:
    """Bounded ring of trace events; ``slice`` hands a turn its own."""

    def __init__(self, bus: EventBus | None = None, *, ring_size: int = DEFAULT_RING_SIZE) -> None:
        self._ring: deque[TraceEvent] = deque(maxlen=max(1, ring_size))
        self._lock = threading.Lock()
        if bus is not None:
            bus.subscribe_all(self._on_event)

    async def _on_event(self, event: Event) -> None:
        try:
            name = type(event).__name__
            if name not in TRACE_EVENT_KINDS:
                return
            self.record(name, event)
        except Exception:  # noqa: BLE001 — a trace must never reach the publisher (AP-18)
            log.debug("turn trace: event dropped", exc_info=True)

    def record(self, name: str, event: Any) -> None:
        """Append one event (also the seam tests use, without a bus)."""
        ts_ns = getattr(event, "timestamp_ns", None)
        if not isinstance(ts_ns, int):
            import time

            ts_ns = time.time_ns()
        item = TraceEvent(
            ts_ms=ts_ns // 1_000_000,
            name=name,
            trace_id=str(getattr(event, "trace_id", "") or ""),
            payload=trace_payload_for(name, event),
        )
        with self._lock:
            self._ring.append(item)

    def slice(self, since_ms: int, until_ms: int | None = None) -> list[dict[str, Any]]:
        """Events with ``since_ms <= ts_ms <= until_ms``, oldest first."""
        with self._lock:
            items = list(self._ring)
        out = []
        for item in items:
            if item.ts_ms < since_ms:
                continue
            if until_ms is not None and item.ts_ms > until_ms:
                continue
            out.append(item.as_dict())
        return out

    def snapshot(self, since_ms: int, until_ms: int | None = None) -> dict[str, Any] | None:
        """The stored trace of a turn — ``None`` when nothing happened in it."""
        events = self.slice(since_ms, until_ms)
        if not events:
            return None
        ended = until_ms if until_ms is not None else events[-1]["ts_ms"]
        return {"started_ms": since_ms, "ended_ms": ended, "events": events}


def trace_from_events(
    events: list[Any],
    *,
    since_ms: int,
    until_ms: int | None = None,
) -> dict[str, Any] | None:
    """Build a stored trace from already-persisted event rows.

    The voice session recorder keeps every event of a session in
    ``voice_events`` (kind + payload); this turns the rows of one turn into
    the same shape the collector produces, so a voice turn in the history
    shows its steps exactly like a typed one. Rows are anything with
    ``kind``/``ts_ms``/``payload`` attributes or keys.
    """
    out: list[dict[str, Any]] = []
    for row in events:
        kind = _field(row, "kind")
        if kind not in TRACE_EVENT_KINDS:
            continue
        ts = _field(row, "ts_ms")
        if not isinstance(ts, int | float):
            continue
        ts_ms = int(ts)
        if ts_ms < since_ms or (until_ms is not None and ts_ms > until_ms):
            continue
        payload = _field(row, "payload")
        out.append(
            {
                "name": kind,
                "ts_ms": ts_ms,
                "payload": trace_payload_for(kind, payload if isinstance(payload, Mapping) else {}),
            }
        )
    if not out:
        return None
    ended = until_ms if until_ms is not None else out[-1]["ts_ms"]
    return {"started_ms": since_ms, "ended_ms": ended, "events": out}


def _field(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)
