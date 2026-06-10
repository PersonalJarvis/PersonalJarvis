"""Discord marketplace connect = enable the in-repo DiscordChannel.

Mirror of test_telegram_connect.py. Connecting Discord mirrors the validated
bot token into the canonical `discord_bot_token` secret and flips
`[integrations.discord].enabled`, so the existing bidirectional channel boots.
An explicit owner user id locks the allowlist and turns trust-on-first-DM off.
Disconnecting reverses the secret + enable flag.
"""
# ruff: noqa: S106

import types

import pytest

from jarvis.marketplace import discord_connect as dc
from jarvis.marketplace.catalog import PatPasteAuth, PluginSpec
from jarvis.ui.web import marketplace_routes as mr


def _discord_spec() -> PluginSpec:
    return PluginSpec(
        id="discord",
        display_name="Discord",
        description="d",
        category="Communication",
        logo_slug="discord",
        auth=PatPasteAuth(
            mode="pat_paste",
            token_creation_url="https://discord.com/developers/applications",
            token_prefix="",
            validation_endpoint="https://discord.com/api/v10/users/@me",
            instruction_md="md",
            auth_scheme="bot",
        ),
    )


def _fake_request():
    return types.SimpleNamespace(app=types.SimpleNamespace(state="STATE"))


def test_enable_writes_secret_and_flips_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(dc, "set_secret", lambda k, v: calls.__setitem__("secret", (k, v)) or True)
    monkeypatch.setattr(dc, "_set_discord_enabled", lambda on: calls.__setitem__("enabled", on))
    dc.on_discord_connected("bot-token")
    assert calls["secret"] == ("discord_bot_token", "bot-token")
    assert calls["enabled"] is True


def test_enable_with_user_id_locks_owner(monkeypatch):
    calls = {}
    monkeypatch.setattr(dc, "set_secret", lambda k, v: True)
    monkeypatch.setattr(dc, "_set_discord_enabled", lambda on: None)
    monkeypatch.setattr(
        dc, "_add_discord_allowed_user_id", lambda uid: calls.__setitem__("uid", uid)
    )
    monkeypatch.setattr(dc, "_set_discord_pairing", lambda on: calls.__setitem__("pairing", on))
    dc.on_discord_connected("bot-token", 4242)
    assert calls["uid"] == 4242
    assert calls["pairing"] is False


def test_owner_lock_enables_channel_last(monkeypatch):
    # Security: the allowlist + pairing-off must be persisted BEFORE the enable
    # flag, so a crash mid-sequence never leaves the channel enabled with an open
    # (pair-on-first-DM) allowlist.
    order: list = []
    monkeypatch.setattr(dc, "set_secret", lambda k, v: order.append("secret") or True)
    monkeypatch.setattr(dc, "_set_discord_enabled", lambda on: order.append(("enabled", on)))
    monkeypatch.setattr(dc, "_add_discord_allowed_user_id", lambda uid: order.append("allowlist"))
    monkeypatch.setattr(dc, "_set_discord_pairing", lambda on: order.append(("pairing", on)))

    dc.on_discord_connected("bot-token", 4242)

    assert order[-1] == ("enabled", True)
    assert order.index("allowlist") < order.index(("enabled", True))
    assert order.index(("pairing", False)) < order.index(("enabled", True))


def test_enable_without_user_id_leaves_allowlist_untouched(monkeypatch):
    calls = {"uid": "untouched", "pairing": "untouched"}
    monkeypatch.setattr(dc, "set_secret", lambda k, v: True)
    monkeypatch.setattr(dc, "_set_discord_enabled", lambda on: None)
    monkeypatch.setattr(
        dc, "_add_discord_allowed_user_id", lambda uid: calls.__setitem__("uid", uid)
    )
    monkeypatch.setattr(dc, "_set_discord_pairing", lambda on: calls.__setitem__("pairing", on))
    dc.on_discord_connected("bot-token", None)
    assert calls["uid"] == "untouched"
    assert calls["pairing"] == "untouched"


