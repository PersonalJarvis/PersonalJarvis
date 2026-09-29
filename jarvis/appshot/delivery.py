"""Hand an appshot to a running voice call.

Every continuous voice session (``jarvis.live``) that can take a picture
exposes ``attach_appshot(image, mime, note)``: GPT-Live puts it into its
thinking backend's context, native live providers receive it as a video
frame. The call stays silent — an appshot is context for what the user says
next, not a question of its own.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def live_call_running() -> bool:
    try:
        from jarvis.live.runtime import active  # noqa: PLC0415

        return any(getattr(session, "is_active", False) for session in active())
    except Exception:  # noqa: BLE001 - a probe failure means "no call"
        log.debug("appshot: live-session probe failed", exc_info=True)
        return False


async def deliver_to_live(image: bytes, mime: str, note: str) -> bool:
    """``True`` when a running voice call accepted the picture."""
    try:
        from jarvis.live.runtime import active  # noqa: PLC0415

        sessions: tuple[Any, ...] = active()
    except Exception:  # noqa: BLE001 - no live package state means no call
        log.debug("appshot: live runtime unavailable", exc_info=True)
        return False
    for session in sessions:
        attach = getattr(session, "attach_appshot", None)
        if not callable(attach) or not getattr(session, "is_active", False):
            continue
        try:
            if await attach(image, mime, note):
                return True
        except Exception:  # noqa: BLE001 - fall back to the next message
            log.warning("appshot: the voice call rejected the appshot", exc_info=True)
    return False


__all__ = ["deliver_to_live", "live_call_running"]
