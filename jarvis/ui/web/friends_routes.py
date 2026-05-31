# === F-FRIENDS [F3] · feature/friends-section · the maintainer-2026-05-01 ===
# === F-FRIENDS [F2] · feature/friends-section · the maintainer-2026-04-30 ===
"""REST-API fuer die Friends-Sektion (Phase F2).

Pattern wie ``missions_routes.py``:
- Resources (``FriendRegistry``, ``ChannelManager``) liegen in
  ``app.state.friend_registry`` / ``app.state.channel_manager``.
- Nicht gesetzt → ``HTTPException(503)``.
- Pydantic-Body-Models inline.

Endpoints (Phase F2):

- ``GET    /api/friends``                          → Liste mit Channels.
- ``POST   /api/friends``                          → Neuen Friend anlegen.
- ``GET    /api/friends/{friend_id}``              → Detail + Channels + Permission.
- ``PATCH  /api/friends/{friend_id}``              → display_name, note aktualisieren.
- ``DELETE /api/friends/{friend_id}``              → Friend + Channels + Permission loeschen.
- ``POST   /api/friends/{friend_id}/channels``     → Channel-Link hinzufuegen.
- ``DELETE /api/friends/{friend_id}/channels/{channel}/{handle}`` → Unlink.
- ``GET    /api/friends/{friend_id}/permission``   → Status-Permission lesen.
- ``PATCH  /api/friends/{friend_id}/permission``   → Profile setzen.
- ``GET    /api/friends/{friend_id}/messages``     → Chat-Thread (F2: Stub).
- ``POST   /api/friends/{friend_id}/messages``     → Outbound via primary Channel.
"""
from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.channels.base import ChannelMessage
from jarvis.channels.manager import ChannelManager
from jarvis.friends.messages import DirectMessage
from jarvis.friends.models import (
    Friend,
    FriendChannel,
    FriendChannelKind,
    StatusProfile,
)
from jarvis.friends.registry import FriendNotFoundError, FriendRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/friends", tags=["friends"])


# ---------------------------------------------------------------------------
# DI-Helpers
# ---------------------------------------------------------------------------


def _require_registry(request: Request) -> FriendRegistry:
    reg = getattr(request.app.state, "friend_registry", None)
    if reg is None:
        raise HTTPException(
            status_code=503, detail="FriendRegistry nicht verfuegbar"
        )
    return reg


def _optional_channel_manager(request: Request) -> ChannelManager | None:
    return getattr(request.app.state, "channel_manager", None)


def _parse_friend_id(friend_id: str) -> UUID:
    try:
        return UUID(friend_id)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalide Friend-ID: {friend_id!r}"
        ) from exc


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class ChannelLinkDTO(BaseModel):
    channel: FriendChannelKind
    handle: str
    is_primary: bool = False
    linked_at_ns: int


class FriendDTO(BaseModel):
    id: str
    display_name: str
    avatar_url: str | None = None
    note: str | None = None
    created_at_ns: int
    channels: list[ChannelLinkDTO] = Field(default_factory=list)


class FriendDetailDTO(FriendDTO):
    permission_profile: StatusProfile = "minimal"


class CreateFriendBody(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)
    avatar_url: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class UpdateFriendBody(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    avatar_url: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class LinkChannelBody(BaseModel):
    channel: FriendChannelKind
    handle: str = Field(..., min_length=1, max_length=200)
    is_primary: bool = False


class PermissionBody(BaseModel):
    profile: StatusProfile
    custom_whitelist: list[str] | None = None


class SendMessageBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)


