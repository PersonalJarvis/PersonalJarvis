# jarvis/realtime/factory.py
"""Build a RealtimeVoiceSession for the browser /ws/audio path.

Returns None (=> caller runs the classic path) when realtime is not selected
or no realtime key is present in ANY supported family. The realtime provider
is resolved key-aware and cross-family (OpenAI Realtime <-> Gemini Live,
AP-22): the configured ``[brain.realtime].provider`` wins when it is keyed,
otherwise the factory crosses to whichever family actually has a key, and
only degrades to the classic pipeline when neither family is reachable.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.config import get_provider_secret

log = logging.getLogger(__name__)


def _resolve_realtime_provider(cfg: Any) -> Any:
    """Return an instantiated realtime provider by key presence (cross-family,
    AP-22), preferring [brain.realtime].provider; None when no realtime key."""
    from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider
    from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider

    # (id, secret-name, class) — capability/key-gated, never name-pinned behavior
    families = [
        ("openai-realtime", "openai", OpenAIRealtimeProvider),
        ("gemini-live", "gemini", GeminiLiveProvider),
    ]
    configured = (
        getattr(getattr(getattr(cfg, "brain", None), "realtime", None), "provider", "")
        or "openai-realtime"
    )
    ordered = sorted(families, key=lambda f: f[0] != configured)  # configured first
    for _id, secret, cls in ordered:
        if get_provider_secret(secret):
            return cls()
    return None


def build_realtime_session(
    *, cfg: Any, bus: Any, session_id: str, send_binary: Any, send_json: Any
):
    mode = getattr(getattr(cfg, "voice", None), "mode", "pipeline")
    if mode != "realtime":
        return None
    try:
        provider = _resolve_realtime_provider(cfg)
        if provider is None:
            log.info("realtime: no realtime key in any family — classic path")
            return None

        from jarvis.realtime.session import RealtimeVoiceSession

        return RealtimeVoiceSession(
            session_id=session_id,
            send_binary=send_binary,
            send_json=send_json,
            provider=provider,
            config=cfg,
            bus=bus,
        )
    except Exception as exc:  # noqa: BLE001 — unbuildable stack => classic path
        log.warning("realtime: session build failed: %s", exc)
        return None
