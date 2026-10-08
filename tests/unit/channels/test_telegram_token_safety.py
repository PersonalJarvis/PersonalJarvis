"""The Telegram bot token never leaks through errors, stored status or logs.

The token sits in every Bot API URL, and python-telegram-bot 22 puts it into
``InvalidToken`` ("The token `…` was rejected by the server."). These tests
feed exactly such errors through the channel, the channel manager and the
workflow ``telegram_send`` step. The token is a fake string; nothing goes out.
"""

from __future__ import annotations

import logging
import sys
import traceback
import types
from typing import Any

import httpx
import pytest

from jarvis.channels.manager import ChannelContext, ChannelManager, ChannelStartError
from jarvis.channels.telegram import TelegramChannel, _safe_error
from jarvis.core.bus import EventBus
from jarvis.core.config import TelegramConfig

FAKE_TOKEN = "987654321:AAFakeTokenForTestsOnly_abcdefghijklmnop"  # noqa: S105 - a fake


def _all_text(exc: BaseException) -> str:
    """Everything a traceback print of *exc* would show, causes included."""
    return "".join(traceback.format_exception(exc))


def test_safe_error_removes_the_exact_token_and_known_shapes() -> None:
    text = _safe_error(
        RuntimeError(f"The token `{FAKE_TOKEN}` was rejected by the server."), FAKE_TOKEN
    )
    assert FAKE_TOKEN not in text and text.startswith("RuntimeError: ")
    url_error = _safe_error(httpx.ConnectError(f"https://api.telegram.org/bot{FAKE_TOKEN}/getMe"))
    assert FAKE_TOKEN not in url_error  # the generic redactor knows the token shape


# --- Channel start ----------------------------------------------------------------


class _InvalidToken(Exception):
    pass


def _install_fake_ptb(monkeypatch: pytest.MonkeyPatch, *, fail_on: str) -> None:
    """A stand-in for telegram / telegram.ext that fails like PTB 22.8."""

    class Bot:
        def __init__(self, token: str) -> None:
            self.token = token

        async def get_me(self) -> Any:
            if fail_on == "get_me":
                raise TelegramError(f"Bad gateway for /bot{self.token}/getMe")
            return types.SimpleNamespace(username="jarvis_bot")

    class TelegramError(Exception):
        pass

    class App:
        def __init__(self, token: str) -> None:
            self.token = token

        def add_handler(self, *_a: Any) -> None:
            return None

        async def initialize(self) -> None:
            if fail_on == "initialize":
                raise _InvalidToken(f"The token `{self.token}` was rejected by the server.")

    class Builder:
        def token(self, token: str) -> Any:
            self._token = token
            return self

        def build(self) -> App:
            return App(self._token)

    telegram_mod = types.ModuleType("telegram")
    telegram_mod.Bot = Bot  # type: ignore[attr-defined]
    error_mod = types.ModuleType("telegram.error")
    error_mod.InvalidToken = _InvalidToken  # type: ignore[attr-defined]
    error_mod.TelegramError = TelegramError  # type: ignore[attr-defined]
    ext_mod = types.ModuleType("telegram.ext")
    ext_mod.ApplicationBuilder = Builder  # type: ignore[attr-defined]
    ext_mod.MessageHandler = lambda *_a, **_k: None  # type: ignore[attr-defined]
    ext_mod.filters = types.SimpleNamespace(ALL=object())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "telegram", telegram_mod)
    monkeypatch.setitem(sys.modules, "telegram.error", error_mod)
    monkeypatch.setitem(sys.modules, "telegram.ext", ext_mod)
    monkeypatch.setattr("jarvis.channels.telegram.get_secret", lambda *_a, **_k: FAKE_TOKEN)


@pytest.mark.parametrize("fail_on", ["get_me", "initialize"])
async def test_a_failed_start_never_carries_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, fail_on: str
) -> None:
    _install_fake_ptb(monkeypatch, fail_on=fail_on)
    channel = TelegramChannel(EventBus(), TelegramConfig(enabled=True))
    with pytest.raises(ChannelStartError) as exc:
        await channel.start()
    assert FAKE_TOKEN not in _all_text(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__

    # Through the manager: stored status, log and the raised error.
    manager = ChannelManager(ChannelContext(bus=EventBus()))
    monkeypatch.setattr(manager, "get", lambda _name: channel)
    with caplog.at_level(logging.DEBUG), pytest.raises(ChannelStartError) as via_manager:
        await manager.start("telegram")
    assert FAKE_TOKEN not in _all_text(via_manager.value)
    assert FAKE_TOKEN not in str(manager._start_errors)
    assert FAKE_TOKEN not in caplog.text


async def test_the_manager_redacts_any_channel_error(caplog: pytest.LogCaptureFixture) -> None:
    class Leaky:
        name = "leaky"

        async def start(self) -> None:
            raise RuntimeError(f"connect to https://api.telegram.org/bot{FAKE_TOKEN}/getUpdates")

    manager = ChannelManager(ChannelContext(bus=EventBus()))
    manager.get = lambda _name: Leaky()  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG), pytest.raises(ChannelStartError) as exc:
        await manager.start("leaky")
    assert FAKE_TOKEN not in _all_text(exc.value)
    assert FAKE_TOKEN not in str(manager._start_errors) and FAKE_TOKEN not in caplog.text
    assert await manager._start_safe("leaky") is not None
    assert FAKE_TOKEN not in (await manager._start_safe("leaky") or "")


async def test_send_failures_log_no_token_and_no_chat(caplog: pytest.LogCaptureFixture) -> None:
    class _Bot:
        async def send_message(self, **_k: Any) -> None:
            raise httpx.ConnectError(f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage")

    channel = TelegramChannel(EventBus(), TelegramConfig())
    channel._app = types.SimpleNamespace(bot=_Bot())
    with caplog.at_level(logging.DEBUG):
        await channel._send_text(55501234, "hello", language="en")
    assert caplog.records
    assert FAKE_TOKEN not in caplog.text and "55501234" not in caplog.text


# --- Workflow telegram_send step ----------------------------------------------------


@pytest.fixture
def telegram_step(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *_a, **_k: FAKE_TOKEN)
    return types.SimpleNamespace(chat_id="4242", text="hello")


def _runner() -> Any:
    from jarvis.workflows.runner import WorkflowRunner

    return WorkflowRunner.__new__(WorkflowRunner)


async def test_workflow_network_error_has_no_url_or_token(
    monkeypatch: pytest.MonkeyPatch, telegram_step: Any
) -> None:
    async def _post(self: Any, url: str, **_k: Any) -> Any:
        raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    with pytest.raises(RuntimeError) as exc:
        await _runner()._run_telegram_send(telegram_step, {}, {})
    assert str(exc.value) == "Telegram request failed: ConnectError"
    assert FAKE_TOKEN not in _all_text(exc.value)


async def test_workflow_http_error_carries_no_provider_body(
    monkeypatch: pytest.MonkeyPatch, telegram_step: Any
) -> None:
    async def _post(self: Any, url: str, **_k: Any) -> Any:
        return httpx.Response(
            400, json={"ok": False, "description": f"Bad Request: chat not found {FAKE_TOKEN}"}
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    with pytest.raises(RuntimeError) as exc:
        await _runner()._run_telegram_send(telegram_step, {}, {})
    assert str(exc.value) == "Telegram HTTP 400"
