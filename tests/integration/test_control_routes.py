"""Jarvis Control API facade (step 6) — end-to-end via FastAPI TestClient.

Proves the authenticated /api/control/* surface: the Bearer gate, machine
discovery, a SAFE config write that actually lands in the (temp) jarvis.toml,
forbidden-path refusal, secret management (mocked keyring), and key reveal.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core import config as cfg_mod
from jarvis.core import control_key
from jarvis.core.bus import EventBus
from jarvis.ui.web.control_routes import ALLOWED_SECRET_KEYS
from jarvis.ui.web.control_routes import router as control_router

KEY = "jctl_testkey_abcd"
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text('[brain]\nreply_language = "de"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(config_file))

    # Deterministic auth: a fixed stored key, no real keyring access.
    monkeypatch.setattr(control_key, "get_control_key", lambda: KEY)
    monkeypatch.setattr(control_key, "rotate_control_key", lambda: "jctl_rotated_key")

    # Mocked secret store.
    secret_store: dict[str, str] = {}

    def _set(k, v):
        secret_store[k] = v
        return True

    def _get(k, env_fallback=None):
        return secret_store.get(k)

    def _del(k):
        secret_store.pop(k, None)
        return True

    monkeypatch.setattr(cfg_mod, "set_secret", _set)
    monkeypatch.setattr(cfg_mod, "get_secret", _get)
    monkeypatch.setattr(cfg_mod, "delete_secret", _del)

    app = FastAPI()
    app.state.bus = EventBus()
    app.state.config = cfg_mod.load_config()
    app.include_router(control_router)
    return TestClient(app), config_file, secret_store


# --- auth gate ---


def test_requires_key(client) -> None:
    tc, _, _ = client
    assert tc.get("/api/control/auth/probe").status_code == 401
    bad = tc.get("/api/control/auth/probe", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_probe_ok_with_key(client) -> None:
    tc, _, _ = client
    res = tc.get("/api/control/auth/probe", headers=AUTH)
    assert res.status_code == 200 and res.json()["ok"] is True


# --- discovery ---


def test_allowlist_exposes_reply_language(client) -> None:
    tc, _, _ = client
    res = tc.get("/api/control/allowlist", headers=AUTH)
    assert res.status_code == 200
    paths = {s["path"] for s in res.json()["specs"]}
    assert "brain.reply_language" in paths


# --- config write (the headline: SAFE applies immediately, lands on disk) ---


def test_put_config_reply_language_applies_and_persists(client) -> None:
    tc, config_file, _ = client
    res = tc.put(
        "/api/control/config",
        json={"path": "brain.reply_language", "value": "en"},
        headers=AUTH,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied"] is True
    assert body["needs_confirmation"] is False
    # The mutation actually landed in the env-pointed config file.
    assert cfg_mod.load_config().brain.reply_language == "en"


def test_language_verb_sets_reply_and_ui_language(client) -> None:
    # A concrete code switches BOTH the reply language and the visible interface
    # language so the whole app reflects it.
    tc, _, _ = client
    res = tc.put("/api/control/language", json={"reply_language": "es"}, headers=AUTH)
    assert res.status_code == 200 and res.json()["applied"] is True
    assert res.json()["ui_language"]["value"] == "es"
    loaded = cfg_mod.load_config()
    assert loaded.brain.reply_language == "es"
    assert loaded.ui.language == "es"


def test_language_verb_auto_leaves_ui_untouched(client) -> None:
    # "auto" only mirrors the reply language; it must not force an interface code.
    tc, _, _ = client
    res = tc.put("/api/control/language", json={"reply_language": "auto"}, headers=AUTH)
    assert res.status_code == 200
    assert "ui_language" not in res.json()
    assert cfg_mod.load_config().brain.reply_language == "auto"


def test_put_config_forbidden_path_is_403(client) -> None:
    tc, _, _ = client
    res = tc.put(
        "/api/control/config",
        json={"path": "security.admin_password_hash", "value": "x"},
        headers=AUTH,
    )
    assert res.status_code == 403


def test_safety_path_is_forbidden_for_read_and_write(client) -> None:
    # The risk-tier whitelist/blacklist must never be reachable via the API.
    tc, _, _ = client
    assert tc.get("/api/control/config?path=safety.whitelist", headers=AUTH).status_code == 403
    write = tc.put(
        "/api/control/config",
        json={"path": "safety.whitelist", "value": "x"},
        headers=AUTH,
    )
    assert write.status_code == 403


def test_language_rejects_invalid_code(client) -> None:
    # A bad language code is rejected at the boundary (422), never written.
    tc, _, _ = client
    res = tc.put("/api/control/language", json={"reply_language": "zh"}, headers=AUTH)
    assert res.status_code == 422


def test_put_config_unknown_path_is_400(client) -> None:
    tc, _, _ = client
    res = tc.put(
        "/api/control/config",
        json={"path": "brain.totally_made_up", "value": "x"},
        headers=AUTH,
    )
    assert res.status_code == 400


# --- secrets ---


def test_secrets_roundtrip(client) -> None:
    tc, _, secret_store = client
    some_key = sorted(ALLOWED_SECRET_KEYS)[0]
    # set
    set_res = tc.put(f"/api/control/secrets/{some_key}", json={"value": "sk-123456"}, headers=AUTH)
    assert set_res.status_code == 200
    # list shows it configured + masked (never the raw value)
    listing = tc.get("/api/control/secrets", headers=AUTH).json()["secrets"]
    entry = next(s for s in listing if s["key"] == some_key)
    assert entry["configured"] is True
    assert entry["preview"] and "sk-123456" not in entry["preview"]
    # delete
    assert tc.delete(f"/api/control/secrets/{some_key}", headers=AUTH).status_code == 200
    assert some_key not in secret_store


def test_unknown_secret_key_is_404(client) -> None:
    tc, _, _ = client
    res = tc.put("/api/control/secrets/not_a_real_key", json={"value": "x"}, headers=AUTH)
    assert res.status_code == 404


# --- control-API key reveal + rotate ---


def test_get_api_key(client) -> None:
    tc, _, _ = client
    res = tc.get("/api/control/api-key", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["key"] == KEY


def test_rotate_requires_confirm(client) -> None:
    tc, _, _ = client
    no_confirm = tc.post("/api/control/api-key/rotate", json={"confirm": False}, headers=AUTH)
    assert no_confirm.status_code == 400
    res = tc.post("/api/control/api-key/rotate", json={"confirm": True}, headers=AUTH)
    assert res.status_code == 200 and res.json()["key"] == "jctl_rotated_key"


# --- user-chosen key (PUT /api/control/api-key) ---


@pytest.mark.no_auto_web_auth
def test_set_custom_key_requires_auth(client) -> None:
    # Opt out of the conftest auto-session: this asserts the raw boundary.
    tc, _, _ = client
    res = tc.put("/api/control/api-key", json={"value": "correct-horse-battery", "confirm": True})
    assert res.status_code == 401


def test_set_custom_key_requires_confirm(client) -> None:
    tc, _, _ = client
    res = tc.put("/api/control/api-key", json={"value": "correct-horse-battery"}, headers=AUTH)
    assert res.status_code == 400


def test_set_custom_key_rejects_weak_or_invalid_values(client) -> None:
    tc, _, _ = client
    too_short = tc.put(
        "/api/control/api-key", json={"value": "short", "confirm": True}, headers=AUTH
    )
    assert too_short.status_code == 422
    bad_chars = tc.put(
        "/api/control/api-key",
        json={"value": "has spaces in the key", "confirm": True},
        headers=AUTH,
    )
    assert bad_chars.status_code == 422


def test_set_custom_key_persists_and_returns_masked_only(client) -> None:
    tc, _, secret_store = client
    res = tc.put(
        "/api/control/api-key",
        json={"value": "correct-horse-battery", "confirm": True},
        headers=AUTH,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["masked"] == "…tery"
    # The caller typed the value — the clear key never crosses the wire twice.
    assert "key" not in body
    assert secret_store[control_key.KEYRING_SLOT] == "correct-horse-battery"


# --- providers (snapshot needs no live manager) ---


def test_providers_snapshot(client) -> None:
    tc, _, _ = client
    res = tc.get("/api/control/providers", headers=AUTH)
    assert res.status_code == 200
    assert "providers" in res.json() and "settings" in res.json()
