"""Pair tests for Phase D, Commit 1."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from board_backend.config import Settings
from board_backend.crypto import canonical_json, generate_keypair, sign
from board_backend.main import create_app
from board_backend.models import Friend, PairToken


# ----------------------------------------------------------------------
# Helpers + fixtures for an "owner"-registered backend
# ----------------------------------------------------------------------

def _register_owner(client: TestClient, pubkey: str, name: str = "Owner") -> None:
    resp = client.post(
        "/api/v1/identity/register",
        json={"pubkey": pubkey, "display_name": name},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert resp.status_code == 200, resp.text


def _signed_get(client: TestClient, path: str, *, priv: str, pub: str) -> "TestClient.Response":
    payload = {"ts_ms": int(time.time() * 1000)}
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=priv)
    return client.request(
        "GET", path, content=body,
        headers={"X-Pubkey": pub, "X-Jarvis-Sig": sig, "Content-Type": "application/json"},
    )


@pytest.fixture
def owner_keys() -> tuple[str, str]:
    return generate_keypair()


@pytest.fixture
def friend_keys() -> tuple[str, str]:
    return generate_keypair()


@pytest.fixture
def owner_client(client: TestClient, owner_keys) -> TestClient:
    _register_owner(client, owner_keys[1], "Alice")
    return client


# ----------------------------------------------------------------------
# Pair tests
# ----------------------------------------------------------------------

def test_pair_initiate_returns_token_and_url(owner_client: TestClient) -> None:
    resp = owner_client.post(
        "/api/v1/pair/initiate", json={},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["token"]) >= 32
    assert "/pair/redeem?token=" in body["url"]
    assert body["expires_at"]


def test_pair_initiate_requires_admin(owner_client: TestClient) -> None:
    resp = owner_client.post(
        "/api/v1/pair/initiate", json={},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_pair_creates_bidirectional_friendship(
    owner_client: TestClient, friend_keys: tuple[str, str]
) -> None:
    """Plan smoke #1: after a successful accept, the owner's backend is
    friends with the friend. (Bidirectional on the owner's side — the
    friend's side depends on their backend; we test that in
    test_two_backends_pair.)"""
    init = owner_client.post(
        "/api/v1/pair/initiate", json={},
        headers={"X-Admin-Token": "test-admin"},
    ).json()

    _, friend_pub = friend_keys
    resp = owner_client.post("/api/v1/pair/accept", json={
        "token": init["token"],
        "friend_pubkey": friend_pub,
        "friend_url": "http://friend-backend:8765",
        "friend_display_name": "Bob",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["owner_pubkey"]
    assert body["owner_display_name"] == "Alice"

    # Friendship in DB
    factory = owner_client.app.state.session_factory
    with factory() as session:
        rows = session.query(Friend).all()
        assert len(rows) == 1
        assert rows[0].friend_pubkey == friend_pub
        assert rows[0].friend_url == "http://friend-backend:8765"


def test_pair_token_expires_after_10min(
    owner_client: TestClient, friend_keys: tuple[str, str], settings: Settings,
) -> None:
    """Plan smoke #2: token > 10 min old → 401."""
    init = owner_client.post(
        "/api/v1/pair/initiate", json={},
        headers={"X-Admin-Token": "test-admin"},
    ).json()

    # Move the expires_at row in the DB to 11 min in the past.
    factory = owner_client.app.state.session_factory
    with factory() as session:
        tok = session.get(PairToken, init["token"])
        assert tok is not None
        tok.expires_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        tok.created_at = datetime.now(timezone.utc) - timedelta(minutes=21)
        session.commit()

    _, friend_pub = friend_keys
    resp = owner_client.post("/api/v1/pair/accept", json={
        "token": init["token"],
        "friend_pubkey": friend_pub,
        "friend_url": "http://b:8765",
        "friend_display_name": "Bob",
    })
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_pair_token_single_use(
    owner_client: TestClient, friend_keys: tuple[str, str]
) -> None:
    init = owner_client.post(
        "/api/v1/pair/initiate", json={},
        headers={"X-Admin-Token": "test-admin"},
    ).json()
    _, friend_pub = friend_keys
    body = {
        "token": init["token"],
        "friend_pubkey": friend_pub,
        "friend_url": "http://b:8765",
        "friend_display_name": "Bob",
    }
    r1 = owner_client.post("/api/v1/pair/accept", json=body)
    r2 = owner_client.post("/api/v1/pair/accept", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 401
    assert "already used" in r2.json()["detail"].lower()


def test_pair_unknown_token_rejected(owner_client: TestClient) -> None:
    _, friend_pub = generate_keypair()
    resp = owner_client.post("/api/v1/pair/accept", json={
        "token": "00" * 24,
        "friend_pubkey": friend_pub,
        "friend_url": "http://b:8765",
        "friend_display_name": "Bob",
    })
    assert resp.status_code == 401


def test_pair_self_pubkey_rejected(
    owner_client: TestClient, owner_keys: tuple[str, str]
) -> None:
    init = owner_client.post(
        "/api/v1/pair/initiate", json={},
        headers={"X-Admin-Token": "test-admin"},
    ).json()
    _, owner_pub = owner_keys
    resp = owner_client.post("/api/v1/pair/accept", json={
        "token": init["token"],
        "friend_pubkey": owner_pub,
        "friend_url": "http://localhost:8765",
        "friend_display_name": "Self",
    })
    assert resp.status_code == 400


def test_friends_list_endpoint(
    owner_client: TestClient, owner_keys: tuple[str, str], friend_keys: tuple[str, str]
) -> None:
    """GET /api/v1/friends with a signed owner request lists the friend(s)."""
    owner_priv, owner_pub = owner_keys
    init = owner_client.post(
        "/api/v1/pair/initiate", json={},
        headers={"X-Admin-Token": "test-admin"},
    ).json()
    _, friend_pub = friend_keys
    owner_client.post("/api/v1/pair/accept", json={
        "token": init["token"],
        "friend_pubkey": friend_pub,
        "friend_url": "http://b:8765",
        "friend_display_name": "Bob",
    })

    resp = _signed_get(owner_client, "/api/v1/friends", priv=owner_priv, pub=owner_pub)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["friends"]) == 1
    assert body["friends"][0]["display_name"] == "Bob"