class MessageDTO(BaseModel):
    direction: Literal["inbound", "outbound"]
    text: str
    timestamp_ns: int
    channel: FriendChannelKind | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _to_friend_dto(reg: FriendRegistry, friend: Friend) -> FriendDTO:
    channels = await reg.channels_for_friend(friend.id)
    return FriendDTO(
        id=str(friend.id),
        display_name=friend.display_name,
        avatar_url=friend.avatar_url,
        note=friend.note,
        created_at_ns=friend.created_at_ns,
        channels=[
            ChannelLinkDTO(
                channel=c.channel,
                handle=c.handle,
                is_primary=c.is_primary,
                linked_at_ns=c.linked_at_ns,
            )
            for c in channels
        ],
    )


def _primary_channel(channels: list[FriendChannel]) -> FriendChannel | None:
    if not channels:
        return None
    primary = [c for c in channels if c.is_primary]
    return primary[0] if primary else channels[0]


# ---------------------------------------------------------------------------
# Listing + CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[FriendDTO])
async def list_friends(request: Request) -> list[FriendDTO]:
    reg = _require_registry(request)
    friends = await reg.list_friends()
    return [await _to_friend_dto(reg, f) for f in friends]


@router.post("", response_model=FriendDetailDTO, status_code=201)
async def create_friend(body: CreateFriendBody, request: Request) -> FriendDetailDTO:
    reg = _require_registry(request)
    friend = Friend(
        display_name=body.display_name,
        avatar_url=body.avatar_url,
        note=body.note,
    )
    await reg.add_friend(friend)
    perm = await reg.get_status_permission(friend.id)
    return FriendDetailDTO(
        id=str(friend.id),
        display_name=friend.display_name,
        avatar_url=friend.avatar_url,
        note=friend.note,
        created_at_ns=friend.created_at_ns,
        channels=[],
        permission_profile=perm.profile,
    )


@router.get("/{friend_id}", response_model=FriendDetailDTO)
async def get_friend(friend_id: str, request: Request) -> FriendDetailDTO:
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    try:
        friend = await reg.get_friend(fid)
    except FriendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    base = await _to_friend_dto(reg, friend)
    perm = await reg.get_status_permission(fid)
    return FriendDetailDTO(**base.model_dump(), permission_profile=perm.profile)


@router.patch("/{friend_id}", response_model=FriendDetailDTO)
async def update_friend(
    friend_id: str, body: UpdateFriendBody, request: Request
) -> FriendDetailDTO:
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    try:
        existing = await reg.get_friend(fid)
    except FriendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    new_display = body.display_name if body.display_name is not None else existing.display_name
    new_avatar = body.avatar_url if body.avatar_url is not None else existing.avatar_url
    new_note = body.note if body.note is not None else existing.note

    conn = reg._require_conn()  # noqa: SLF001
    await conn.execute(
        "UPDATE friends SET display_name = ?, avatar_url = ?, note = ? WHERE id = ?",
        (new_display, new_avatar, new_note, str(fid)),
    )
    return await get_friend(friend_id, request)


@router.delete("/{friend_id}", status_code=204)
async def delete_friend(friend_id: str, request: Request) -> None:
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    try:
        await reg.delete_friend(fid)
    except FriendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@router.post("/{friend_id}/channels", response_model=FriendDetailDTO, status_code=201)
async def link_channel(
    friend_id: str, body: LinkChannelBody, request: Request
) -> FriendDetailDTO:
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    try:
        await reg.link_channel(
            FriendChannel(
                friend_id=fid,
                channel=body.channel,
                handle=body.handle,
                is_primary=body.is_primary,
            )
        )
    except FriendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await get_friend(friend_id, request)


@router.delete(
    "/{friend_id}/channels/{channel}/{handle}",
    status_code=204,
)
async def unlink_channel(
    friend_id: str, channel: FriendChannelKind, handle: str, request: Request
) -> None:
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    await reg.unlink_channel(fid, channel, handle)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@router.get("/{friend_id}/permission")
async def get_permission(friend_id: str, request: Request) -> dict[str, Any]:
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    perm = await reg.get_status_permission(fid)
    return {
        "friend_id": str(perm.friend_id),
        "profile": perm.profile,
        "custom_whitelist": perm.custom_whitelist,
        "updated_at_ns": perm.updated_at_ns,
    }


