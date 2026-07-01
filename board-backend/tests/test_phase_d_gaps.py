"""Tests for Phase D spec gaps: /stories, since param, friend PATCH."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from board_backend.crypto import canonical_json, generate_keypair, sign
from board_backend.models import ActivityItem, Friend


def _now_ms() -> int:
    return int(time.time() * 1000)


def _signed(method: str, client: TestClient, path: str, *, priv: str, pub: str,
            payload: dict, params: dict | None = None):
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=priv)
    return client.request(
        method, path, content=body, params=params or {},
        headers={"X-Pubkey": pub, "X-Jarvis-Sig": sig, "Content-Type": "application/json"},
    )


def _setup_owner(client: TestClient, pub: str, name: str = "Alice") -> None:
    resp = client.post(
        "/api/v1/identity/register",
        json={"pubkey": pub, "display_name": name},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert resp.status_code == 200


# ----------------------------------------------------------------------
# /stories — separate route
# ----------------------------------------------------------------------

def test_stories_route_creates_story_with_24h_expiry(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _setup_owner(client, pub)
    body = {
        "ts_ms": _now_ms(),
        "text": "Wrote a lot of code today.",
        "visibility": "friends",
    }
    resp = _signed("POST", client, "/api/v1/stories", priv=priv, pub=pub, payload=body)
    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert item["kind"] == "story"
    assert item["payload"]["text"] == "Wrote a lot of code today."
    assert item["expires_at"] is not None

    # Verify the 24h default.
    factory = client.app.state.session_factory
    with factory() as session:
        row = session.get(ActivityItem, item["id"])
        ea = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        delta_h = (ea - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 23.5 < delta_h < 24.5


def test_stories_rejects_too_long_text(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _setup_owner(client, pub)
    body = {
        "ts_ms": _now_ms(),
        "text": "x" * 281,            # > 280
        "visibility": "friends",
    }
    resp = _signed("POST", client, "/api/v1/stories", priv=priv, pub=pub, payload=body)
    assert resp.status_code == 422


def test_stories_rejects_extra_fields(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _setup_owner(client, pub)
    body = {
        "ts_ms": _now_ms(),
        "text": "ok",
        "visibility": "friends",
        "transcript_leak": "secret",     # extra
    }
    resp = _signed("POST", client, "/api/v1/stories", priv=priv, pub=pub, payload=body)
    assert resp.status_code == 422


# ----------------------------------------------------------------------
# /federation/feed?since=...
# ----------------------------------------------------------------------

def test_feed_since_filters_old_items(client: TestClient) -> None:
    """Plan §D-Spec: ``since`` filters out items with created_at < since."""
    priv, pub = generate_keypair()
    _setup_owner(client, pub)

    # Item 1: old
    old = _signed("POST", client, "/api/v1/activities",
                  priv=priv, pub=pub,
                  payload={"ts_ms": _now_ms(), "kind": "milestone",
                           "payload": {}, "visibility": "public"}).json()["id"]
    # Backdate item 1's created_at.
    factory = client.app.state.session_factory
    with factory() as session:
        row = session.get(ActivityItem, old)
        row.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        session.commit()

    # Item 2: new
    new = _signed("POST", client, "/api/v1/activities",
                  priv=priv, pub=pub,
                  payload={"ts_ms": _now_ms(), "kind": "milestone",
                           "payload": {}, "visibility": "public"}).json()["id"]

    # Pulling with since=yesterday should only return the new item.
    sp_priv, sp_pub = generate_keypair()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp = _signed("GET", client, "/api/v1/federation/feed",
                   priv=sp_priv, pub=sp_pub,
                   payload={"ts_ms": _now_ms()},
                   params={"since": yesterday})
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert new in ids
    assert old not in ids


def test_feed_since_invalid_400(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _setup_owner(client, pub)
    sp_priv, sp_pub = generate_keypair()
    resp = _signed("GET", client, "/api/v1/federation/feed",
                   priv=sp_priv, pub=sp_pub,
                   payload={"ts_ms": _now_ms()},
                   params={"since": "not-a-date"})
    assert resp.status_code == 400


# ----------------------------------------------------------------------
# PATCH /friends/{pubkey}  (per-friend pull_interval_s)
# ----------------------------------------------------------------------

def test_friend_patch_updates_pull_interval(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _, fp = generate_keypair()
    _setup_owner(client, pub)
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(Friend(
            owner_pubkey=pub, friend_pubkey=fp,
            friend_url="http://b:8765", friend_display_name="Bob",
            paired_at=datetime.now(timezone.utc), pull_interval_s=120,
        ))
        session.commit()

    resp = _signed("PATCH", client, f"/api/v1/friends/{fp}",
                   priv=priv, pub=pub,
                   payload={"ts_ms": _now_ms(), "pull_interval_s": 300})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pull_interval_s"] == 300

    with factory() as session:
        row = session.get(Friend, (pub, fp))
        assert row.pull_interval_s == 300


def test_friend_patch_rejects_too_short_interval(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _, fp = generate_keypair()
    _setup_owner(client, pub)
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(Friend(
            owner_pubkey=pub, friend_pubkey=fp,
            friend_url="http://b:8765", friend_display_name="Bob",
            paired_at=datetime.now(timezone.utc),
        ))
        session.commit()
    resp = _signed("PATCH", client, f"/api/v1/friends/{fp}",
                   priv=priv, pub=pub,
                   payload={"ts_ms": _now_ms(), "pull_interval_s": 10})
    assert resp.status_code == 422


def test_friend_patch_404_on_unknown_friend(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _, fp = generate_keypair()
    _setup_owner(client, pub)
    resp = _signed("PATCH", client, f"/api/v1/friends/{fp}",
                   priv=priv, pub=pub,
                   payload={"ts_ms": _now_ms(), "pull_interval_s": 180})
    assert resp.status_code == 404
