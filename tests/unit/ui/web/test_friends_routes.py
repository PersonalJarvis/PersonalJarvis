# === F-FRIENDS [F3] · feature/friends-section · ruben-2026-05-01 ===
# === F-FRIENDS [F2] · feature/friends-section · ruben-2026-04-30 ===
"""REST route tests for the Phase-F2 Friends API."""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.friends.registry import FriendRegistry
from jarvis.ui.web.friends_routes import router as friends_router


@pytest_asyncio.fixture
async def registry() -> FriendRegistry:
    reg = FriendRegistry(":memory:")
    await reg.open()
    try:
        yield reg
    finally:
        await reg.close()


@pytest.fixture
def app_no_registry() -> FastAPI:
    app = FastAPI()
    app.include_router(friends_router)
    return app


@pytest.fixture
def app_with_registry(registry: FriendRegistry) -> FastAPI:
    app = FastAPI()
    app.include_router(friends_router)
    app.state.friend_registry = registry
    return app


# 503 ohne Registry


def test_list_returns_503_without_registry(app_no_registry: FastAPI) -> None:
    with TestClient(app_no_registry) as client:
        r = client.get("/api/friends")
    assert r.status_code == 503
    assert "FriendRegistry" in r.json()["detail"]


# Listing + Create + Get


