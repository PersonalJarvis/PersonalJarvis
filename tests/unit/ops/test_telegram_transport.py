"""Live Telegram transport — prepared, not wired, never leaking a token.

Every request goes to an ``httpx.MockTransport``; nothing reaches Telegram.
The token is a fake string.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import jarvis
from jarvis.core.http_pool import HttpClientPool
from jarvis.ops.notify import (
    MAX_RETRY_AFTER_S,
    Notification,
    NotifyStore,
    OwnerNotifier,
    TransportError,
)
from jarvis.ops.telegram_transport import OwnerChat, TelegramBotTransport, owner_chat

FAKE_TOKEN = "123456789:FAKE-token-for-tests-only"  # noqa: S105 - a fake, never real
OWNER = 4242


def _config(users: list[int], chat_id: str = "") -> Any:
    return SimpleNamespace(allowed_user_ids=users, chat_id=chat_id)


# --- Owner ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "users,chat_id,expected",
    [
        ([], "", OwnerChat(None, "owner_not_paired")),
        ([OWNER, 99], "", OwnerChat(None, "owner_not_unique")),
        ([-100123], "", OwnerChat(None, "owner_not_private")),
        ([OWNER], "777", OwnerChat(None, "chat_id_mismatch")),
        ([OWNER], "@channel", OwnerChat(None, "chat_id_mismatch")),
        ([OWNER], str(OWNER), OwnerChat(OWNER)),
        ([OWNER, OWNER], "", OwnerChat(OWNER)),
        ([OWNER], "", OwnerChat(OWNER)),
    ],
)
def test_the_owner_chat_is_unambiguous(users: list[int], chat_id: str, expected: OwnerChat) -> None:
    assert owner_chat(_config(users, chat_id)) == expected


# --- Requests ---------------------------------------------------------------------


class Telegram:
    """A fake Bot API: scripted (status, body) answers, recorded requests."""

    def __init__(self, *answers: tuple[int, dict[str, Any]] | Exception) -> None:
        self.answers = list(answers) or [(200, {"ok": True, "result": {}})]
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _transport(
    telegram: Telegram,
    *,
    owner: OwnerChat | None = None,
    token: str | None = FAKE_TOKEN,
    sleep: Sleeps | None = None,
    clock: list[float] | None = None,
) -> TelegramBotTransport:
    ticks = clock if clock is not None else [100.0]
    chat = owner if owner is not None else OwnerChat(OWNER)
    return TelegramBotTransport(
        owner=lambda: chat,
        token=lambda: token,
        pool=HttpClientPool(transport=httpx.MockTransport(telegram.handler)),
        sleep=sleep or Sleeps(),
        monotonic=lambda: ticks[0],
    )


async def test_send_posts_plain_text_to_the_owner_only() -> None:
    telegram = Telegram()
    await _transport(telegram).send("Briefing for 2026-10-08")
    [request] = telegram.requests
    assert request.method == "POST"
    assert request.url.host == "api.telegram.org"
    assert request.url.path == f"/bot{FAKE_TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert body == {
        "chat_id": OWNER,
        "text": "Briefing for 2026-10-08",
        "disable_web_page_preview": True,
    }
    assert "parse_mode" not in body


@pytest.mark.parametrize(
    "answer,code,retryable,retry_after",
    [
        ((429, {"ok": False, "parameters": {"retry_after": 5}}), "rate_limited", True, 5.0),
        ((429, {"ok": False}), "rate_limited", True, None),
        ((502, {"ok": False}), "server_error", True, None),
        ((401, {"ok": False, "description": "Unauthorized"}), "invalid_token", False, None),
        ((404, {"ok": False}), "invalid_token", False, None),
        (
            (403, {"ok": False, "description": "bot was blocked by the user"}),
            "blocked_by_owner",
            False,
            None,
        ),
        ((400, {"ok": False, "description": "chat not found"}), "bad_request", False, None),
        ((200, {"ok": False}), "http_200", False, None),
    ],
)
async def test_errors_become_codes_without_provider_text(
    answer: tuple[int, dict[str, Any]], code: str, retryable: bool, retry_after: float | None
) -> None:
    with pytest.raises(TransportError) as exc:
        await _transport(Telegram(answer)).send("hello")
    assert (exc.value.code, exc.value.retryable, exc.value.retry_after) == (
        code,
        retryable,
        retry_after,
    )
    assert str(exc.value) == code  # no description, no body


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{FAKE_TOKEN}/x"), "network"),
        (httpx.ReadTimeout(f"timeout on /bot{FAKE_TOKEN}/sendMessage"), "timeout"),
    ],
)
async def test_network_errors_never_carry_the_url_or_token(error: Exception, code: str) -> None:
    with pytest.raises(TransportError) as exc:
        await _transport(Telegram(error)).send("hello")
    assert exc.value.code == code and exc.value.retryable is True
    assert exc.value.__cause__ is None and exc.value.__suppress_context__ is True
    assert FAKE_TOKEN not in repr(exc.value) and FAKE_TOKEN not in str(exc.value)


@pytest.mark.parametrize(
    "owner,token,code",
    [
        (OwnerChat(None, "owner_not_paired"), FAKE_TOKEN, "owner_not_paired"),
        (OwnerChat(None, "chat_id_mismatch"), FAKE_TOKEN, "chat_id_mismatch"),
        (OwnerChat(OWNER), None, "token_missing"),
    ],
)
async def test_nothing_is_requested_without_owner_or_token(
    owner: OwnerChat, token: str | None, code: str
) -> None:
    telegram = Telegram()
    with pytest.raises(TransportError) as exc:
        await _transport(telegram, owner=owner, token=token).send("hello")
    assert exc.value.code == code and exc.value.retryable is False
    assert telegram.requests == []


async def test_messages_are_paced_for_the_chat_rate_limit() -> None:
    sleeps = Sleeps()
    clock = [100.0]
    transport = _transport(Telegram(), sleep=sleeps, clock=clock)
    await transport.send("one")
    clock[0] = 100.4
    await transport.send("two")
    assert len(sleeps.calls) == 1 and abs(sleeps.calls[0] - 0.7) < 1e-6


# --- With the notifier --------------------------------------------------------------


@pytest.fixture
async def store(tmp_path: Path) -> NotifyStore:
    s = NotifyStore(tmp_path / "ops.sqlite")
    await s.save_settings(enabled=True, kinds=["daily_briefing"])
    return s


async def test_a_short_retry_after_is_waited_then_sent(store: NotifyStore) -> None:
    sleeps = Sleeps()
    telegram = Telegram((429, {"ok": False, "parameters": {"retry_after": 3}}), (200, {"ok": True}))
    notifier = OwnerNotifier(store, _transport(telegram), sleep=sleeps)
    report = await notifier.deliver([Notification("daily_briefing", "briefing:d", "hi")])
    assert report.outcomes[0].status == "delivered"
    assert sleeps.calls and sleeps.calls[0] >= 3.0
    row = await store.get("briefing:d")
    assert row is not None and (row.transport, row.attempts) == ("telegram", 2)


async def test_a_long_retry_after_leaves_the_message_for_the_next_run(
    store: NotifyStore,
) -> None:
    sleeps = Sleeps()
    long_wait = MAX_RETRY_AFTER_S * 4
    telegram = Telegram((429, {"ok": False, "parameters": {"retry_after": long_wait}}))
    notifier = OwnerNotifier(store, _transport(telegram), sleep=sleeps)
    report = await notifier.deliver([Notification("daily_briefing", "briefing:d", "hi")])
    assert (report.outcomes[0].status, report.outcomes[0].error) == ("failed", "rate_limited")
    assert all(s < long_wait for s in sleeps.calls)  # the scheduler is never held that long
    assert len(telegram.requests) == 1


async def test_logs_never_show_the_token_or_the_chat(
    store: NotifyStore, caplog: pytest.LogCaptureFixture
) -> None:
    telegram = Telegram(
        httpx.ConnectError(f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"),
    )
    with caplog.at_level(logging.DEBUG):
        await OwnerNotifier(store, _transport(telegram), sleep=Sleeps()).deliver(
            [Notification("daily_briefing", "briefing:d", "hi")]
        )
    assert caplog.records
    assert FAKE_TOKEN not in caplog.text and str(OWNER) not in caplog.text


# --- Not wired: every path still sends simulated --------------------------------------


def test_only_the_live_switch_selects_the_live_transport() -> None:
    root = Path(jarvis.__file__).parent
    users = []
    for path in root.rglob("*.py"):
        if path.name == "telegram_transport.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "TelegramBotTransport" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name in ("TelegramBotTransport", "*") for alias in node.names
            ):
                users.append(str(path.relative_to(root)))
            elif isinstance(node, ast.Name) and node.id == "TelegramBotTransport":
                users.append(str(path.relative_to(root)))
    # The live switch module is the one sanctioned user (jarvis/ops/delivery.py).
    assert sorted(set(users)) == ["ops/delivery.py"]


# --- Readiness endpoint: facts only, never the token or the chat ---------------------


async def _readiness(monkeypatch: pytest.MonkeyPatch, token: str | None, telegram: Any) -> dict:
    from fastapi import FastAPI

    from jarvis.ui.web import ops_routes

    monkeypatch.setattr("jarvis.ops.telegram_transport.default_token", lambda: token)
    app = FastAPI()
    app.include_router(ops_routes.router)
    app.state.config = SimpleNamespace(integrations=SimpleNamespace(telegram=telegram))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        res = await client.get("/api/ops/notify/telegram")
    assert res.status_code == 200
    assert FAKE_TOKEN not in res.text and str(OWNER) not in res.text
    return res.json()


async def test_readiness_before_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = SimpleNamespace(
        allowed_user_ids=[], chat_id="", enabled=False, pair_on_first_private_message=True
    )
    body = await _readiness(monkeypatch, None, telegram)
    assert body == {
        "live_enabled": False,
        "token_stored": False,
        "channel_enabled": False,
        "owner_paired": False,
        "owner_reason": "owner_not_paired",
        "pairing_open": True,
        "ready_for_activation": False,
        "next_steps": ["store_bot_token", "pair_owner_chat"],
    }


async def test_readiness_after_setup_stays_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = SimpleNamespace(
        allowed_user_ids=[OWNER], chat_id="", enabled=True, pair_on_first_private_message=False
    )
    body = await _readiness(monkeypatch, FAKE_TOKEN, telegram)
    assert body["ready_for_activation"] is True and body["next_steps"] == []
    assert body["live_enabled"] is False  # activation is a separate, approved step
