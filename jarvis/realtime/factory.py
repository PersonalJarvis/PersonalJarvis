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


def _ordered_families(cfg: Any) -> list[tuple[str, str, Any]]:
    """Return the (id, secret-name, class) realtime family list, configured
    provider first — the single shared ordering both resolvers below use, so
    "which realtime provider is active" can never disagree between the
    session builder and the availability check (AP-22)."""
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
    return sorted(families, key=lambda f: f[0] != configured)  # configured first


def _resolve_realtime_provider(cfg: Any) -> Any:
    """Return an instantiated realtime provider by key presence (cross-family,
    AP-22), preferring [brain.realtime].provider; None when no realtime key."""
    for _id, secret, cls in _ordered_families(cfg):
        if get_provider_secret(secret):
            return cls()
    return None


def realtime_available_provider(cfg: Any) -> str | None:
    """Return the resolved realtime provider id (cross-family, AP-22) — id
    counterpart of :func:`_resolve_realtime_provider`, sharing the exact same
    ``_ordered_families`` ordering so the two can never drift. Used by the
    voice-mode route to compute ``realtime_available`` / ``active_provider``
    without instantiating a provider (and its SDK client) just to check."""
    for provider_id, secret, _cls in _ordered_families(cfg):
        if get_provider_secret(secret):
            return provider_id
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
