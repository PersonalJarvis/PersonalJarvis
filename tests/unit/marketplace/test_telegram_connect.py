"""Telegram marketplace connect = enable the in-repo TelegramChannel.

Connecting Telegram mirrors the validated bot token into the canonical
`telegram_bot_token` secret and flips `[integrations.telegram].enabled`, so the
existing bidirectional channel boots. Disconnecting reverses both.
"""
# ruff: noqa: S106

import types

import pytest

from jarvis.marketplace import telegram_connect as tc
from jarvis.marketplace.catalog import PatPasteAuth, PluginSpec
from jarvis.ui.web import marketplace_routes as mr


def _fake_request():
    # Channel routes now take the FastAPI Request to reach app.state for the
    # live reload. A state without channel_manager makes apply_channel_live a
    # safe no-op (returns False), which is all these enable-hook tests need.
    return types.SimpleNamespace(app=types.SimpleNamespace(state=None))


def test_enable_writes_secret_and_flips_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        tc, "set_secret", lambda k, v: calls.__setitem__("secret", (k, v)) or True
    )
    monkeypatch.setattr(
        tc, "_set_telegram_enabled", lambda on: calls.__setitem__("enabled", on)
    )
    tc.on_telegram_connected("123:ABC")
    assert calls["secret"] == ("telegram_bot_token", "123:ABC")
    assert calls["enabled"] is True


def test_enable_with_user_id_locks_owner(monkeypatch):
    calls = {}
    monkeypatch.setattr(tc, "set_secret", lambda k, v: True)
    monkeypatch.setattr(tc, "_set_telegram_enabled", lambda on: None)
    monkeypatch.setattr(
        tc, "_add_telegram_allowed_user_id", lambda uid: calls.__setitem__("uid", uid)
    )
    monkeypatch.setattr(
        tc, "_set_telegram_pairing", lambda on: calls.__setitem__("pairing", on)
    )
    tc.on_telegram_connected("123:ABC", 7777)
    assert calls["uid"] == 7777
    assert calls["pairing"] is False


def test_enable_raises_when_secret_store_fails(monkeypatch):
    monkeypatch.setattr(tc, "set_secret", lambda k, v: False)
    monkeypatch.setattr(tc, "_set_telegram_enabled", lambda on: None)
    try:
        tc.on_telegram_connected("123:ABC")
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError when secret store fails")


def test_disable_clears_secret_and_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        tc, "delete_secret", lambda k: calls.__setitem__("deleted", k)
    )
    monkeypatch.setattr(
        tc, "_set_telegram_enabled", lambda on: calls.__setitem__("enabled", on)
    )
    tc.on_telegram_disconnected()
    assert calls["deleted"] == "telegram_bot_token"
    assert calls["enabled"] is False

@pytest.mark.asyncio
async def test_connect_pat_telegram_fires_the_enable_hook(monkeypatch):
    fired = {}
    spec = PluginSpec(
        id="telegram", display_name="Telegram", description="d",
        category="Communication", logo_slug="telegram",
        auth=PatPasteAuth(
            mode="pat_paste", token_creation_url="https://t.me/BotFather",
            token_prefix="", validation_endpoint="https://api.telegram.org/bot{token}/getMe",
            instruction_md="md", auth_scheme="telegram_path",
        ),
    )

    class _Cat:
        def by_id(self, _):
            return spec

    monkeypatch.setattr(mr, "load_catalog", lambda: _Cat())

    async def _ok(_auth, _token):
        return True, 200

    monkeypatch.setattr(mr, "_validate_token", _ok)
    monkeypatch.setattr(mr, "TokenStore", lambda: type("S", (), {"save": lambda *_: None})())
    monkeypatch.setattr(
        mr, "on_telegram_connected", lambda tok, uid=None: fired.__setitem__("tok", tok)
    )

    out = await mr.connect_pat(
        "telegram", mr.PatConnectBody(token="123:ABC"), _fake_request()
    )
    assert out["status"] == "connected"
    assert fired["tok"] == "123:ABC"


@pytest.mark.asyncio
async def test_connect_pat_telegram_fails_when_channel_enable_fails(monkeypatch):
    spec = PluginSpec(
        id="telegram", display_name="Telegram", description="d",
        category="Communication", logo_slug="telegram",
        auth=PatPasteAuth(
            mode="pat_paste", token_creation_url="https://t.me/BotFather",
            token_prefix="", validation_endpoint="https://api.telegram.org/bot{token}/getMe",
            instruction_md="md", auth_scheme="telegram_path",
        ),
    )

    class _Cat:
        def by_id(self, _):
            return spec

    class _Store:
        deleted = False

        def save(self, *_):
            return None

        def delete(self, plugin_id):
            assert plugin_id == "telegram"
            self.deleted = True

    store = _Store()
    monkeypatch.setattr(mr, "load_catalog", lambda: _Cat())
    async def _ok(_auth, _token):
        return True, 200

    monkeypatch.setattr(mr, "_validate_token", _ok)
    monkeypatch.setattr(mr, "TokenStore", lambda: store)

    def _boom(_token, _uid=None):
        raise RuntimeError("keyring down")

    monkeypatch.setattr(mr, "on_telegram_connected", _boom)

    with pytest.raises(mr.HTTPException) as exc:
        await mr.connect_pat(
            "telegram", mr.PatConnectBody(token="123:ABC"), _fake_request()
        )

    assert exc.value.status_code == 500
    assert store.deleted is True
