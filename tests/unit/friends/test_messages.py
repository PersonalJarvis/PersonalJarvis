# === F-FRIENDS [F3] · feature/friends-section · ruben-2026-05-01 ===
"""Unit tests for :class:`jarvis.friends.messages.DirectMessageStore`."""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from jarvis.friends.messages import DirectMessage, DirectMessageStore
from jarvis.friends.models import Friend
from jarvis.friends.registry import FriendRegistry


@pytest_asyncio.fixture
async def registry() -> FriendRegistry:
    reg = FriendRegistry(":memory:")
    await reg.open()
    try:
        yield reg
    finally:
        await reg.close()


@pytest_asyncio.fixture
async def friend(registry: FriendRegistry) -> Friend:
    f = Friend(display_name="Daniel")
    await registry.add_friend(f)
    return f


@pytest.mark.asyncio
async def test_messages_property_returns_store(registry: FriendRegistry) -> None:
    store = registry.messages
    assert isinstance(store, DirectMessageStore)


@pytest.mark.asyncio
async def test_list_for_friend_empty(
    registry: FriendRegistry, friend: Friend
) -> None:
    msgs = await registry.messages.list_for_friend(friend.id)
    assert msgs == []


@pytest.mark.asyncio
async def test_add_and_list(registry: FriendRegistry, friend: Friend) -> None:
    msg = DirectMessage(
        friend_id=friend.id,
        direction="outbound",
        text="Hallo",
        channel="telegram",
    )
    stored = await registry.messages.add(msg)
    assert stored.id == msg.id

    listed = await registry.messages.list_for_friend(friend.id)
    assert len(listed) == 1
    assert listed[0].id == msg.id
    assert listed[0].text == "Hallo"
    assert listed[0].direction == "outbound"
    assert listed[0].channel == "telegram"
    assert listed[0].delivered is True


@pytest.mark.asyncio
async def test_list_chronological_ascending(
    registry: FriendRegistry, friend: Friend
) -> None:
    """Aelteste zuerst, neueste zuletzt — History-Reihenfolge."""
    a = DirectMessage(
        friend_id=friend.id,
        direction="outbound",
        text="erste",
        channel="telegram",
        created_at_ns=1_000,
    )
    b = DirectMessage(
        friend_id=friend.id,
        direction="inbound",
        text="zweite",
        channel="telegram",
        created_at_ns=2_000,
    )
    c = DirectMessage(
        friend_id=friend.id,
        direction="outbound",
        text="dritte",
        channel="telegram",
        created_at_ns=3_000,
    )
    # Intentionally insert out of order, so ORDER BY does the work.
    await registry.messages.add(b)
    await registry.messages.add(c)
    await registry.messages.add(a)

    listed = await registry.messages.list_for_friend(friend.id)
    assert [m.text for m in listed] == ["erste", "zweite", "dritte"]


@pytest.mark.asyncio
async def test_list_respects_limit(
    registry: FriendRegistry, friend: Friend
) -> None:
    """Limit beschneidet die juengsten N — chronologisch aufsteigend."""
    for i in range(10):
        await registry.messages.add(
            DirectMessage(
                friend_id=friend.id,
                direction="outbound",
                text=f"msg-{i}",
                channel="jarvis_pubkey",
                created_at_ns=1_000 + i,
            )
        )

    listed = await registry.messages.list_for_friend(friend.id, limit=3)
    assert len(listed) == 3
    # Die juengsten 3 (msg-7, msg-8, msg-9), aufsteigend.
    assert [m.text for m in listed] == ["msg-7", "msg-8", "msg-9"]


@pytest.mark.asyncio
async def test_multi_friend_isolation(registry: FriendRegistry) -> None:
    f1 = Friend(display_name="Alice")
    f2 = Friend(display_name="Bob")
    await registry.add_friend(f1)
    await registry.add_friend(f2)

    await registry.messages.add(
        DirectMessage(
            friend_id=f1.id,
            direction="outbound",
            text="for Alice",
            channel="telegram",
        )
    )
    await registry.messages.add(
        DirectMessage(
            friend_id=f2.id,
            direction="outbound",
            text="for Bob",
            channel="jarvis_pubkey",
        )
    )

    alice_msgs = await registry.messages.list_for_friend(f1.id)
    bob_msgs = await registry.messages.list_for_friend(f2.id)
    assert len(alice_msgs) == 1
    assert alice_msgs[0].text == "for Alice"
    assert len(bob_msgs) == 1
    assert bob_msgs[0].text == "for Bob"


@pytest.mark.asyncio
async def test_delete_for_friend(
    registry: FriendRegistry, friend: Friend
) -> None:
    await registry.messages.add(
        DirectMessage(
            friend_id=friend.id,
            direction="outbound",
            text="weg gleich",
            channel="telegram",
        )
    )
    assert len(await registry.messages.list_for_friend(friend.id)) == 1

    await registry.messages.delete_for_friend(friend.id)
    assert await registry.messages.list_for_friend(friend.id) == []


@pytest.mark.asyncio
async def test_delete_friend_cascades_messages(
    registry: FriendRegistry, friend: Friend
) -> None:
    """delete_friend must clean up all of the friend's DMs."""
    await registry.messages.add(
        DirectMessage(
            friend_id=friend.id,
            direction="outbound",
            text="bald weg",
            channel="telegram",
        )
    )
    assert len(await registry.messages.list_for_friend(friend.id)) == 1

    await registry.delete_friend(friend.id)
    # Look directly against the table — the friend FK no longer exists,
    # but the DM rows must be gone too.
    msgs_after = await registry.messages.list_for_friend(friend.id)
    assert msgs_after == []


@pytest.mark.asyncio
async def test_delete_for_friend_unknown_is_noop(
    registry: FriendRegistry,
) -> None:
    # No friend with this ID — DELETE may simply hit 0 rows.
    await registry.messages.delete_for_friend(uuid4())
