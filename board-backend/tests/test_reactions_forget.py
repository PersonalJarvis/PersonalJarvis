"""Tests for reactions forwarding, inbound reactions, forget-me, cleanup."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from board_backend.background import StoriesCleanup
from board_backend.crypto import canonical_json, generate_keypair, sign
from board_backend.models import ActivityItem, Friend, Reaction


def _now_ms() -> int:
    return int(time.time() * 1000)


def _signed_post(client: TestClient, path: str, *, priv: str, pub: str, payload: dict):
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=priv)
    return client.post(
        path, content=body,
        headers={"X-Pubkey": pub, "X-Jarvis-Sig": sig, "Content-Type": "application/json"},
    )


def _setup_owner(client: TestClient, owner_pub: str, name: str = "Alice") -> None:
    resp = client.post(
        "/api/v1/identity/register",
        json={"pubkey": owner_pub, "display_name": name},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert resp.status_code == 200


def _create_local_item(client: TestClient, *, priv: str, pub: str, vis: str = "friends") -> str:
    body = {
        "ts_ms": _now_ms(),
        "kind": "achievement_unlocked",
        "payload": {"achievement_id": "tool_master"},
        "visibility": vis,
    }
    return _signed_post(client, "/api/v1/activities", priv=priv, pub=pub, payload=body).json()["id"]


# ----------------------------------------------------------------------
# Forwarding test with MockTransport
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reaction_propagates_to_author_backend(client: TestClient) -> None:
    """Plan smoke: owner reacts to a friend's item, the friend's backend
    gets /federation/reactions/inbound forwarded."""
    owner_priv, owner_pub = generate_keypair()
    _setup_owner(client, owner_pub)

    # Register friend (B) in the owner's friends table.
    _, friend_pub = generate_keypair()
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(Friend(
            owner_pubkey=owner_pub, friend_pubkey=friend_pub,
            friend_url="http://friend-backend:8765",
            friend_display_name="Bob",
            paired_at=datetime.now(timezone.utc),
        ))
        session.commit()

    # Mock transport that catches the forward.
    captured: dict = {}

    async def _handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["pubkey"] = req.headers.get("x-pubkey")
        captured["sig"] = req.headers.get("x-jarvis-sig")
        captured["body"] = req.content
        return httpx.Response(200, json={"accepted": True})

    transport = httpx.MockTransport(_handler)
    client.app.state.federation_http = httpx.AsyncClient(
        transport=transport, base_url="http://x", timeout=5.0,
    )
    try:
        # Owner reacts to an item ID that lives on the friend's backend.
        body = {
            "ts_ms": _now_ms(),
            "item_id": "fakeitem123",
            "reaction": "rocket",
            "author_pubkey": friend_pub,
        }
        resp = _signed_post(client, "/api/v1/reactions",
                            priv=owner_priv, pub=owner_pub, payload=body)
        assert resp.status_code == 200, resp.text
        assert captured["url"].endswith("/api/v1/federation/reactions/inbound")
        assert captured["pubkey"] == owner_pub
        assert b"fakeitem123" in captured["body"]
    finally:
        await client.app.state.federation_http.aclose()
        client.app.state.federation_http = None


def test_inbound_reaction_persists_when_friend(client: TestClient) -> None:
    """Friend (B) signs an inbound reaction → owner's backend stores it."""
    owner_priv, owner_pub = generate_keypair()
    friend_priv, friend_pub = generate_keypair()
    _setup_owner(client, owner_pub)

    # Register B as a friend.
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(Friend(
            owner_pubkey=owner_pub, friend_pubkey=friend_pub,
            friend_url="http://b:8765", friend_display_name="Bob",
            paired_at=datetime.now(timezone.utc),
        ))
        session.commit()

    iid = _create_local_item(client, priv=owner_priv, pub=owner_pub)

    body = {
        "ts_ms": _now_ms(),
        "item_id": iid,
        "reaction": "fire",
        "author_pubkey": owner_pub,
    }
    resp = _signed_post(client, "/api/v1/federation/reactions/inbound",
                        priv=friend_priv, pub=friend_pub, payload=body)
    assert resp.status_code == 200, resp.text

    with factory() as session:
        rows = session.query(Reaction).filter(Reaction.item_id == iid).all()
        assert len(rows) == 1
        assert rows[0].reactor_pubkey == friend_pub
        assert rows[0].reaction == "fire"


def test_inbound_reaction_rejects_non_friend(client: TestClient) -> None:
    owner_priv, owner_pub = generate_keypair()
    stranger_priv, stranger_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    iid = _create_local_item(client, priv=owner_priv, pub=owner_pub)

    body = {
        "ts_ms": _now_ms(),
        "item_id": iid,
        "reaction": "fire",
        "author_pubkey": owner_pub,
    }
    resp = _signed_post(client, "/api/v1/federation/reactions/inbound",
                        priv=stranger_priv, pub=stranger_pub, payload=body)
    assert resp.status_code == 403


