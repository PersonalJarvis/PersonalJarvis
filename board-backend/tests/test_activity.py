"""Activity-Feed + Visibility + Story + interesting-Algorithmus.

Smoke-Tests aus Plan §D:
- test_feed_excludes_non_friend_items   (friends-only Items)
- test_visibility_private_never_appears_in_feed
- test_visibility_public_appears_in_feed_for_anyone
- test_story_expires_after_24h
- test_interesting_algorithm_is_deterministic
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from board_backend.crypto import canonical_json, generate_keypair, sign
from board_backend.models import ActivityItem, Friend
from board_backend.routes.activity import interesting_score


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _signed_post(client: TestClient, path: str, *, priv: str, pub: str, payload: dict):
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=priv)
    return client.post(
        path, content=body,
        headers={"X-Pubkey": pub, "X-Jarvis-Sig": sig, "Content-Type": "application/json"},
    )


def _signed_get(client: TestClient, path: str, *, priv: str, pub: str, params: dict | None = None,
                payload: dict | None = None):
    payload = payload or {"ts_ms": _now_ms()}
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=priv)
    return client.request(
        "GET", path, content=body, params=params or {},
        headers={"X-Pubkey": pub, "X-Jarvis-Sig": sig, "Content-Type": "application/json"},
    )


def _setup_owner(client: TestClient, owner_pub: str) -> None:
    resp = client.post(
        "/api/v1/identity/register",
        json={"pubkey": owner_pub, "display_name": "Alice"},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert resp.status_code == 200


def _add_friend(client: TestClient, owner_pubkey: str, friend_pubkey: str) -> None:
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(Friend(
            owner_pubkey=owner_pubkey,
            friend_pubkey=friend_pubkey,
            friend_url="http://friend:8765",
            friend_display_name="Bob",
            paired_at=datetime.now(timezone.utc),
        ))
        session.commit()


def _create_item(
    client: TestClient, *, priv: str, pub: str, kind: str, visibility: str,
    payload: dict | None = None, expires_in_hours: int | None = None,
) -> str:
    body = {
        "ts_ms": _now_ms(),
        "kind": kind,
        "payload": payload or {},
        "visibility": visibility,
    }
    if expires_in_hours is not None:
        body["expires_in_hours"] = expires_in_hours
    resp = _signed_post(client, "/api/v1/activities", priv=priv, pub=pub, payload=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ----------------------------------------------------------------------
# Feed-Visibility-Tests
# ----------------------------------------------------------------------

def test_feed_excludes_non_friend_items(client: TestClient) -> None:
    """Plan-Smoke: ein non-friend pullt → friends-Items NICHT enthalten,
    public/Items aber schon."""
    owner_priv, owner_pub = generate_keypair()
    _, friend_pub = generate_keypair()
    _, stranger_pub = generate_keypair()

    _setup_owner(client, owner_pub)
    _add_friend(client, owner_pub, friend_pub)

    pub_id = _create_item(client, priv=owner_priv, pub=owner_pub,
                          kind="milestone", visibility="public")
    fr_id = _create_item(client, priv=owner_priv, pub=owner_pub,
                         kind="milestone", visibility="friends")
    pr_id = _create_item(client, priv=owner_priv, pub=owner_pub,
                         kind="milestone", visibility="private")

    stranger_priv, _ = generate_keypair()
    stranger_priv = stranger_priv  # noqa
    # Stranger-Keypair separat
    sp_priv, sp_pub = generate_keypair()
    resp = _signed_get(client, "/api/v1/federation/feed", priv=sp_priv, pub=sp_pub)
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert pub_id in ids
    assert fr_id not in ids
    assert pr_id not in ids


def test_visibility_private_never_appears_in_feed(client: TestClient) -> None:
    owner_priv, owner_pub = generate_keypair()
    fp_priv, fp_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    _add_friend(client, owner_pub, fp_pub)

    pr_id = _create_item(client, priv=owner_priv, pub=owner_pub,
                         kind="milestone", visibility="private")

    # Friend schaut: KEIN private item.
    resp = _signed_get(client, "/api/v1/federation/feed", priv=fp_priv, pub=fp_pub)
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert pr_id not in ids


def test_visibility_public_appears_in_feed_for_anyone(client: TestClient) -> None:
    owner_priv, owner_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    pub_id = _create_item(client, priv=owner_priv, pub=owner_pub,
                          kind="milestone", visibility="public")

    # Random Stranger pullt — sieht das public-Item.
    sp_priv, sp_pub = generate_keypair()
    resp = _signed_get(client, "/api/v1/federation/feed", priv=sp_priv, pub=sp_pub)
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert pub_id in ids


def test_owner_sees_own_private_via_federation(client: TestClient) -> None:
    """Owner pulling its own feed sieht alles (inkl. private)."""
    owner_priv, owner_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    pr_id = _create_item(client, priv=owner_priv, pub=owner_pub,
                         kind="milestone", visibility="private")
    resp = _signed_get(client, "/api/v1/federation/feed",
                       priv=owner_priv, pub=owner_pub)
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert pr_id in ids


# ----------------------------------------------------------------------
# Stories-Expiry
# ----------------------------------------------------------------------

def test_story_creates_expires_at_24h(client: TestClient) -> None:
    owner_priv, owner_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    sid = _create_item(client, priv=owner_priv, pub=owner_pub,
                       kind="story", visibility="friends",
                       payload={"text": "what I worked on"})
    factory = client.app.state.session_factory
    with factory() as session:
        item = session.get(ActivityItem, sid)
        assert item is not None
        assert item.expires_at is not None
        ea = item.expires_at if item.expires_at.tzinfo else item.expires_at.replace(tzinfo=timezone.utc)
        delta = (ea - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 23.5 < delta < 24.5


def test_story_expires_after_24h(client: TestClient) -> None:
    """Plan-Smoke: nach Time-Travel +25h verschwindet die Story aus dem Feed."""
    owner_priv, owner_pub = generate_keypair()
    fp_priv, fp_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    _add_friend(client, owner_pub, fp_pub)

    sid = _create_item(client, priv=owner_priv, pub=owner_pub,
                       kind="story", visibility="friends",
                       payload={"text": "vergaenglich"})

    # Jetzt sichtbar.
    r1 = _signed_get(client, "/api/v1/federation/feed", priv=fp_priv, pub=fp_pub)
    assert sid in {i["id"] for i in r1.json()["items"]}

    # In der DB die expires_at auf 1 min in der Vergangenheit setzen.
    factory = client.app.state.session_factory
    with factory() as session:
        item = session.get(ActivityItem, sid)
        item.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()

    r2 = _signed_get(client, "/api/v1/federation/feed", priv=fp_priv, pub=fp_pub)
    assert sid not in {i["id"] for i in r2.json()["items"]}


# ----------------------------------------------------------------------
# Reaction-Visibility
# ----------------------------------------------------------------------

def test_owner_sees_own_reaction_counts(client: TestClient) -> None:
    """Plan §D §0: Owner sieht Counts mit Zahl, andere nicht."""
    from board_backend.models import Reaction

    owner_priv, owner_pub = generate_keypair()
    _, fp_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    _add_friend(client, owner_pub, fp_pub)

    iid = _create_item(client, priv=owner_priv, pub=owner_pub,
                       kind="achievement_unlocked", visibility="friends")
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(Reaction(item_id=iid, reactor_pubkey=fp_pub, reaction="rocket"))
        session.add(Reaction(item_id=iid, reactor_pubkey=fp_pub, reaction="fire"))
        session.commit()

    # Owner pulled eigenen Feed.
    own = _signed_get(client, "/api/v1/activities",
                      priv=owner_priv, pub=owner_pub).json()
    own_item = next(i for i in own["items"] if i["id"] == iid)
    assert own_item["reaction_counts"] == {"rocket": 1, "brain": 0, "fire": 1}

    # Friend pulled federation-feed → counts MUST BE None
    fp_priv = None  # erzeugen
    fp_priv, fp_pub2 = generate_keypair()
    # reuse fp_pub from above? Wir haben nur den pubkey hinzugefuegt; brauchen
    # den priv. Generiere neu, fuege als zweiten friend hinzu.
    _add_friend(client, owner_pub, fp_pub2)

    fed = _signed_get(client, "/api/v1/federation/feed",
                      priv=fp_priv, pub=fp_pub2).json()
    f_item = next(i for i in fed["items"] if i["id"] == iid)
    assert f_item["reaction_counts"] is None
    assert f_item["has_reactions"] is True


# ----------------------------------------------------------------------
# Interesting-Algorithm — Plan-Smoke
# ----------------------------------------------------------------------

def test_interesting_algorithm_is_deterministic() -> None:
    """Plan-Smoke: same input → same order. Pure-function check."""
    a = interesting_score(reactions_total=5, age_hours=10)
    b = interesting_score(reactions_total=5, age_hours=10)
    assert a == b

    inputs = [(0, 1), (1, 1), (2, 1), (1, 50), (5, 0)]
    once = [interesting_score(r, h) for r, h in inputs]
    twice = [interesting_score(r, h) for r, h in inputs]
    assert once == twice


def test_interesting_sorts_recent_higher_for_equal_reactions(client: TestClient) -> None:
    owner_priv, owner_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    older_id = _create_item(client, priv=owner_priv, pub=owner_pub,
                            kind="milestone", visibility="public")
    # Setze created_at zurueck.
    factory = client.app.state.session_factory
    with factory() as session:
        item = session.get(ActivityItem, older_id)
        item.created_at = datetime.now(timezone.utc) - timedelta(days=5)
        session.commit()
    newer_id = _create_item(client, priv=owner_priv, pub=owner_pub,
                            kind="milestone", visibility="public")

    sp_priv, sp_pub = generate_keypair()
    resp = _signed_get(client, "/api/v1/federation/feed",
                       priv=sp_priv, pub=sp_pub, params={"sort": "interesting"})
    ids = [i["id"] for i in resp.json()["items"]]
    assert ids.index(newer_id) < ids.index(older_id)


def test_latest_sort_is_strict_chronological(client: TestClient) -> None:
    owner_priv, owner_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    a = _create_item(client, priv=owner_priv, pub=owner_pub,
                     kind="milestone", visibility="public")
    factory = client.app.state.session_factory
    with factory() as session:
        item = session.get(ActivityItem, a)
        item.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.commit()
    b = _create_item(client, priv=owner_priv, pub=owner_pub,
                     kind="milestone", visibility="public")

    sp_priv, sp_pub = generate_keypair()
    resp = _signed_get(client, "/api/v1/federation/feed",
                       priv=sp_priv, pub=sp_pub, params={"sort": "latest"})
    ids = [i["id"] for i in resp.json()["items"]]
    assert ids == [b, a]


# ----------------------------------------------------------------------
# Activity-Create-Pflichten
# ----------------------------------------------------------------------

def test_activity_create_rejects_extra_fields(client: TestClient) -> None:
    """PII-Wand: extra='forbid' wirkt auch hier."""
    owner_priv, owner_pub = generate_keypair()
    _setup_owner(client, owner_pub)
    body = {
        "ts_ms": _now_ms(),
        "kind": "milestone",
        "payload": {},
        "visibility": "friends",
        "raw_transcript": "secret",      # extra-Feld
    }
    resp = _signed_post(client, "/api/v1/activities",
                        priv=owner_priv, pub=owner_pub, payload=body)
    assert resp.status_code == 422
