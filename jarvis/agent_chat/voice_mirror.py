"""Mirror completed voice turns into the Jarvis agent-chat history.

Spoken and typed conversations reach the same assistant through different
stacks with different stores: a voice turn lands in ``sessions.db`` (and on
the voice stage's live transcript), a typed turn in ``agent_chat.db``. The
Jarvis agent card shows one of the two at a time (``Voice | Chat``), so a
spoken question and its spoken answer never appeared in the chat — switching
back from voice to chat showed nothing of what was just said.

This bridge closes that gap at the turn boundary, off the voice hot path
(AP-9): it subscribes to :class:`VoiceTurnCompleted` on the app bus and files
each turn's two texts into the newest ``jarvis``-surface chat session via
:meth:`AgentChatService.import_voice_turn` — no runner starts, nothing
re-answers, and a session that is busy typing keeps running. The imported
turn also becomes context for the next typed turn, because the API runner
rebuilds its history from the same event log.

Only the lead's chat (surface ``jarvis``) is mirrored: the other society
agents have no microphone.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final

log = logging.getLogger(__name__)

#: The one chat surface a voice turn belongs in (see module docstring).
SURFACE: Final[str] = "jarvis"


class VoiceChatMirror:
    """Bus subscriber that files voice turns into the Jarvis chat."""

    def __init__(self, get_service: Callable[[], Any | None]) -> None:
        self._get_service = get_service
        self._attached = False

    def attach(self, bus: Any) -> None:
        """Subscribe to completed voice turns. Idempotent."""
        if self._attached:
            return
        try:
            from jarvis.core.events import VoiceTurnCompleted

            bus.subscribe(VoiceTurnCompleted, self._on_turn)
        except Exception as exc:  # noqa: BLE001 — mirroring must never break boot
            log.warning("voice chat mirror could not attach: %s", exc)
            return
        self._attached = True
        log.info("VoiceChatMirror attached to bus")

    async def _on_turn(self, event: Any) -> None:
        """File one turn's texts into the newest Jarvis chat session."""
        try:
            user_text = str(getattr(event, "user_text", "") or "")
            jarvis_text = str(getattr(event, "jarvis_text", "") or "")
            if not user_text.strip() and not jarvis_text.strip():
                return
            svc = self._get_service()
            if svc is None:
                return
            session = self._target_session(svc)
            if session is None:
                session = self._ensure_session(svc, event)
                if session is None:
                    return
            await svc.import_voice_turn(
                session.session_id,
                user_text,
                jarvis_text,
                provider=str(getattr(event, "provider", "") or ""),
                model=str(getattr(event, "model", "") or ""),
                voice_turn_id=str(getattr(event, "turn_id", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001 — an observer must never break a turn
            log.debug("voice chat mirror skipped a turn: %s", exc)

    @staticmethod
    def _target_session(svc: Any) -> Any | None:
        """The newest Jarvis-surface session — the chat the card shows."""
        try:
            sessions = svc.store.list_sessions(limit=1, surface=SURFACE)
        except Exception as exc:  # noqa: BLE001
            log.debug("voice chat mirror could not list sessions: %s", exc)
            return None
        return sessions[0] if sessions else None

    @staticmethod
    def _ensure_session(svc: Any, event: Any) -> Any | None:
        """Create the first Jarvis chat so early voice turns are not lost.

        The provider is the voice turn's own when the chat offers it, else
        the surface's first row. A caller that never opened the chat finds
        the spoken history waiting instead of an empty page.
        """
        try:
            from jarvis.agent_chat.catalog import offers, rows_for

            provider = str(getattr(event, "provider", "") or "").strip().lower()
            if not offers(SURFACE, provider):
                rows = rows_for(SURFACE)
                provider = rows[0].id if rows else ""
            if not provider:
                return None
            return svc.create_session(
                provider=provider,
                model=str(getattr(event, "model", "") or ""),
                surface=SURFACE,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("voice chat mirror could not create a session: %s", exc)
            return None


__all__ = ["SURFACE", "VoiceChatMirror"]
