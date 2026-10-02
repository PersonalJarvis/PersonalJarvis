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
re-answers, and a session that is busy typing keeps running. When the front
page bound a chat (``AgentChatService.bind_voice_chat``) the turn goes there
instead, and every turn of one call stays in the chat its first turn chose.
The imported turn also becomes context for the next typed turn, because the
API runner rebuilds its history from the same event log.

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
            call_id = str(getattr(event, "session_id", "") or "")
            session = self._target_session(svc, call_id)
            if session is None:
                session = self._ensure_session(svc, event)
                if session is None:
                    return
                if getattr(svc, "voice_chat_fresh", False):
                    # The blank page's call now has its chat; the next call
                    # from that page continues it instead of opening another.
                    svc.bind_voice_chat(session.session_id)
            pin = getattr(svc, "pin_voice_call", None)
            if callable(pin):
                pin(call_id, session.session_id)
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
    def _target_session(svc: Any, call_id: str = "") -> Any | None:
        """The chat this turn belongs in.

        In order: the chat this call already files into, the chat the front
        page bound (``AgentChatService.bind_voice_chat``), and — when nothing
        was ever bound — the newest Jarvis chat. ``None`` asks for a new chat:
        a blank page is open, or there is no chat yet.
        """
        try:
            for chat_id in (
                getattr(svc, "voice_call_chat", lambda _c: None)(call_id),
                getattr(svc, "voice_chat_id", None),
            ):
                if not chat_id:
                    continue
                session = svc.store.get_session(chat_id)
                if session is not None and session.surface == SURFACE:
                    return session
            if getattr(svc, "voice_chat_fresh", False):
                return None
            sessions = svc.store.list_sessions(limit=1, surface=SURFACE)
        except Exception as exc:  # noqa: BLE001
            log.debug("voice chat mirror could not list sessions: %s", exc)
            return None
        return sessions[0] if sessions else None

    @staticmethod
    def _ensure_session(svc: Any, event: Any) -> Any | None:
        """Create the Jarvis chat a call without one files into.

        The chat is seated on the person's own chat pick, never on the voice
        model: going back to typing must find the model they chose for the
        chat, not the realtime voice (whose model id is no chat model at
        all). In order: the last explicit chat pick, the seat of the newest
        Jarvis chat, the voice turn's provider when the chat offers it (with
        its default model), the surface's first row. A caller that never
        opened the chat finds the spoken history waiting instead of an empty
        page.
        """
        try:
            from jarvis.agent_chat.catalog import offers, rows_for

            seat = VoiceChatMirror._chat_seat(svc)
            if seat is None:
                provider = str(getattr(event, "provider", "") or "").strip().lower()
                if not offers(SURFACE, provider):
                    rows = rows_for(SURFACE)
                    provider = rows[0].id if rows else ""
                if not provider:
                    return None
                seat = {"provider": provider}
            return svc.create_session(surface=SURFACE, **seat)
        except Exception as exc:  # noqa: BLE001
            log.debug("voice chat mirror could not create a session: %s", exc)
            return None

    @staticmethod
    def _chat_seat(svc: Any) -> dict[str, str] | None:
        """The provider / model / effort the person last picked for the chat."""
        from jarvis.agent_chat.catalog import offers

        read_selection = getattr(svc.store, "chat_selection", None)
        selection = read_selection() if callable(read_selection) else None
        if selection is not None and offers(SURFACE, selection.provider):
            seat = {
                "provider": selection.provider,
                "model": selection.model,
                "effort": selection.effort,
            }
            if getattr(selection, "account_id", ""):
                seat["account_id"] = selection.account_id
            return seat
        newest = svc.store.list_sessions(limit=1, surface=SURFACE)
        if newest and offers(SURFACE, newest[0].provider):
            session = newest[0]
            seat = {"provider": session.provider, "model": session.model, "effort": session.effort}
            if getattr(session, "account_id", ""):
                seat["account_id"] = session.account_id
            return seat
        return None


__all__ = ["SURFACE", "VoiceChatMirror"]
