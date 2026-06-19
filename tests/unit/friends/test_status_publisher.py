# === F-FRIENDS [F4] · feature/friends-section · ruben-2026-05-01 ===
"""Unit-Tests fuer :class:`jarvis.friends.status_publisher.StatusPublisher`.

Strategie: echte FriendRegistry (in-memory SQLite) + Fake-ChannelManager + Fake-Bus.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus
from jarvis.friends.models import Friend, FriendChannel
from jarvis.friends.registry import FriendRegistry
from jarvis.friends.schemas import StatusUpdate
from jarvis.friends.status_publisher import StatusPublisher


# ----------------------------------------------------------------------
# Fake-Events (Class-Names matchen echte Bus-Events)
# ----------------------------------------------------------------------


@dataclass
class VoiceSessionStarted:
    timestamp_ns: int = 1_000
    wake_keyword: str = "jarvis"


@dataclass
class MissionStarted:
    timestamp_ns: int = 3_000
    title: str = "Test-Mission"


@dataclass
class MissionCompleted:
    timestamp_ns: int = 4_000
    title: str = "Test-Mission"
    success: bool = True


@dataclass
class UtteranceCaptured:
    timestamp_ns: int = 5_000
    audio_ref: str = "leak-me-not"


# ----------------------------------------------------------------------
# Fake-ChannelManager + Fake-TelegramChannel
# ----------------------------------------------------------------------


class FakeTelegramChannel:
    """Captured all send_status_card calls fuer Assertion."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, StatusUpdate]] = []

    async def send_status_card(self, chat_id: int, update: StatusUpdate) -> None:
        self.sent.append((chat_id, update))


class FakeChannelManager:
    def __init__(self, telegram: FakeTelegramChannel | None = None) -> None:
        self._channels: dict[str, Any] = {}
        if telegram is not None:
            self._channels["telegram"] = telegram

    def get(self, name: str) -> Any:
        if name not in self._channels:
            raise KeyError(name)
        return self._channels[name]


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def registry() -> FriendRegistry:
    reg = FriendRegistry(":memory:")
    await reg.open()
    try:
        yield reg
    finally:
        await reg.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribes_unsubscribes(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """start() registriert Wildcard-Handler, stop() entfernt ihn."""
    pub = StatusPublisher(bus, registry, channel_manager=None)
    assert len(bus._wildcard_subscribers) == 0
    await pub.start()
    assert len(bus._wildcard_subscribers) == 1
    await pub.stop()
    assert len(bus._wildcard_subscribers) == 0


@pytest.mark.asyncio
async def test_double_start_idempotent(
    bus: EventBus, registry: FriendRegistry
) -> None:
    pub = StatusPublisher(bus, registry, channel_manager=None)
    await pub.start()
    await pub.start()
    assert len(bus._wildcard_subscribers) == 1
    await pub.stop()


@pytest.mark.asyncio
async def test_filtered_event_dispatched_to_telegram(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """Friend mit telegram + standard, MissionCompleted -> dispatched."""
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="telegram",
            handle="12345",
            is_primary=True,
        )
    )
    await registry.set_status_permission(friend.id, "standard")

    fake_tg = FakeTelegramChannel()
    cm = FakeChannelManager(fake_tg)
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        await bus.publish(MissionCompleted())
    finally:
        await pub.stop()

    assert len(fake_tg.sent) == 1
    chat_id, update = fake_tg.sent[0]
    assert chat_id == 12345
    assert update.event_type == "MissionCompleted"
    assert update.profile_used == "standard"
    assert update.fields == {"title": "Test-Mission", "success": True}


@pytest.mark.asyncio
async def test_blacklisted_event_not_dispatched(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """UtteranceCaptured wird NIE dispatched, egal welches Profile."""
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="telegram",
            handle="12345",
            is_primary=True,
        )
    )
    await registry.set_status_permission(friend.id, "detailed")

    fake_tg = FakeTelegramChannel()
    cm = FakeChannelManager(fake_tg)
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        await bus.publish(UtteranceCaptured())
    finally:
        await pub.stop()

    assert fake_tg.sent == []


@pytest.mark.asyncio
async def test_blacklisted_event_with_custom_whitelist_blocked(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """Custom-Whitelist kann Hard-Blacklist NICHT umgehen."""
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="telegram",
            handle="12345",
            is_primary=True,
        )
    )
    await registry.set_status_permission(
        friend.id, "minimal", custom_whitelist=["UtteranceCaptured"]
    )

    fake_tg = FakeTelegramChannel()
    cm = FakeChannelManager(fake_tg)
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        await bus.publish(UtteranceCaptured())
    finally:
        await pub.stop()

    assert fake_tg.sent == []


