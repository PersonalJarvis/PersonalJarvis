"""Unit tests for :class:`jarvis.channels.discord.DiscordChannel`.

Strategy mirrors ``test_telegram_channel.py``: no real ``discord.py`` library is
needed on the test path. Discord ``Message``/``Client`` objects are faked with
``SimpleNamespace`` + ``AsyncMock`` and the channel is instantiated manually.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from jarvis.channels.discord import DiscordChannel, InflightMap
from jarvis.channels.manager import ChannelContext
from jarvis.core.bus import EventBus
from jarvis.core.config import DiscordConfig
from jarvis.core.events import ResponseGenerated

BOT_ID = 999_000

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_message(
    *,
    user_id: int = 100,
    channel_id: int = 555,
    text: str = "Hallo",
    username: str = "alice",
    is_bot: bool = False,
    guild_id: int | None = None,
    mention_ids: list[int] | None = None,
) -> Any:
    author = SimpleNamespace(id=user_id, name=username, bot=is_bot)
    channel = SimpleNamespace(id=channel_id)
    guild = SimpleNamespace(id=guild_id) if guild_id is not None else None
    mentions = [SimpleNamespace(id=mid) for mid in (mention_ids or [])]
    return SimpleNamespace(
        content=text,
        channel=channel,
        author=author,
        guild=guild,
        mentions=mentions,
    )


def _make_channel(
    cfg: DiscordConfig,
    *,
    bot_user_id: int = BOT_ID,
) -> DiscordChannel:
    ch = DiscordChannel(bus=EventBus(), config=cfg)
    ch._bot_user_id = bot_user_id  # noqa: SLF001
    fake_channel = SimpleNamespace(send=AsyncMock())
    fake_client = SimpleNamespace(
        user=SimpleNamespace(id=bot_user_id),
        get_channel=Mock(return_value=fake_channel),
    )
    ch._client = fake_client  # noqa: SLF001
    ch._sent_channel = fake_channel  # type: ignore[attr-defined]  # test handle
    return ch


# ---------------------------------------------------------------------------
# InflightMap
# ---------------------------------------------------------------------------


def test_inflightmap_set_and_get() -> None:
    m = InflightMap(ttl_s=10.0)
    tid = uuid4()
    m.set(tid, 42)
    assert m.get(tid) == 42


def test_inflightmap_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    m = InflightMap(ttl_s=1.0)
    tid = uuid4()
    fake_now = [time.time_ns()]
    monkeypatch.setattr(
        "jarvis.channels.discord.time.time_ns", lambda: fake_now[0]
    )
    m.set(tid, 99)
    assert m.get(tid) == 99
    fake_now[0] += 2_000_000_000
    assert m.get(tid) is None


# ---------------------------------------------------------------------------
# _is_allowed
# ---------------------------------------------------------------------------


def test_is_allowed_dm_user_in_allowlist() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    assert ch._is_allowed(_make_message(user_id=100)) is True  # noqa: SLF001


def test_is_allowed_dm_user_not_in_allowlist() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    assert ch._is_allowed(_make_message(user_id=200)) is False  # noqa: SLF001


def test_is_allowed_guild_policy_disabled() -> None:
    cfg = DiscordConfig(enabled=True, guild_policy="disabled")
    ch = _make_channel(cfg)
    msg = _make_message(user_id=100, guild_id=1, channel_id=-7)
    assert ch._is_allowed(msg) is False  # noqa: SLF001


def test_is_allowed_guild_allowlist_blocks_unknown_channel() -> None:
    cfg = DiscordConfig(
        enabled=True,
        guild_policy="allowlist",
        allowed_channel_ids=[],
        require_mention=False,
    )
    ch = _make_channel(cfg)
    msg = _make_message(user_id=100, guild_id=1, channel_id=-7)
    assert ch._is_allowed(msg) is False  # noqa: SLF001


def test_is_allowed_guild_allowlist_passes_known_channel() -> None:
    cfg = DiscordConfig(
        enabled=True,
        guild_policy="allowlist",
        allowed_channel_ids=[-7],
        require_mention=False,
    )
    ch = _make_channel(cfg)
    msg = _make_message(user_id=100, guild_id=1, channel_id=-7)
    assert ch._is_allowed(msg) is True  # noqa: SLF001


def test_is_allowed_require_mention_drops_without_mention() -> None:
    cfg = DiscordConfig(
        enabled=True,
        guild_policy="allowlist",
        allowed_channel_ids=[-7],
        require_mention=True,
    )
    ch = _make_channel(cfg)
    msg = _make_message(user_id=100, guild_id=1, channel_id=-7, mention_ids=[])
    assert ch._is_allowed(msg) is False  # noqa: SLF001


def test_is_allowed_require_mention_passes_with_mention() -> None:
    cfg = DiscordConfig(
        enabled=True,
        guild_policy="allowlist",
        allowed_channel_ids=[-7],
        require_mention=True,
    )
    ch = _make_channel(cfg)
    msg = _make_message(
        user_id=100, guild_id=1, channel_id=-7, mention_ids=[BOT_ID]
    )
    assert ch._is_allowed(msg) is True  # noqa: SLF001


# ---------------------------------------------------------------------------
# _pair_first_dm
# ---------------------------------------------------------------------------


def test_pair_first_dm_claims_empty_allowlist() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[], pair_on_first_dm=True)
    ch = _make_channel(cfg)
    assert ch._pair_first_dm(_make_message(user_id=777)) is True  # noqa: SLF001
    assert ch._cfg.allowed_user_ids == [777]  # noqa: SLF001


def test_pair_first_dm_off_when_allowlist_nonempty() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[1], pair_on_first_dm=True)
    ch = _make_channel(cfg)
    assert ch._pair_first_dm(_make_message(user_id=777)) is False  # noqa: SLF001


def test_pair_first_dm_off_for_guild_message() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[], pair_on_first_dm=True)
    ch = _make_channel(cfg)
    msg = _make_message(user_id=777, guild_id=1, channel_id=-7)
    assert ch._pair_first_dm(msg) is False  # noqa: SLF001


# ---------------------------------------------------------------------------
# _on_discord_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_drops_when_not_allowed() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[], pair_on_first_dm=False)
    ch = _make_channel(cfg)
    await ch._on_discord_message(_make_message(user_id=999))  # noqa: SLF001
    assert ch._inbox.qsize() == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_ignores_self() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[BOT_ID])
    ch = _make_channel(cfg)
    await ch._on_discord_message(_make_message(user_id=BOT_ID))  # noqa: SLF001
    assert ch._inbox.qsize() == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_ignores_other_bots() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._on_discord_message(  # noqa: SLF001
        _make_message(user_id=100, is_bot=True)
    )
    assert ch._inbox.qsize() == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_inserts_inbox_and_inflight() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._on_discord_message(  # noqa: SLF001
        _make_message(user_id=100, channel_id=555, text="Hallo Jarvis")
    )
    assert ch._inbox.qsize() == 1  # noqa: SLF001
    msg = await ch._inbox.get()  # noqa: SLF001
    assert msg.content == "Hallo Jarvis"
    assert msg.metadata["discord_channel_id"] == 555
    # Stable thread id key so the chat-bridge groups one Discord channel into
    # one conversation thread.
    assert msg.metadata["channel_session_id"] == 555
    assert ch._inflight.get(msg.trace_id) == 555  # noqa: SLF001


@pytest.mark.asyncio
async def test_on_message_handler_swallows_errors() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._on_discord_message(SimpleNamespace())  # noqa: SLF001
    assert ch._inbox.qsize() == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# _send_text + send_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_text_calls_channel_send() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._send_text(555, "Hallo Welt", language="de")  # noqa: SLF001
    ch._sent_channel.send.assert_awaited_once()  # type: ignore[attr-defined]
    args, _ = ch._sent_channel.send.call_args  # type: ignore[attr-defined]
    assert "Hallo Welt" in args[0]


@pytest.mark.asyncio
async def test_send_text_drops_empty_after_scrub() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._send_text(555, "", language="de")  # noqa: SLF001
    ch._sent_channel.send.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_send_text_aborts_when_client_none() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    ch._client = None  # noqa: SLF001
    await ch._send_text(555, "x", language="de")  # noqa: SLF001


@pytest.mark.asyncio
async def test_send_message_drops_without_channel_id() -> None:
    from jarvis.channels.base import ChannelMessage

    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    msg = ChannelMessage(session_id=uuid4(), kind="text", content="x", metadata={})
    await ch.send_message(msg)
    ch._sent_channel.send.assert_not_awaited()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _on_bus_event (trace_id routing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_bus_event_ignores_non_response_generated() -> None:
    from jarvis.core.events import SystemStateChanged

    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._on_bus_event(SystemStateChanged(new_state="IDLE"))  # noqa: SLF001
    ch._sent_channel.send.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_on_bus_event_ignores_unknown_trace() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    await ch._on_bus_event(ResponseGenerated(text="Hi", language="de"))  # noqa: SLF001
    ch._sent_channel.send.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_on_bus_event_routes_known_trace() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ch = _make_channel(cfg)
    evt = ResponseGenerated(text="Antwort fuer dich", language="de")
    ch._inflight.set(evt.trace_id, 555)  # noqa: SLF001
    await ch._on_bus_event(evt)  # noqa: SLF001
    ch._sent_channel.send.assert_awaited_once()  # type: ignore[attr-defined]
    args, _ = ch._sent_channel.send.call_args  # type: ignore[attr-defined]
    assert "Antwort" in args[0]


# ---------------------------------------------------------------------------
# from_context
# ---------------------------------------------------------------------------


def test_from_context_uses_disabled_default_when_config_missing() -> None:
    ctx = ChannelContext(bus=EventBus(), friend_registry=None, config={})
    ch = DiscordChannel.from_context(ctx)
    assert ch._cfg.enabled is False  # noqa: SLF001


def test_from_context_picks_up_discord_config() -> None:
    cfg = DiscordConfig(enabled=True, allowed_user_ids=[100])
    ctx = ChannelContext(
        bus=EventBus(), friend_registry=None, config={"discord_config": cfg}
    )
    ch = DiscordChannel.from_context(ctx)
    assert ch._cfg.enabled is True  # noqa: SLF001
    assert ch._cfg.allowed_user_ids == [100]  # noqa: SLF001


# ---------------------------------------------------------------------------
# Lifecycle edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_skips_when_disabled() -> None:
    cfg = DiscordConfig(enabled=False)
    bus = EventBus()
    ch = DiscordChannel(bus=bus, config=cfg)
    before = len(bus._wildcard_subscribers)  # noqa: SLF001

    await ch.start()

    assert ch._started is True  # noqa: SLF001
    assert ch._client is None  # noqa: SLF001
    assert len(bus._wildcard_subscribers) == before + 1  # noqa: SLF001

    await ch.stop()
    assert len(bus._wildcard_subscribers) == before  # noqa: SLF001


@pytest.mark.asyncio
async def test_stop_idempotent_when_not_started() -> None:
    cfg = DiscordConfig(enabled=False)
    ch = DiscordChannel(bus=EventBus(), config=cfg)
    await ch.stop()