def test_inbound_reaction_idempotent(client: TestClient) -> None:
    owner_priv, owner_pub = generate_keypair()
    friend_priv, friend_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(Friend(
            owner_pubkey=owner_pub, friend_pubkey=friend_pub,
            friend_url="http://b:8765", friend_display_name="Bob",
            paired_at=datetime.now(timezone.utc),
        ))
        session.commit()
    iid = _create_local_item(client, priv=owner_priv, pub=owner_pub)

    body = {
        "ts_ms": _now_ms(),
        "item_id": iid,
        "reaction": "fire",
        "author_pubkey": owner_pub,
    }
    r1 = _signed_post(client, "/api/v1/federation/reactions/inbound",
                      priv=friend_priv, pub=friend_pub, payload=body)
    r2 = _signed_post(client, "/api/v1/federation/reactions/inbound",
                      priv=friend_priv, pub=friend_pub, payload={**body, "ts_ms": _now_ms()})
    assert r1.status_code == 200
    assert r2.status_code == 200
    with factory() as session:
        rows = session.query(Reaction).filter(Reaction.item_id == iid).all()
        # UNIQUE constraint blocked the second reaction.
        assert len(rows) == 1


# ----------------------------------------------------------------------
# Right-to-be-forgotten
# ----------------------------------------------------------------------

def test_right_to_be_forgotten_removes_all_traces(client: TestClient) -> None:
    """Plan smoke: signed DELETE /federation/identity/{pubkey} deletes
    friendship + reactions + activities for that pubkey."""
    owner_priv, owner_pub = generate_keypair()
    friend_priv, friend_pub = generate_keypair()
    _setup_owner(client, owner_pub)

    # Register friend B + leave reactions.
    factory = client.app.state.session_factory
    iid = _create_local_item(client, priv=owner_priv, pub=owner_pub)
    with factory() as session:
        session.add(Friend(
            owner_pubkey=owner_pub, friend_pubkey=friend_pub,
            friend_url="http://b:8765", friend_display_name="Bob",
            paired_at=datetime.now(timezone.utc),
        ))
        session.add(Reaction(
            item_id=iid, reactor_pubkey=friend_pub, reaction="rocket",
        ))
        session.add(Reaction(
            item_id=iid, reactor_pubkey=friend_pub, reaction="brain",
        ))
        session.commit()

    # B signed DELETE.
    payload = {"ts_ms": _now_ms()}
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=friend_priv)
    resp = client.request(
        "DELETE",
        f"/api/v1/federation/identity/{friend_pub}",
        content=body,
        headers={"X-Pubkey": friend_pub, "X-Jarvis-Sig": sig,
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    body_resp = resp.json()
    assert body_resp["deleted_friendship"] is True
    assert body_resp["deleted_reactions"] == 2

    # Verify DB
    with factory() as session:
        assert session.get(Friend, (owner_pub, friend_pub)) is None
        assert session.query(Reaction).filter(
            Reaction.reactor_pubkey == friend_pub
        ).count() == 0


def test_forget_me_path_and_signature_must_match(client: TestClient) -> None:
    """If the DELETE path pubkey != X-Pubkey in the header → 403."""
    owner_priv, owner_pub = generate_keypair()
    friend_priv, friend_pub = generate_keypair()
    other_priv, other_pub = generate_keypair()
    _setup_owner(client, owner_pub)

    payload = {"ts_ms": _now_ms()}
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=other_priv)

    resp = client.request(
        "DELETE",
        f"/api/v1/federation/identity/{friend_pub}",   # path
        content=body,
        headers={"X-Pubkey": other_pub, "X-Jarvis-Sig": sig,    # but other signs
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


# ----------------------------------------------------------------------
# Stories cleanup
# ----------------------------------------------------------------------

def test_stories_cleanup_removes_only_expired(client: TestClient) -> None:
    owner_priv, owner_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    factory = client.app.state.session_factory

    # A story with expires_at in the past + an achievement without expires.
    expired_id = _create_local_item(client, priv=owner_priv, pub=owner_pub, vis="friends")
    keep_id = _create_local_item(client, priv=owner_priv, pub=owner_pub, vis="friends")
    with factory() as session:
        item = session.get(ActivityItem, expired_id)
        item.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.commit()

    cleanup = StoriesCleanup(session_factory=factory)
    deleted = cleanup.run_once()
    assert deleted == 1

    with factory() as session:
        assert session.get(ActivityItem, expired_id) is None
        assert session.get(ActivityItem, keep_id) is not None