@router.patch("/{friend_id}/permission")
async def update_permission(
    friend_id: str, body: PermissionBody, request: Request
) -> dict[str, Any]:
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    try:
        await reg.get_friend(fid)
    except FriendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    perm = await reg.set_status_permission(
        fid, profile=body.profile, custom_whitelist=body.custom_whitelist
    )
    return {
        "friend_id": str(perm.friend_id),
        "profile": perm.profile,
        "custom_whitelist": perm.custom_whitelist,
        "updated_at_ns": perm.updated_at_ns,
    }


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@router.get("/{friend_id}/messages", response_model=list[MessageDTO])
async def list_messages(friend_id: str, request: Request) -> list[MessageDTO]:
    """F3: liest persistierte Direct-Messages aus ``direct_messages``.

    Telegram-Inbound aus dem Live-Bus landet in F4 hier; aktuell sind nur
    Outbound-Echoes (von ``send_message``) sichtbar.
    """
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    msgs = await reg.messages.list_for_friend(fid)
    return [
        MessageDTO(
            direction=m.direction,
            text=m.text,
            timestamp_ns=m.created_at_ns,
            channel=m.channel,
        )
        for m in msgs
    ]


@router.post("/{friend_id}/messages", response_model=MessageDTO, status_code=201)
async def send_message(
    friend_id: str, body: SendMessageBody, request: Request
) -> MessageDTO:
    """Outbound via primary Channel.

    F3: persistiert jeden Outbound-Versand in ``direct_messages``, damit
    der History-View ihn anzeigt — sowohl fuer Telegram (zusaetzlich zum
    Telegram-API-Send) als auch fuer ``jarvis_pubkey`` (echte
    Federation-Auslieferung kommt in F5; bis dahin echo-store).
    """
    reg = _require_registry(request)
    fid = _parse_friend_id(friend_id)
    try:
        await reg.get_friend(fid)
    except FriendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    channels = await reg.channels_for_friend(fid)
    primary = _primary_channel(channels)
    if primary is None:
        raise HTTPException(
            status_code=400,
            detail="Friend hat keinen verknuepften Channel — Outbound nicht moeglich.",
        )

    if primary.channel == "telegram":
        manager = _optional_channel_manager(request)
        if manager is None or "telegram" not in manager.started():
            raise HTTPException(
                status_code=503,
                detail="TelegramChannel nicht verfuegbar (nicht gestartet).",
            )
        telegram = manager.get("telegram")
        from uuid import uuid4

        msg = ChannelMessage(
            session_id=uuid4(),
            kind="text",
            content=body.text,
            metadata={"telegram_chat_id": int(primary.handle)},
        )
        await telegram.send_message(msg)
        # Outbound-Echo persistieren, damit History-View die Message zeigt.
        stored = await reg.messages.add(
            DirectMessage(
                friend_id=fid,
                direction="outbound",
                text=body.text,
                channel="telegram",
                delivered=True,
            )
        )
        return MessageDTO(
            direction="outbound",
            text=stored.text,
            timestamp_ns=stored.created_at_ns,
            channel="telegram",
        )

    if primary.channel == "jarvis_pubkey":
        # F3: lokal-only Persistenz. Federation-Auslieferung kommt mit F5
        # als separater Adapter, der denselben DirectMessageStore nutzt.
        stored = await reg.messages.add(
            DirectMessage(
                friend_id=fid,
                direction="outbound",
                text=body.text,
                channel="jarvis_pubkey",
                delivered=True,
            )
        )
        return MessageDTO(
            direction="outbound",
            text=stored.text,
            timestamp_ns=stored.created_at_ns,
            channel="jarvis_pubkey",
        )

    raise HTTPException(
        status_code=400, detail=f"Channel '{primary.channel}' nicht unterstuetzt"
    )
