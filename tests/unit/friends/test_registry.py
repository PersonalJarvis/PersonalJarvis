# === F-FRIENDS [F0] · feature/friends-section · ruben-2026-04-30 ===
"""Unit tests for :class:`jarvis.friends.registry.FriendRegistry`."""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from jarvis.friends.models import Friend, FriendChannel
from jarvis.friends.registry import (
    FriendNotFoundError,
    FriendRegistry,
    FriendRegistryError,
)


@pytest_asyncio.fixture
async def registry() -> FriendRegistry:
    reg = FriendRegistry(":memory:")
    await reg.open()
    try:
        yield reg
    finally:
        await reg.close()


@pytest.mark.asyncio
async def test_open_close_idempotent() -> None:
    reg = FriendRegistry(":memory:")
    await reg.open()
    await reg.open()
    await reg.close()
    await reg.close()


@pytest.mark.asyncio
async def test_require_conn_raises_before_open() -> None:
    reg = FriendRegistry(":memory:")
    with pytest.raises(FriendRegistryError, match="nicht geoeffnet"):  # i18n-allow: matches the real (German) exception message string
        await reg.list_friends()


@pytest.mark.asyncio
async def test_add_and_get_friend(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel", note="Schule")
    await registry.add_friend(friend)
    fetched = await registry.get_friend(friend.id)
    assert fetched.id == friend.id
    assert fetched.display_name == "Daniel"
    assert fetched.note == "Schule"


@pytest.mark.asyncio
async def test_get_friend_not_found(registry: FriendRegistry) -> None:
    with pytest.raises(FriendNotFoundError):
        await registry.get_friend(uuid4())


@pytest.mark.asyncio
async def test_list_friends_alphabetical(registry: FriendRegistry) -> None:
    await registry.add_friend(Friend(display_name="Felix"))
    await registry.add_friend(Friend(display_name="Anna"))
    await registry.add_friend(Friend(display_name="bjoern"))
    friends = await registry.list_friends()
    assert [f.display_name for f in friends] == ["Anna", "bjoern", "Felix"]


@pytest.mark.asyncio
async def test_default_permission_created_on_add(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Sven")
    await registry.add_friend(friend)
    perm = await registry.get_status_permission(friend.id)
    assert perm.profile == "minimal"
    assert perm.friend_id == friend.id


@pytest.mark.asyncio
async def test_delete_friend_cascades(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Mama")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(friend_id=friend.id, channel="telegram", handle="111")
    )
    await registry.delete_friend(friend.id)
    with pytest.raises(FriendNotFoundError):
        await registry.get_friend(friend.id)
    found = await registry.find_friend_by_channel("telegram", "111")
    assert found is None


@pytest.mark.asyncio
async def test_delete_unknown_friend_raises(registry: FriendRegistry) -> None:
    with pytest.raises(FriendNotFoundError):
        await registry.delete_friend(uuid4())


@pytest.mark.asyncio
async def test_link_channel_and_lookup(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(friend_id=friend.id, channel="telegram", handle="42")
    )
    found = await registry.find_friend_by_channel("telegram", "42")
    assert found is not None
    assert found.id == friend.id


@pytest.mark.asyncio
async def test_multi_channel_friend(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(friend_id=friend.id, channel="telegram", handle="42")
    )
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id, channel="jarvis_pubkey", handle="abc123def", is_primary=True
        )
    )
    channels = await registry.channels_for_friend(friend.id)
    assert len(channels) == 2
    assert channels[0].channel == "jarvis_pubkey"
    assert channels[0].is_primary is True
    assert channels[1].is_primary is False


@pytest.mark.asyncio
async def test_link_channel_demotes_other_primaries(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id, channel="telegram", handle="42", is_primary=True
        )
    )
    await registry.link_channel(
        FriendChannel(
            friend_id=friend.id,
            channel="jarvis_pubkey",
            handle="abc",
            is_primary=True,
        )
    )
    channels = await registry.channels_for_friend(friend.id)
    primaries = [c for c in channels if c.is_primary]
    assert len(primaries) == 1
    assert primaries[0].channel == "jarvis_pubkey"


@pytest.mark.asyncio
async def test_link_channel_unknown_friend_raises(registry: FriendRegistry) -> None:
    with pytest.raises(FriendNotFoundError):
        await registry.link_channel(
            FriendChannel(friend_id=uuid4(), channel="telegram", handle="x")
        )


@pytest.mark.asyncio
async def test_unlink_channel(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.link_channel(
        FriendChannel(friend_id=friend.id, channel="telegram", handle="42")
    )
    await registry.unlink_channel(friend.id, "telegram", "42")
    found = await registry.find_friend_by_channel("telegram", "42")
    assert found is None


@pytest.mark.asyncio
async def test_find_by_channel_returns_none_when_unknown(
    registry: FriendRegistry,
) -> None:
    found = await registry.find_friend_by_channel("telegram", "ghost")
    assert found is None


@pytest.mark.asyncio
async def test_set_and_get_permission(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.set_status_permission(friend.id, "detailed")
    perm = await registry.get_status_permission(friend.id)
    assert perm.profile == "detailed"


@pytest.mark.asyncio
async def test_permission_default_minimal_when_no_record(
    registry: FriendRegistry,
) -> None:
    fake_id = uuid4()
    perm = await registry.get_status_permission(fake_id)
    assert perm.profile == "minimal"
    assert perm.custom_whitelist is None


@pytest.mark.asyncio
async def test_permission_custom_whitelist_roundtrip(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.set_status_permission(
        friend.id, "standard", custom_whitelist=["MissionStarted", "MissionCompleted"]
    )
    perm = await registry.get_status_permission(friend.id)
    assert perm.profile == "standard"
    assert perm.custom_whitelist == ["MissionStarted", "MissionCompleted"]


@pytest.mark.asyncio
async def test_permission_upsert_replaces(registry: FriendRegistry) -> None:
    friend = Friend(display_name="Daniel")
    await registry.add_friend(friend)
    await registry.set_status_permission(friend.id, "detailed")
    await registry.set_status_permission(friend.id, "minimal")
    perm = await registry.get_status_permission(friend.id)
    assert perm.profile == "minimal"
