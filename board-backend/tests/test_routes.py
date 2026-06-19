"""Route-Tests fuer Phase C, Commit 2 — Sicherheitsanforderungen aus PLAN.md."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from board_backend.crypto import canonical_json, generate_keypair, sign


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _register(client: TestClient, *, pubkey: str, name: str = "Tester",
              admin_token: str = "test-admin") -> None:
    resp = client.post(
        "/api/v1/identity/register",
        json={"pubkey": pubkey, "display_name": name},
        headers={"X-Admin-Token": admin_token},
    )
    assert resp.status_code == 200, resp.text


def _signed_post(client: TestClient, path: str, *, priv: str, pub: str,
                 payload: dict) -> "TestClient.Response":
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=priv)
    return client.post(
        path,
        content=body,
        headers={
            "X-Pubkey": pub,
            "X-Jarvis-Sig": sig,
            "Content-Type": "application/json",
        },
    )


def _signed_get(client: TestClient, path: str, *, priv: str, pub: str,
                payload: dict) -> "TestClient.Response":
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=priv)
    return client.request(
        "GET", path,
        content=body,
        headers={
            "X-Pubkey": pub,
            "X-Jarvis-Sig": sig,
            "Content-Type": "application/json",
        },
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _minimal_payload(*, display_name: str = "Tester") -> dict:
    return {
        "ts_ms": _now_ms(),
        "display_name": display_name,
        "daily_stats": [],
        "achievements": [],
    }


# ----------------------------------------------------------------------
# Identity-Register
# ----------------------------------------------------------------------

def test_register_requires_admin_token(client: TestClient) -> None:
    _, pub = generate_keypair()
    resp = client.post(
        "/api/v1/identity/register",
        json={"pubkey": pub, "display_name": "X"},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_register_succeeds_with_admin_token(client: TestClient) -> None:
    _, pub = generate_keypair()
    resp = client.post(
        "/api/v1/identity/register",
        json={"pubkey": pub, "display_name": "Ruben"},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pubkey"] == pub
    assert body["display_name"] == "Ruben"


def test_register_rejects_non_hex_pubkey(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/identity/register",
        json={"pubkey": "ZZ" * 32, "display_name": "X"},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert resp.status_code == 422


def test_register_rate_limit_enforced(client: TestClient) -> None:
    """11. Request in einer Minute → 429 (Limit ist 10)."""
    headers = {"X-Admin-Token": "wrong"}  # invalid token, aber Rate-Limit greift VOR der Token-Pruefung
    last = None
    for i in range(12):
        resp = client.post(
            "/api/v1/identity/register",
            json={"pubkey": f"{i:064x}", "display_name": "X"},
            headers=headers,
        )
        last = resp
    assert last is not None
    assert last.status_code == 429


# ----------------------------------------------------------------------
# Signed Sync — Plan §C-Smoke-Tests
# ----------------------------------------------------------------------

def test_signed_sync_accepts_valid_signature(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)

    payload = _minimal_payload()
    payload["daily_stats"] = [{
        "date": "2026-04-24",
        "tasks_completed": 5,
        "tasks_failed": 1,
        "tools_used": ["bash", "search_web"],
        "unique_tools_count": 2,
        "voice_commands_count": 3,
        "voice_first_try_rate": 0.8,
        "hours_saved_estimate": 1.5,
    }]
    resp = _signed_post(client, "/api/v1/sync", priv=priv, pub=pub, payload=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["daily_stats_count"] == 1


def test_signed_sync_rejects_tampered_payload(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)

    payload = _minimal_payload()
    payload["daily_stats"] = [{
        "date": "2026-04-24",
        "tasks_completed": 1,
        "tasks_failed": 0,
        "tools_used": [],
        "unique_tools_count": 0,
        "voice_commands_count": 0,
        "hours_saved_estimate": 0,
    }]
    sig = sign(payload, privkey_hex=priv)

    # Manipulate after signing — typischer Replay-Tamper-Vector.
    tampered = dict(payload)
    tampered["daily_stats"] = [{**payload["daily_stats"][0], "tasks_completed": 99999}]
    body = canonical_json(tampered)
    resp = client.post(
        "/api/v1/sync",
        content=body,
        headers={
            "X-Pubkey": pub,
            "X-Jarvis-Sig": sig,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_signed_sync_rejects_unregistered_pubkey(client: TestClient) -> None:
    priv, pub = generate_keypair()
    payload = _minimal_payload()
    resp = _signed_post(client, "/api/v1/sync", priv=priv, pub=pub, payload=payload)
    assert resp.status_code == 401
    assert "not registered" in resp.json()["detail"].lower()


def test_signed_sync_rejects_old_timestamp(client: TestClient) -> None:
    """Plan §C-Sec: Replay-Protection bei ts > 5min Vergangenheit."""
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)
    payload = _minimal_payload()
    payload["ts_ms"] = _now_ms() - 6 * 60 * 1000   # 6 min alt
    resp = _signed_post(client, "/api/v1/sync", priv=priv, pub=pub, payload=payload)
    assert resp.status_code == 401
    assert "replay" in resp.json()["detail"].lower()


def test_signed_sync_rejects_future_timestamp(client: TestClient) -> None:
    """Drift in beide Richtungen — Plan §C-Sec implizit."""
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)
    payload = _minimal_payload()
    payload["ts_ms"] = _now_ms() + 6 * 60 * 1000
    resp = _signed_post(client, "/api/v1/sync", priv=priv, pub=pub, payload=payload)
    assert resp.status_code == 401


def test_signed_sync_missing_signature_header(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)
    payload = _minimal_payload()
    body = canonical_json(payload)
    resp = client.post(
        "/api/v1/sync",
        content=body,
        headers={"X-Pubkey": pub, "Content-Type": "application/json"},
    )
    assert resp.status_code == 422  # FastAPI Header-Pflichtfeld


# ----------------------------------------------------------------------
# PII-Filter — Plan §C-Sec, kein Voice-Text/Tool-IO am Backend
# ----------------------------------------------------------------------

def test_no_pii_in_sync_payload(client: TestClient) -> None:
    """Forbidden-Phrases-Liste analog zu Phase A test_no_pii_in_aggregated_stats."""
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)

    payload = _minimal_payload()
    # Bewusster PII-Leak-Versuch: extra Top-Level-Feld
    payload["voice_transcript"] = "Mein passwort ist hunter2"
    resp = _signed_post(client, "/api/v1/sync", priv=priv, pub=pub, payload=payload)
    assert resp.status_code == 422


def test_no_pii_in_daily_stats_extra_field(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)

    payload = _minimal_payload()
    payload["daily_stats"] = [{
        "date": "2026-04-24",
        "tasks_completed": 1,
        "tasks_failed": 0,
        "tools_used": [],
        "unique_tools_count": 0,
        "voice_commands_count": 0,
        "hours_saved_estimate": 0,
        "raw_transcript": "credit-card 4111-1111",
    }]
    resp = _signed_post(client, "/api/v1/sync", priv=priv, pub=pub, payload=payload)
    assert resp.status_code == 422


def test_pushlog_contains_no_payload_inhalte(client: TestClient) -> None:
    """Server speichert nur Metadaten, keinen Payload-Inhalt."""
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)

    payload = _minimal_payload()
    payload["daily_stats"] = [{
        "date": "2026-04-24",
        "tasks_completed": 7,
        "tasks_failed": 0,
        "tools_used": ["bash"],
        "unique_tools_count": 1,
        "voice_commands_count": 0,
        "hours_saved_estimate": 0,
    }]
    resp = _signed_post(client, "/api/v1/sync", priv=priv, pub=pub, payload=payload)
    assert resp.status_code == 200

    # Inspiziere die DB direkt via app.state.engine
    from sqlalchemy import select
    from board_backend.models import PushLog
    factory = client.app.state.session_factory
    with factory() as session:
        rows = session.execute(select(PushLog)).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        # Kein Inhalt-Feld in PushLog-Schema vorhanden
        assert not hasattr(row, "payload")
        assert not hasattr(row, "tools_used")
        assert row.daily_stats_count == 1
        assert row.achievements_count == 0


# ----------------------------------------------------------------------
# /me — signed GET
# ----------------------------------------------------------------------

def test_me_returns_identity_after_register(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _register(client, pubkey=pub, name="Ruben")
    payload = {"ts_ms": _now_ms()}
    resp = _signed_get(client, "/api/v1/me", priv=priv, pub=pub, payload=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["pubkey"] == pub
    assert body["display_name"] == "Ruben"
    assert body["push_count"] == 0


def test_me_push_count_increments(client: TestClient) -> None:
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)
    # Drei Pushes
    for _ in range(3):
        payload = _minimal_payload()
        payload["ts_ms"] = _now_ms()
        resp = _signed_post(client, "/api/v1/sync", priv=priv, pub=pub, payload=payload)
        assert resp.status_code == 200
    payload_me = {"ts_ms": _now_ms()}
    resp = _signed_get(client, "/api/v1/me", priv=priv, pub=pub, payload=payload_me)
    assert resp.json()["push_count"] == 3


# ----------------------------------------------------------------------
# Replay
# ----------------------------------------------------------------------

def test_signature_replay_with_changed_body_rejected(client: TestClient) -> None:
    """Klassischer Replay-Vector: Sig + Body von vorhin, ts neu — sig matcht
    aber den re-canonicalized body nicht (weil ts geaendert wurde)."""
    priv, pub = generate_keypair()
    _register(client, pubkey=pub)
    payload = _minimal_payload()
    sig = sign(payload, privkey_hex=priv)

    # Replay: ts_ms ueberschreiben → Sig matcht nicht mehr
    replayed = dict(payload)
    replayed["ts_ms"] = _now_ms() + 1
    body = canonical_json(replayed)
    resp = client.post(
        "/api/v1/sync",
        content=body,
        headers={
            "X-Pubkey": pub,
            "X-Jarvis-Sig": sig,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