def test_enable_raises_when_secret_store_fails(monkeypatch):
    monkeypatch.setattr(dc, "set_secret", lambda k, v: False)
    monkeypatch.setattr(dc, "_set_discord_enabled", lambda on: None)
    try:
        dc.on_discord_connected("bot-token")
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError when secret store fails")


def test_disable_clears_secret_and_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(dc, "delete_secret", lambda k: calls.__setitem__("deleted", k))
    monkeypatch.setattr(dc, "_set_discord_enabled", lambda on: calls.__setitem__("enabled", on))
    dc.on_discord_disconnected()
    assert calls["deleted"] == "discord_bot_token"
    assert calls["enabled"] is False


# --- route wiring ----------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_pat_discord_enables_channel_and_applies_live(monkeypatch):
    fired = {}
    captured = {}
    spec = _discord_spec()
    monkeypatch.setattr(
        mr, "load_catalog", lambda: type("C", (), {"by_id": lambda self, _: spec})()
    )

    async def _ok(_auth, _token):
        return True, 200

    monkeypatch.setattr(mr, "_validate_token", _ok)
    monkeypatch.setattr(mr, "TokenStore", lambda: type("S", (), {"save": lambda *_: None})())
    monkeypatch.setattr(mr, "_refresh_plugin_in_live_registry", lambda _pid: None)
    monkeypatch.setattr(
        mr, "on_discord_connected", lambda tok, uid=None: fired.__setitem__("args", (tok, uid))
    )

    async def _apply(state, name):
        captured["apply"] = (state, name)
        return True

    monkeypatch.setattr(mr, "apply_channel_live", _apply)

    out = await mr.connect_pat(
        "discord",
        mr.PatConnectBody(token="bot-token", allowed_user_id=4242),
        _fake_request(),
    )

    assert out["status"] == "connected"
    assert out["live_applied"] is True
    assert fired["args"] == ("bot-token", 4242)
    assert captured["apply"] == ("STATE", "discord")


@pytest.mark.asyncio
async def test_connect_pat_discord_fails_when_channel_enable_fails(monkeypatch):
    spec = _discord_spec()

    class _Store:
        deleted = False

        def save(self, *_):
            return None

        def delete(self, plugin_id):
            assert plugin_id == "discord"
            self.deleted = True

    store = _Store()
    monkeypatch.setattr(
        mr, "load_catalog", lambda: type("C", (), {"by_id": lambda self, _: spec})()
    )

    async def _ok(_auth, _token):
        return True, 200

    monkeypatch.setattr(mr, "_validate_token", _ok)
    monkeypatch.setattr(mr, "TokenStore", lambda: store)
    monkeypatch.setattr(mr, "_refresh_plugin_in_live_registry", lambda _pid: None)

    def _boom(_token, _uid=None):
        raise RuntimeError("keyring down")

    monkeypatch.setattr(mr, "on_discord_connected", _boom)

    with pytest.raises(mr.HTTPException) as exc:
        await mr.connect_pat("discord", mr.PatConnectBody(token="bot-token"), _fake_request())

    assert exc.value.status_code == 500
    assert store.deleted is True


@pytest.mark.asyncio
async def test_disconnect_discord_disables_and_applies_live(monkeypatch):
    fired = {}
    captured = {}
    spec = _discord_spec()
    monkeypatch.setattr(
        mr, "load_catalog", lambda: type("C", (), {"by_id": lambda self, _: spec})()
    )
    monkeypatch.setattr(mr, "TokenStore", lambda: type("S", (), {"delete": lambda *_: None})())
    monkeypatch.setattr(mr, "_refresh_plugin_in_live_registry", lambda _pid: None)
    monkeypatch.setattr(mr, "on_discord_disconnected", lambda: fired.__setitem__("called", True))

    async def _apply(state, name):
        captured["apply"] = (state, name)
        return True

    monkeypatch.setattr(mr, "apply_channel_live", _apply)

    out = await mr.disconnect("discord", _fake_request())

    assert out["status"] == "not_connected"
    assert fired["called"] is True
    assert captured["apply"] == ("STATE", "discord")