@pytest.mark.asyncio
async def test_per_friend_filtering(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """Zwei Friends mit unterschiedlichen Profilen — nur der mit
    passendem Profile bekommt das Event."""
    minimal_friend = Friend(display_name="Minimal-Mike")
    detailed_friend = Friend(display_name="Detail-Dora")
    await registry.add_friend(minimal_friend)
    await registry.add_friend(detailed_friend)

    await registry.link_channel(
        FriendChannel(
            friend_id=minimal_friend.id,
            channel="telegram",
            handle="111",
            is_primary=True,
        )
    )
    await registry.link_channel(
        FriendChannel(
            friend_id=detailed_friend.id,
            channel="telegram",
            handle="222",
            is_primary=True,
        )
    )
    await registry.set_status_permission(minimal_friend.id, "minimal")
    await registry.set_status_permission(detailed_friend.id, "detailed")

    fake_tg = FakeTelegramChannel()
    cm = FakeChannelManager(fake_tg)
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        # MissionStarted ist 'standard'+ — minimal blockiert, detailed durch
        await bus.publish(MissionStarted())
    finally:
        await pub.stop()

    assert len(fake_tg.sent) == 1
    chat_id, _update = fake_tg.sent[0]
    assert chat_id == 222  # nur detailed-Friend


@pytest.mark.asyncio
async def test_friend_without_channel_skipped(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """Friend ohne verknuepften Channel -> kein Crash, kein Send."""
    friend = Friend(display_name="Channelless")
    await registry.add_friend(friend)
    await registry.set_status_permission(friend.id, "detailed")

    fake_tg = FakeTelegramChannel()
    cm = FakeChannelManager(fake_tg)
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        await bus.publish(VoiceSessionStarted())
    finally:
        await pub.stop()

    assert fake_tg.sent == []


@pytest.mark.asyncio
async def test_invalid_telegram_handle_does_not_crash(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """Wenn telegram-handle nicht zu int parsen geht -> skip, kein Crash."""
    friend = Friend(display_name="BadHandle")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="telegram",
            handle="not-an-int",
            is_primary=True,
        )
    )
    await registry.set_status_permission(friend.id, "detailed")

    fake_tg = FakeTelegramChannel()
    cm = FakeChannelManager(fake_tg)
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        await bus.publish(VoiceSessionStarted())
    finally:
        await pub.stop()

    assert fake_tg.sent == []


@pytest.mark.asyncio
async def test_telegram_channel_unavailable_does_not_crash(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """ChannelManager liefert KeyError fuer 'telegram' -> skip, kein Crash."""
    friend = Friend(display_name="Foo")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="telegram",
            handle="12345",
            is_primary=True,
        )
    )
    await registry.set_status_permission(friend.id, "minimal")

    cm = FakeChannelManager(telegram=None)  # telegram NICHT registriert
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        await bus.publish(VoiceSessionStarted())
    finally:
        await pub.stop()
    # Erfolgreich ohne Exception


@pytest.mark.asyncio
async def test_channel_manager_none_does_not_crash(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """Ohne ChannelManager wird trotzdem nicht gecrasht."""
    friend = Friend(display_name="Foo")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="telegram",
            handle="12345",
            is_primary=True,
        )
    )
    await registry.set_status_permission(friend.id, "minimal")

    pub = StatusPublisher(bus, registry, channel_manager=None)
    await pub.start()
    try:
        await bus.publish(VoiceSessionStarted())
    finally:
        await pub.stop()


@pytest.mark.asyncio
async def test_jarvis_pubkey_channel_stub_only(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """jarvis_pubkey ist in F4 Stub — kein Send, kein Crash."""
    friend = Friend(display_name="Federation-Friend")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="jarvis_pubkey",
            handle="pubkey-abc",
            is_primary=True,
        )
    )
    await registry.set_status_permission(friend.id, "detailed")

    fake_tg = FakeTelegramChannel()
    cm = FakeChannelManager(fake_tg)
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        await bus.publish(VoiceSessionStarted())
    finally:
        await pub.stop()

    # Telegram darf NICHT gerufen werden (Friend hat nur jarvis_pubkey)
    assert fake_tg.sent == []


@pytest.mark.asyncio
async def test_primary_channel_chosen_when_multiple(
    bus: EventBus, registry: FriendRegistry
) -> None:
    """Wenn mehrere Channels verknuepft, gewinnt is_primary=True."""
    friend = Friend(display_name="MultiChannel")
    await registry.add_friend(friend)
    # Nicht-primary zuerst
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="telegram",
            handle="111",
            is_primary=False,
        )
    )
    # Primary danach
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="telegram",
            handle="999",
            is_primary=True,
        )
    )
    await registry.set_status_permission(friend.id, "minimal")

    fake_tg = FakeTelegramChannel()
    cm = FakeChannelManager(fake_tg)
    pub = StatusPublisher(bus, registry, channel_manager=cm)
    await pub.start()
    try:
        await bus.publish(VoiceSessionStarted())
    finally:
        await pub.stop()

    assert len(fake_tg.sent) == 1
    chat_id, _ = fake_tg.sent[0]
    assert chat_id == 999  # primary, nicht 111
