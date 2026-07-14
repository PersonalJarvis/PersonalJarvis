"""WebSocket /api/runs/live — forwards run-relevant bus events to the open
Run Inspector so the in-flight run grows in real time.

Thin read-only adapter: it subscribes to the same EventBus the recorder uses,
filters to the forensic event kinds, and pushes compact frames. The receive
loop treats any non-clean read error as terminal (break, never continue) — an
unclean client teardown raises RuntimeError, not WebSocketDisconnect, and a
continue would spin on a dead socket (AP-20).

Bus access
----------
The bus is resolved from ``app.state.bus`` via ``ws.scope["app"]``, matching
the pattern in ``jarvis/ui/web/wiki_ws.py``.  The ``subscribe_all`` callback
is **async** — matching the real ``EventBus`` signature (``Handler = Callable[[Event],
Awaitable[None]]``).

Unsubscribe
-----------
The ``finally`` block detaches the wildcard handler via
``EventBus.unsubscribe_all`` so ``_wildcard_subscribers`` does not grow a dead
reference per connect/disconnect — important for a long-lived tray process
where the Run Inspector view is opened and closed repeatedly.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

router = APIRouter()

# Event kinds the inspector cares about live (superset of the recorder forensic
# additions). Keep in sync with jarvis/sessions/recorder.py::_RAW_EVENT_KINDS.
_LIVE_KINDS: frozenset[str] = frozenset({
    "VoiceSessionStarted",
    "VoiceSessionEnded",
    "VoiceTurnStarted",
    "VoiceTurnCompleted",
    "RealtimeSessionReady",
    "WakeWordDetected",
    "ListeningStarted",
    "TranscriptFinal",
    "IntentClassified",
    "ActionProposed",
    "ActionApproved",
    "ActionDenied",
    "BrainTurnStarted",
    "BrainTurnCompleted",
    "BrainTTFT",
    "ToolCallStarted",
    "ToolCallCompleted",
    "ActionExecuted",
    "ResponseGenerated",
    "SystemStateChanged",
    "LatencySpan",
    "ErrorOccurred",
    "SpeechSpoken",
    "JarvisAgentTaskStarted",
    "JarvisAgentTaskCompleted",
})


def _resolve_bus(ws: WebSocket):
    """Pull the shared EventBus off ``app.state``.

    Returns ``None`` when the bus has not been wired yet (e.g. early startup).
    Matches the pattern used in ``jarvis/ui/web/wiki_ws.py``.
    """
    app = ws.scope.get("app")
    if app is None:
        return None
    return getattr(app.state, "bus", None)


@router.websocket("/api/runs/live")
async def runs_live(ws: WebSocket) -> None:
    """Live-event stream for the Run Inspector.

    Each bus event whose class name is in ``_LIVE_KINDS`` is forwarded as a
    compact JSON frame::

        {
            "type":       "event",
            "kind":       "TranscriptFinal",
            "ts_ms":      1718000000123,
            "session_id": "abc123",
            "trace_id":   "550e8400-e29b-..."
        }

    A ``welcome`` frame is sent immediately after accept so the client can
    detect a live connection before any events arrive.

    The receive loop treats **any** non-clean read error as terminal (``break``,
    never ``continue``) — an unclean client teardown raises ``RuntimeError``,
    not ``WebSocketDisconnect``, and a ``continue`` would spin on a dead socket
    at ~9 MB/s log output until the app self-restarts (AP-20).
    """
    await ws.accept()
    await ws.send_json({"type": "welcome", "channel": "runs.live"})

    bus = _resolve_bus(ws)
    if bus is None:
        await ws.send_json({"type": "unavailable", "reason": "no-bus"})
        await ws.close()
        return

    async def _forward(event) -> None:
        kind = type(event).__name__
        if kind not in _LIVE_KINDS:
            return
        try:
            await ws.send_json(
                {
                    "type": "event",
                    "kind": kind,
                    "ts_ms": getattr(event, "timestamp_ns", 0) // 1_000_000,
                    "session_id": getattr(event, "session_id", None),
                    "trace_id": str(getattr(event, "trace_id", "")),
                }
            )
        except Exception:  # noqa: BLE001,S110 — socket gone; recv loop will terminate
            pass

    bus.subscribe_all(_forward)
    try:
        while True:
            try:
                await ws.receive_text()  # keepalive / client pings; ignore content
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # Unclean client disconnect raises RuntimeError("WebSocket is
                # not connected"), not WebSocketDisconnect.  Treat as terminal
                # (AP-20) — NEVER continue on a dead socket.
                break
    finally:
        # Detach the wildcard handler so _wildcard_subscribers does not grow a
        # dead reference per connect/disconnect (long-lived tray process).
        try:
            bus.unsubscribe_all(_forward)
        except Exception as exc:  # noqa: BLE001 — detach best-effort
            log.debug("runs_ws: unsubscribe_all raised (%s) — ignoring", exc)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001,S110
            pass


__all__ = ["router", "runs_live"]
