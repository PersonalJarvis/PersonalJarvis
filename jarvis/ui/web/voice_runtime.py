"""Bridge voice-setting routes to the effective desktop speech runtime.

The persisted/configured engine and an already-open call are separate pieces
of state. These helpers keep routes honest without making headless/browser-only
deployments depend on the desktop speech stack.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _pipeline(request: Any) -> Any | None:
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    return getattr(state, "speech_pipeline", None)


def voice_engine_status(request: Any) -> dict[str, Any]:
    pipeline = _pipeline(request)
    snapshot = getattr(pipeline, "voice_engine_status", None)
    if not callable(snapshot):
        return {
            "session_active": False,
            "session_id": "",
            "active_session_mode": None,
            "active_session_provider": "",
            "active_session_model": "",
            "transitioning": False,
        }
    try:
        value = snapshot()
    except Exception:  # noqa: BLE001 -- status must never break settings
        log.debug("Voice-engine status snapshot failed", exc_info=True)
        return {
            "session_active": False,
            "session_id": "",
            "active_session_mode": None,
            "active_session_provider": "",
            "active_session_model": "",
            "transitioning": False,
        }
    return dict(value) if isinstance(value, dict) else {}


def apply_voice_mode(request: Any, mode: str) -> bool:
    pipeline = _pipeline(request)
    apply = getattr(pipeline, "apply_voice_mode", None)
    if not callable(apply):
        return False
    try:
        return bool(apply(mode))
    except Exception:  # noqa: BLE001 -- config remains applied for next session
        log.warning("Live voice-mode application failed", exc_info=True)
        return False


def reconnect_realtime(request: Any, *, reason: str) -> bool:
    pipeline = _pipeline(request)
    reconnect = getattr(pipeline, "reconnect_realtime_session", None)
    if not callable(reconnect):
        return False
    try:
        return bool(reconnect(reason=reason))
    except Exception:  # noqa: BLE001 -- new config still applies next session
        log.warning("Live realtime reconnect failed", exc_info=True)
        return False


__all__ = ["apply_voice_mode", "reconnect_realtime", "voice_engine_status"]
