# jarvis/realtime/factory.py
"""Build a RealtimeVoiceSession for the browser /ws/audio path.

Returns None (=> caller runs the classic path) when realtime is not selected or
no OpenAI key is present. Phase 1 is OpenAI-only by scope; the cross-family
chain lands in Phase 4.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.config import get_provider_secret

log = logging.getLogger(__name__)


def build_realtime_session(
    *, cfg: Any, bus: Any, session_id: str, send_binary: Any, send_json: Any
):
    mode = getattr(getattr(cfg, "voice", None), "mode", "pipeline")
    if mode != "realtime":
        return None
    if not get_provider_secret("openai"):
        log.info("realtime: no OpenAI key — falling back to the classic path")
        return None
    try:
        from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
        from jarvis.realtime.session import RealtimeVoiceSession

        return RealtimeVoiceSession(
            session_id=session_id,
            send_binary=send_binary,
            send_json=send_json,
            provider=OpenAIRealtimeProvider(),
            config=cfg,
            bus=bus,
        )
    except Exception as exc:  # noqa: BLE001 — unbuildable stack => classic path
        log.warning("realtime: session build failed: %s", exc)
        return None
