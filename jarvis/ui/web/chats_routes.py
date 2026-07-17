"""REST routes for the Chats conversation manager.

A unified history over two sources:
  - **text** conversations from :class:`jarvis.state.chat_store.ChatStore`
  - **voice** sessions from :class:`jarvis.sessions.store.SessionStore`

Endpoints (prefix ``/api/chats``):
    GET    /api/chats                         Unified list, newest-first.
    GET    /api/chats/{kind}/{id}             Normalized transcript.
    POST   /api/chats                         Create a new (empty) text thread.
    POST   /api/chats/{kind}/{id}/resume      Seed the web brain → continue by text.
    POST   /api/chats/{kind}/{id}/speak       Seed the voice brain + open the mic.
    DELETE /api/chats/text/{id}               Delete a text thread.

Registered in ``server.py::_build_app()`` next to ``sessions_router``.

DI follows the established ``getattr(request.app.state, X, None)`` + 503
pattern. The ``/speak`` path needs a live speech pipeline (``app.state.
speech_pipeline``), which only exists in the full desktop app — on a headless
VPS it returns 503, consistent with the cloud-first doctrine (voice is a
desktop extra).

Loopback-only (server binds 127.0.0.1) — no auth token needed.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.state.chat_store import ChatStore
from jarvis.state.conversation_constants import (
    CONVERSATION_KIND_TEXT,
    CONVERSATION_KIND_VOICE,
    ConversationKind,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])

# How many trailing turns to seed into the brain when resuming/speaking. The
# brain caps its own buffer too (BrainManager._HISTORY_MAX); this keeps the
# payload small for very long voice sessions.
_SEED_MAX_MESSAGES = 40

# Roles that are meaningful to seed back into the brain conversation buffer.
_SEEDABLE_ROLES = frozenset({"user", "assistant", "system"})


# ----------------------------------------------------------------------
# Response models
# ----------------------------------------------------------------------


class ConversationSummary(BaseModel):
    """One row in the unified history list."""

    kind: ConversationKind
    id: str
    title: str = ""
    preview: str = ""
    created_ms: int = 0
    updated_ms: int = 0
    message_count: int = 0


class ChatTurn(BaseModel):
    """A single normalized message (text thread message OR half a voice turn)."""

    role: str
    text: str
    ts_ms: int = 0


class ConversationDetail(BaseModel):
    kind: ConversationKind
    id: str
    title: str = ""
    messages: list[ChatTurn] = Field(default_factory=list)


class NewChatResponse(BaseModel):
    kind: ConversationKind = CONVERSATION_KIND_TEXT
    id: str
    title: str = ""


class NewChatRequest(BaseModel):
    title: str = "New Chat"


class ResumeResponse(BaseModel):
    kind: ConversationKind
    id: str
    title: str = ""
    messages: list[ChatTurn] = Field(default_factory=list)
    seeded_turns: int = 0


class SpeakResponse(BaseModel):
    armed: bool
    seeded_turns: int = 0


# ----------------------------------------------------------------------
# DI helpers
# ----------------------------------------------------------------------


def _require_chat_store(request: Request) -> ChatStore:
    store = getattr(request.app.state, "chat_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="chat-store-unavailable")
    return store


def _optional_session_store(request: Request) -> Any | None:
    return getattr(request.app.state, "session_store", None)


def _optional_brain(request: Request) -> Any | None:
    return getattr(request.app.state, "brain", None)


def _optional_pipeline(request: Request) -> Any | None:
    return getattr(request.app.state, "speech_pipeline", None)


# ----------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------


def _voice_session_to_summary(s: Any) -> ConversationSummary:
    preview = getattr(s, "preview", "") or ""
    started = int(getattr(s, "started_ms", 0) or 0)
    ended = getattr(s, "ended_ms", None)
    return ConversationSummary(
        kind=CONVERSATION_KIND_VOICE,
        id=str(s.id),
        title=preview or "Voice session",
        preview=preview,
        created_ms=started,
        updated_ms=int(ended) if ended else started,
        message_count=int(getattr(s, "turn_count", 0) or 0),
    )


def _text_thread_to_summary(t: dict[str, Any]) -> ConversationSummary:
    return ConversationSummary(
        kind=CONVERSATION_KIND_TEXT,
        id=str(t["thread_id"]),
        title=t.get("title") or t.get("preview") or "New Chat",
        preview=t.get("preview", "") or "",
        created_ms=int(t.get("created_at_ns", 0)) // 1_000_000,
        updated_ms=int(t.get("updated_at_ns", t.get("created_at_ns", 0))) // 1_000_000,
        message_count=int(t.get("message_count", 0)),
    )


def _normalized_messages(
    kind: str, cid: str, chat_store: ChatStore, session_store: Any | None
) -> list[ChatTurn] | None:
    """Flatten either source into an ordered list of messages. None = not found."""
    if kind == CONVERSATION_KIND_TEXT:
        thread = chat_store.get_thread(cid)
        if thread is None:
            return None
        return [
            ChatTurn(
                role=m["role"],
                text=m["text"],
                ts_ms=int(m["timestamp_ns"]) // 1_000_000,
            )
            for m in thread["messages"]
        ]
    if kind == CONVERSATION_KIND_VOICE:
        if session_store is None:
            return None
        session = session_store.get_session(cid)
        if session is None:
            return None
        turns = session_store.get_turns(cid)
        out: list[ChatTurn] = []
        for turn in turns:
            if getattr(turn, "user_text", ""):
                out.append(
                    ChatTurn(role="user", text=turn.user_text, ts_ms=int(turn.started_ms))
                )
            if getattr(turn, "jarvis_text", ""):
                out.append(
                    ChatTurn(
                        role="assistant",
                        text=turn.jarvis_text,
                        ts_ms=int(turn.ended_ms or turn.started_ms),
                    )
                )
        return out
    return None


def _seed_pairs(messages: list[ChatTurn]) -> list[tuple[str, str]]:
    """Trailing seedable (role, text) pairs for BrainManager.seed_history."""
    pairs = [
        (m.role, m.text)
        for m in messages
        if m.role in _SEEDABLE_ROLES and m.text.strip()
    ]
    return pairs[-_SEED_MAX_MESSAGES:]


def _conversation_title(
    kind: str, cid: str, chat_store: ChatStore, session_store: Any | None
) -> str:
    if kind == CONVERSATION_KIND_TEXT:
        thread = chat_store.get_thread(cid)
        return (thread or {}).get("title", "") if thread else ""
    if kind == CONVERSATION_KIND_VOICE and session_store is not None:
        session = session_store.get_session(cid)
        return "Voice session" if session is not None else ""
    return ""


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    request: Request,
    days: int = Query(default=0, ge=0, le=3650),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[ConversationSummary]:
    """Unified history (text threads + voice sessions), newest-first.

    ``days`` is a soft recent-window filter (0 = no filter, show all).
    """
    chat_store = _require_chat_store(request)
    session_store = _optional_session_store(request)

    items: list[ConversationSummary] = [
        _text_thread_to_summary(t) for t in chat_store.list_threads()
    ]
    if session_store is not None:
        try:
            for s in session_store.list_sessions(limit=limit, include_empty=False):
                items.append(_voice_session_to_summary(s))
        except Exception as exc:  # noqa: BLE001 — voice list must never 500 the page
            log.warning("voice session list failed, showing text-only: %s", exc)

    if days > 0:
        cutoff = int(time.time() * 1000) - days * 86_400_000
        items = [c for c in items if c.updated_ms >= cutoff]

    items.sort(key=lambda c: c.updated_ms, reverse=True)
    return items[:limit]


@router.post("", response_model=NewChatResponse)
async def create_chat(request: Request, body: NewChatRequest | None = None) -> NewChatResponse:
    """Create a new, empty text conversation and return its id."""
    chat_store = _require_chat_store(request)
    title = (body.title if body else "New Chat") or "New Chat"
    created = await chat_store.create_thread(title=title)
    return NewChatResponse(id=str(created["thread_id"]), title=created["title"])


@router.get("/{kind}/{cid}", response_model=ConversationDetail)
async def get_conversation(
    kind: ConversationKind, cid: str, request: Request
) -> ConversationDetail:
    """Normalized transcript for one conversation."""
    chat_store = _require_chat_store(request)
    session_store = _optional_session_store(request)
    messages = _normalized_messages(kind, cid, chat_store, session_store)
    if messages is None:
        raise HTTPException(status_code=404, detail="conversation-not-found")
    return ConversationDetail(
        kind=kind,
        id=cid,
        title=_conversation_title(kind, cid, chat_store, session_store),
        messages=messages,
    )


@router.post("/{kind}/{cid}/resume", response_model=ResumeResponse)
async def resume_conversation(
    kind: ConversationKind, cid: str, request: Request
) -> ResumeResponse:
    """Seed the (text) brain with this conversation so the next typed message
    continues it coherently, and return the messages so the UI can render it
    as the active conversation."""
    chat_store = _require_chat_store(request)
    session_store = _optional_session_store(request)
    messages = _normalized_messages(kind, cid, chat_store, session_store)
    if messages is None:
        raise HTTPException(status_code=404, detail="conversation-not-found")

    seeded = 0
    brain = _optional_brain(request)
    if brain is not None and hasattr(brain, "seed_history"):
        pairs = _seed_pairs(messages)
        brain.seed_history(pairs)
        seeded = len(pairs)

    return ResumeResponse(
        kind=kind,
        id=cid,
        title=_conversation_title(kind, cid, chat_store, session_store),
        messages=messages,
        seeded_turns=seeded,
    )


@router.post("/{kind}/{cid}/speak", response_model=SpeakResponse)
async def speak_in_conversation(
    kind: ConversationKind, cid: str, request: Request
) -> SpeakResponse:
    """Start a voice session seeded with this conversation — "Hey Jarvis" that
    already remembers where you left off.

    503 when no speech pipeline is wired (headless / VPS): voice is a desktop
    extra. The pipeline seeds its own brain and arms the mic on its own loop.
    """
    chat_store = _require_chat_store(request)
    session_store = _optional_session_store(request)
    pipeline = _optional_pipeline(request)
    if pipeline is None or not hasattr(pipeline, "request_voice_session"):
        raise HTTPException(status_code=503, detail="voice-pipeline-unavailable")

    messages = _normalized_messages(kind, cid, chat_store, session_store)
    if messages is None:
        raise HTTPException(status_code=404, detail="conversation-not-found")

    pairs = _seed_pairs(messages)
    armed = bool(pipeline.request_voice_session(seed_messages=pairs))
    return SpeakResponse(armed=armed, seeded_turns=len(pairs))


@router.delete("/text/{cid}")
async def delete_text_conversation(cid: str, request: Request) -> dict[str, Any]:
    """Delete a text conversation. Voice sessions are retention-managed and not
    deletable here."""
    chat_store = _require_chat_store(request)
    deleted = await chat_store.delete_thread(cid)
    if not deleted:
        raise HTTPException(status_code=404, detail="conversation-not-found")
    return {"deleted": True, "id": cid}
