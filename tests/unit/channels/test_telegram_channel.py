# === F-FRIENDS [F1] · feature/friends-section · alex-2026-04-30 ===
"""Unit tests for :class:`jarvis.channels.telegram.TelegramChannel`.

Strategy: no real ``python-telegram-bot`` lib needed in the test path —
we test with mock update/bot objects and manual instantiation.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from jarvis.channels.manager import ChannelContext
from jarvis.channels.telegram import InflightMap, TelegramChannel
from jarvis.core.bus import EventBus
from jarvis.core.config import TelegramConfig
from jarvis.core.events import ResponseGenerated
from jarvis.friends.models import Friend, FriendChannel
from jarvis.friends.registry import FriendRegistry


def _make_update(
    *,
    user_id: int = 100,
    chat_id: int | None = None,
    chat_type: str = "private",
    text: str = "Hallo",
    username: str | None = "alice",
    full_name: str | None = "Alice Tester",
) -> Any:
    if chat_id is None:
        chat_id = user_id
    user = SimpleNamespace(id=user_id, username=username, full_name=full_name)
    chat = SimpleNamespace(id=chat_id, type=chat_type)
    message = SimpleNamespace(text=text, chat=chat, from_user=user)
    return SimpleNamespace(message=message)


def _make_channel(
    cfg: TelegramConfig,
    *,
    bot_username: str = "jarvis_test_bot",
    registry: FriendRegistry | None = None,
) -> TelegramChannel:
    bus = EventBus()
    ch = TelegramChannel(bus=bus, config=cfg, friend_registry=registry)
    ch._bot_username = bot_username  # noqa: SLF001
    fake_bot = SimpleNamespace(send_message=AsyncMock())
    ch._app = SimpleNamespace(bot=fake_bot)  # noqa: SLF001
    return ch


@pytest_asyncio.fixture
async def registry() -> FriendRegistry:
    reg = FriendRegistry(":memory:")
    await reg.open()
    try:
        yield reg
    finally:
        await reg.close()


# InflightMap


def test_inflightmap_set_and_get() -> None:
    m = InflightMap(ttl_s=10.0)
    tid = uuid4()
    m.set(tid, 42)
    assert m.get(tid) == 42


def test_inflightmap_get_unknown_returns_none() -> None:
    m = InflightMap(ttl_s=10.0)
    assert m.get(uuid4()) is None


def test_inflightmap_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    m = InflightMap(ttl_s=1.0)
    tid = uuid4()
    fake_now = [time.time_ns()]

    def _fake_time_ns() -> int:
        return fake_now[0]

    monkeypatch.setattr("jarvis.channels.telegram.time.time_ns", _fake_time_ns)
    m.set(tid, 99)
    assert m.get(tid) == 99
    fake_now[0] += 2_000_000_000
    assert m.get(tid) is None


# _is_allowed


def test_is_allowed_private_user_in_allowlist() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    upd = _make_update(user_id=100, chat_type="private")
    assert ch._is_allowed(upd) is True  # noqa: SLF001


def test_is_allowed_private_user_not_in_allowlist() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    upd = _make_update(user_id=200, chat_type="private")
    assert ch._is_allowed(upd) is False  # noqa: SLF001


def test_is_allowed_group_policy_disabled() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100], group_policy="disabled")
    ch = _make_channel(cfg)
    upd = _make_update(user_id=100, chat_id=-999, chat_type="group", text="hi")
    assert ch._is_allowed(upd) is False  # noqa: SLF001


def test_is_allowed_group_policy_allowlist_blocks_unknown_chat() -> None:
    cfg = TelegramConfig(
        enabled=True,
        allowed_user_ids=[100],
        group_policy="allowlist",
        allowed_chat_ids=[],
        require_mention=False,
    )
    ch = _make_channel(cfg)
    upd = _make_update(user_id=100, chat_id=-999, chat_type="group", text="hi")
    assert ch._is_allowed(upd) is False  # noqa: SLF001


def test_is_allowed_group_policy_allowlist_passes_known_chat() -> None:
    cfg = TelegramConfig(
        enabled=True,
        allowed_user_ids=[100],
        group_policy="allowlist",
        allowed_chat_ids=[-999],
        require_mention=False,
    )
    ch = _make_channel(cfg)
    upd = _make_update(user_id=100, chat_id=-999, chat_type="group", text="hi")
    assert ch._is_allowed(upd) is True  # noqa: SLF001


def test_is_allowed_require_mention_drops_no_mention() -> None:
    cfg = TelegramConfig(
        enabled=True,
        allowed_user_ids=[100],
        group_policy="allowlist",
        allowed_chat_ids=[-999],
        require_mention=True,
    )
    ch = _make_channel(cfg, bot_username="jarvis_test_bot")
    upd = _make_update(user_id=100, chat_id=-999, chat_type="group", text="hallo")
    assert ch._is_allowed(upd) is False  # noqa: SLF001


def test_is_allowed_require_mention_passes_with_mention() -> None:
    cfg = TelegramConfig(
        enabled=True,
        allowed_user_ids=[100],
        group_policy="allowlist",
        allowed_chat_ids=[-999],
        require_mention=True,
    )
    ch = _make_channel(cfg, bot_username="jarvis_test_bot")
    upd = _make_update(
        user_id=100,
        chat_id=-999,
        chat_type="group",
        text="@jarvis_test_bot was geht?",
    )
    assert ch._is_allowed(upd) is True  # noqa: SLF001


def test_is_allowed_anonymous_message_dropped() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    chat = SimpleNamespace(id=42, type="private")
    upd = SimpleNamespace(message=SimpleNamespace(text="x", chat=chat, from_user=None))
    assert ch._is_allowed(upd) is False  # noqa: SLF001


# _on_telegram_msg


@pytest.mark.asyncio
async def test_on_msg_drops_when_not_allowed() -> None:
    cfg = TelegramConfig(
        enabled=True,
        allowed_user_ids=[],
        pair_on_first_private_message=False,
    )
    ch = _make_channel(cfg)
    upd = _make_update(user_id=999)
    await ch._on_telegram_msg(upd, _ctx=None)  # noqa: SLF001
    assert ch._inbox.qsize() == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_msg_pairs_first_private_user_and_inserts_inbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paired: list[int] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.add_telegram_allowed_user_id",
        lambda user_id: paired.append(user_id),
    )
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[])
    ch = _make_channel(cfg)
    upd = _make_update(user_id=999, chat_id=999, text="Hello Jarvis")

    await ch._on_telegram_msg(upd, _ctx=None)  # noqa: SLF001

    assert paired == [999]
    assert ch._cfg.allowed_user_ids == [999]  # noqa: SLF001
    msg = await ch._inbox.get()  # noqa: SLF001
    assert msg.content == "Hello Jarvis"
    assert msg.metadata["telegram_chat_id"] == 999


@pytest.mark.asyncio
async def test_start_command_replies_without_entering_inbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.core.config_writer.add_telegram_allowed_user_id",
        lambda user_id: None,
    )
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[])
    ch = _make_channel(cfg)
    upd = _make_update(user_id=999, chat_id=999, text="/start")

    await ch._on_telegram_msg(upd, _ctx=None)  # noqa: SLF001

    assert ch._inbox.qsize() == 0  # noqa: SLF001
    ch._app.bot.send_message.assert_awaited_once()  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_msg_inserts_inbox_and_inflight() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    upd = _make_update(user_id=100, chat_id=100, text="Hallo Jarvis")
    await ch._on_telegram_msg(upd, _ctx=None)  # noqa: SLF001
    assert ch._inbox.qsize() == 1  # noqa: SLF001
    msg = await ch._inbox.get()  # noqa: SLF001
    assert msg.content == "Hallo Jarvis"
    assert msg.metadata["telegram_chat_id"] == 100
    assert ch._inflight.get(msg.trace_id) == 100  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_msg_handler_swallows_errors() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    bad_upd = SimpleNamespace()
    await ch._on_telegram_msg(bad_upd, _ctx=None)  # noqa: SLF001
    assert ch._inbox.qsize() == 0  # noqa: SLF001


# _resolve_friend


@pytest.mark.asyncio
async def test_resolve_friend_returns_existing(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(friend_id=friend.id, channel="telegram", handle="42")
    )
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg, registry=registry)
    user = SimpleNamespace(id=100, username="daniel", full_name="Daniel D.")
    resolved = await ch._resolve_friend(42, user)  # noqa: SLF001
    assert resolved is not None
    assert resolved.id == friend.id


@pytest.mark.asyncio
async def test_resolve_friend_auto_register_off(registry: FriendRegistry) -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100], auto_register_friends=False)
    ch = _make_channel(cfg, registry=registry)
    user = SimpleNamespace(id=100, username="alice", full_name="Alice")
    resolved = await ch._resolve_friend(123, user)  # noqa: SLF001
    assert resolved is None
    assert (await registry.list_friends()) == []


@pytest.mark.asyncio
async def test_resolve_friend_auto_register_on(registry: FriendRegistry) -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100], auto_register_friends=True)
    ch = _make_channel(cfg, registry=registry)
    user = SimpleNamespace(id=100, username="alice", full_name="Alice Tester")
    resolved = await ch._resolve_friend(123, user)  # noqa: SLF001
    assert resolved is not None
    assert resolved.display_name == "Alice Tester"
    found = await registry.find_friend_by_channel("telegram", "123")
    assert found is not None
    assert found.id == resolved.id


# _send_text + send_message


@pytest.mark.asyncio
async def test_send_text_calls_bot_send_message() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._send_text(42, "Hallo Welt", language="de")  # noqa: SLF001
    ch._app.bot.send_message.assert_awaited_once()  # noqa: SLF001
    kwargs = ch._app.bot.send_message.call_args.kwargs  # noqa: SLF001
    assert kwargs["chat_id"] == 42
    assert "Hallo Welt" in kwargs["text"]


@pytest.mark.asyncio
async def test_send_text_drops_empty_after_scrub() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._send_text(42, "", language="de")  # noqa: SLF001
    ch._app.bot.send_message.assert_not_awaited()  # noqa: SLF001


@pytest.mark.asyncio
async def test_send_text_aborts_when_app_not_started() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    ch._app = None  # noqa: SLF001
    await ch._send_text(42, "x", language="de")  # noqa: SLF001


@pytest.mark.asyncio
async def test_send_message_drops_without_chat_id() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    from jarvis.channels.base import ChannelMessage

    msg = ChannelMessage(session_id=uuid4(), kind="text", content="x", metadata={})
    await ch.send_message(msg)
    ch._app.bot.send_message.assert_not_awaited()  # noqa: SLF001


# _on_bus_event


@pytest.mark.asyncio
async def test_on_bus_event_ignores_non_response_generated() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)

    from jarvis.core.events import SystemStateChanged

    evt = SystemStateChanged(new_state="IDLE")
    await ch._on_bus_event(evt)  # noqa: SLF001
    ch._app.bot.send_message.assert_not_awaited()  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_bus_event_ignores_unknown_trace() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    evt = ResponseGenerated(text="Hi", language="de")
    await ch._on_bus_event(evt)  # noqa: SLF001
    ch._app.bot.send_message.assert_not_awaited()  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_bus_event_routes_known_trace() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    evt = ResponseGenerated(text="Antwort fuer dich", language="de")  # i18n-allow: simulated German assistant output under a "de" language key
    ch._inflight.set(evt.trace_id, 42)  # noqa: SLF001
    await ch._on_bus_event(evt)  # noqa: SLF001
    ch._app.bot.send_message.assert_awaited_once()  # noqa: SLF001
    kwargs = ch._app.bot.send_message.call_args.kwargs  # noqa: SLF001
    assert kwargs["chat_id"] == 42
    assert "Antwort" in kwargs["text"]


# from_context


def test_from_context_uses_disabled_default_when_config_missing() -> None:
    ctx = ChannelContext(bus=EventBus(), friend_registry=None, config={})
    ch = TelegramChannel.from_context(ctx)
    assert ch._cfg.enabled is False  # noqa: SLF001


def test_from_context_picks_up_telegram_config() -> None:
    cfg = TelegramConfig(enabled=True, allowed_user_ids=[100])
    ctx = ChannelContext(
        bus=EventBus(), friend_registry=None, config={"telegram_config": cfg}
    )
    ch = TelegramChannel.from_context(ctx)
    assert ch._cfg.enabled is True  # noqa: SLF001
    assert ch._cfg.allowed_user_ids == [100]  # noqa: SLF001


# Lifecycle Edge-Cases


@pytest.mark.asyncio
async def test_start_disabled_attaches_noop_bus_observer() -> None:
    cfg = TelegramConfig(enabled=False)
    bus = EventBus()
    ch = TelegramChannel(bus=bus, config=cfg)
    await ch.start()
    assert ch._started is True  # noqa: SLF001
    assert len(bus._wildcard_subscribers) == 1  # noqa: SLF001
    assert ch._app is None  # noqa: SLF001
    await ch.stop()
    assert len(bus._wildcard_subscribers) == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_stop_idempotent_when_not_started() -> None:
    cfg = TelegramConfig(enabled=False)
    ch = TelegramChannel(bus=EventBus(), config=cfg)
    await ch.stop()