def test_list_empty(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.get("/api/friends")
    assert r.status_code == 200
    assert r.json() == []


def test_create_and_list(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post(
            "/api/friends",
            json={"display_name": "Daniel", "note": "Schule"},
        )
        assert r.status_code == 201
        created = r.json()
        assert created["display_name"] == "Daniel"
        assert created["note"] == "Schule"
        assert created["channels"] == []
        assert created["permission_profile"] == "minimal"

        r2 = client.get("/api/friends")
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["display_name"] == "Daniel"


def test_create_validates_empty_name(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": ""})
    assert r.status_code == 422


def test_get_friend_404(app_with_registry: FastAPI) -> None:
    fake = "00000000-0000-0000-0000-000000000000"
    with TestClient(app_with_registry) as client:
        r = client.get(f"/api/friends/{fake}")
    assert r.status_code == 404


def test_get_friend_invalid_uuid(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.get("/api/friends/not-a-uuid")
    assert r.status_code == 400


def test_get_friend_detail(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Alice"})
        fid = r.json()["id"]
        r2 = client.get(f"/api/friends/{fid}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == fid
    assert body["display_name"] == "Alice"
    assert body["permission_profile"] == "minimal"


# PATCH + DELETE


def test_patch_friend_partial_update(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post(
            "/api/friends", json={"display_name": "Alice", "note": "Bekannt"}
        )
        fid = r.json()["id"]
        r2 = client.patch(f"/api/friends/{fid}", json={"display_name": "Alice K."})
    assert r2.status_code == 200
    assert r2.json()["display_name"] == "Alice K."
    assert r2.json()["note"] == "Bekannt"


def test_delete_friend(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Tmp"})
        fid = r.json()["id"]
        r2 = client.delete(f"/api/friends/{fid}")
        assert r2.status_code == 204

        r3 = client.get(f"/api/friends/{fid}")
    assert r3.status_code == 404


def test_delete_unknown_404(app_with_registry: FastAPI) -> None:
    fake = "00000000-0000-0000-0000-000000000000"
    with TestClient(app_with_registry) as client:
        r = client.delete(f"/api/friends/{fake}")
    assert r.status_code == 404


# Channels


def test_link_channel(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Daniel"})
        fid = r.json()["id"]
        r2 = client.post(
            f"/api/friends/{fid}/channels",
            json={"channel": "telegram", "handle": "42", "is_primary": True},
        )
    assert r2.status_code == 201
    assert len(r2.json()["channels"]) == 1
    assert r2.json()["channels"][0]["channel"] == "telegram"
    assert r2.json()["channels"][0]["is_primary"] is True


def test_unlink_channel(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Daniel"})
        fid = r.json()["id"]
        client.post(
            f"/api/friends/{fid}/channels",
            json={"channel": "telegram", "handle": "42"},
        )
        r2 = client.delete(f"/api/friends/{fid}/channels/telegram/42")
        assert r2.status_code == 204

        r3 = client.get(f"/api/friends/{fid}")
    assert r3.json()["channels"] == []


# Permissions


def test_get_permission_default_minimal(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Alice"})
        fid = r.json()["id"]
        r2 = client.get(f"/api/friends/{fid}/permission")
    assert r2.status_code == 200
    assert r2.json()["profile"] == "minimal"


def test_patch_permission(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Alice"})
        fid = r.json()["id"]
        r2 = client.patch(
            f"/api/friends/{fid}/permission",
            json={"profile": "detailed"},
        )
    assert r2.status_code == 200
    assert r2.json()["profile"] == "detailed"


def test_patch_permission_unknown_friend(app_with_registry: FastAPI) -> None:
    fake = "00000000-0000-0000-0000-000000000000"
    with TestClient(app_with_registry) as client:
        r = client.patch(
            f"/api/friends/{fake}/permission",
            json={"profile": "standard"},
        )
    assert r.status_code == 404


def test_patch_permission_invalid_profile(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Alice"})
        fid = r.json()["id"]
        r2 = client.patch(
            f"/api/friends/{fid}/permission", json={"profile": "extreme"}
        )
    assert r2.status_code == 422


# Messages


def test_list_messages_empty_initially(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Daniel"})
        fid = r.json()["id"]
        r2 = client.get(f"/api/friends/{fid}/messages")
    assert r2.status_code == 200
    assert r2.json() == []


def test_send_message_without_channel_400(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "GhostFriend"})
        fid = r.json()["id"]
        r2 = client.post(f"/api/friends/{fid}/messages", json={"text": "hi"})
    assert r2.status_code == 400
    assert "Channel" in r2.json()["detail"]


def test_send_message_pubkey_persists_outbound(app_with_registry: FastAPI) -> None:
    """F3: the jarvis_pubkey branch is no longer 501 — outbound lands in the DB."""
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "JarvisFriend"})
        fid = r.json()["id"]
        client.post(
            f"/api/friends/{fid}/channels",
            json={"channel": "jarvis_pubkey", "handle": "abc123", "is_primary": True},
        )
        r2 = client.post(f"/api/friends/{fid}/messages", json={"text": "hi"})
    assert r2.status_code == 201
    body = r2.json()
    assert body["direction"] == "outbound"
    assert body["text"] == "hi"
    assert body["channel"] == "jarvis_pubkey"
    assert isinstance(body["timestamp_ns"], int)


def test_list_messages_after_send_returns_items(app_with_registry: FastAPI) -> None:
    """GET /messages reads back the outbound items persisted via POST."""
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "JarvisFriend"})
        fid = r.json()["id"]
        client.post(
            f"/api/friends/{fid}/channels",
            json={"channel": "jarvis_pubkey", "handle": "abc123", "is_primary": True},
        )
        client.post(f"/api/friends/{fid}/messages", json={"text": "erste"})
        client.post(f"/api/friends/{fid}/messages", json={"text": "zweite"})

        r2 = client.get(f"/api/friends/{fid}/messages")
    assert r2.status_code == 200
    items = r2.json()
    assert len(items) == 2
    # chronologisch aufsteigend
    assert items[0]["text"] == "erste"
    assert items[1]["text"] == "zweite"
    assert all(it["direction"] == "outbound" for it in items)
    assert all(it["channel"] == "jarvis_pubkey" for it in items)


def test_send_message_telegram_no_manager_503(app_with_registry: FastAPI) -> None:
    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Daniel"})
        fid = r.json()["id"]
        client.post(
            f"/api/friends/{fid}/channels",
            json={"channel": "telegram", "handle": "42", "is_primary": True},
        )
        r2 = client.post(f"/api/friends/{fid}/messages", json={"text": "Hallo"})
    assert r2.status_code == 503
    assert "TelegramChannel" in r2.json()["detail"]


def test_send_message_telegram_routes_via_manager(
    app_with_registry: FastAPI,
) -> None:
    from unittest.mock import AsyncMock

    fake_telegram = AsyncMock()
    fake_telegram.send_message = AsyncMock()

    class FakeManager:
        def started(self) -> list[str]:
            return ["telegram"]

        def get(self, name: str) -> Any:
            assert name == "telegram"
            return fake_telegram

    app_with_registry.state.channel_manager = FakeManager()

    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Daniel"})
        fid = r.json()["id"]
        client.post(
            f"/api/friends/{fid}/channels",
            json={"channel": "telegram", "handle": "42", "is_primary": True},
        )
        r2 = client.post(f"/api/friends/{fid}/messages", json={"text": "Hi Daniel"})

    assert r2.status_code == 201
    body = r2.json()
    assert body["direction"] == "outbound"
    assert body["text"] == "Hi Daniel"
    assert body["channel"] == "telegram"
    fake_telegram.send_message.assert_awaited_once()
    args = fake_telegram.send_message.call_args.args
    assert args[0].metadata["telegram_chat_id"] == 42
    assert args[0].content == "Hi Daniel"


def test_send_telegram_persists_outbound(app_with_registry: FastAPI) -> None:
    """F3: Telegram send must additionally create an outbound echo in the DB."""
    from unittest.mock import AsyncMock

    fake_telegram = AsyncMock()
    fake_telegram.send_message = AsyncMock()

    class FakeManager:
        def started(self) -> list[str]:
            return ["telegram"]

        def get(self, name: str) -> Any:
            return fake_telegram

    app_with_registry.state.channel_manager = FakeManager()

    with TestClient(app_with_registry) as client:
        r = client.post("/api/friends", json={"display_name": "Daniel"})
        fid = r.json()["id"]
        client.post(
            f"/api/friends/{fid}/channels",
            json={"channel": "telegram", "handle": "42", "is_primary": True},
        )
        client.post(f"/api/friends/{fid}/messages", json={"text": "Hi Daniel"})

        r2 = client.get(f"/api/friends/{fid}/messages")
    assert r2.status_code == 200
    items = r2.json()
    assert len(items) == 1
    assert items[0]["text"] == "Hi Daniel"
    assert items[0]["channel"] == "telegram"
    assert items[0]["direction"] == "outbound"
