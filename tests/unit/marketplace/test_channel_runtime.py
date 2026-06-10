"""apply_channel_live applies a freshly written channel config to the running
ChannelManager without a restart: rebuild the context from disk, reload the one
channel, and rebind the chat-bridge consumer. No live manager → safe no-op.
"""

from __future__ import annotations

import types

import pytest

from jarvis.marketplace import channel_runtime as cr


class _Ctx:
    bus = "BUS"
    friend_registry = "REG"


class _FakeManager:
    def __init__(self) -> None:
        self.context = _Ctx()
        self.reloaded: list[str] = []
        self.set_ctx = None

    def set_context(self, ctx) -> None:
        self.set_ctx = ctx

    async def reload(self, name: str) -> None:
        self.reloaded.append(name)


class _FakeBridge:
    def __init__(self) -> None:
        self.refreshed: list[str] = []

    async def refresh(self, name: str) -> None:
        self.refreshed.append(name)


@pytest.mark.asyncio
async def test_no_manager_returns_false() -> None:
    state = types.SimpleNamespace(channel_manager=None, channel_chat_bridge=None)
    assert await cr.apply_channel_live(state, "discord") is False


@pytest.mark.asyncio
async def test_full_path_reloads_and_refreshes(monkeypatch) -> None:
    mgr = _FakeManager()
    bridge = _FakeBridge()
    state = types.SimpleNamespace(channel_manager=mgr, channel_chat_bridge=bridge)
    fake_cfg = types.SimpleNamespace(
        integrations=types.SimpleNamespace(telegram="TG", discord="DC")
    )
    monkeypatch.setattr(cr, "load_config", lambda: fake_cfg)

    ok = await cr.apply_channel_live(state, "discord")

    assert ok is True
    assert mgr.reloaded == ["discord"]
    assert bridge.refreshed == ["discord"]
    assert mgr.set_ctx is not None
    assert mgr.set_ctx.bus == "BUS"
    assert mgr.set_ctx.friend_registry == "REG"
    assert mgr.set_ctx.config["telegram_config"] == "TG"
    assert mgr.set_ctx.config["discord_config"] == "DC"


@pytest.mark.asyncio
async def test_reload_failure_returns_false(monkeypatch) -> None:
    class _BoomManager(_FakeManager):
        async def reload(self, name: str) -> None:
            raise RuntimeError("login failed")

    mgr = _BoomManager()
    bridge = _FakeBridge()
    state = types.SimpleNamespace(channel_manager=mgr, channel_chat_bridge=bridge)
    fake_cfg = types.SimpleNamespace(
        integrations=types.SimpleNamespace(telegram="TG", discord="DC")
    )
    monkeypatch.setattr(cr, "load_config", lambda: fake_cfg)

    assert await cr.apply_channel_live(state, "discord") is False
