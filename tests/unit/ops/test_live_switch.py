"""Live Telegram switch: off by default, owner-only, one test message, then off.

Every Bot API call goes to an ``httpx.MockTransport``; the token is fake.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.http_pool import HttpClientPool
from jarvis.ops import delivery
from jarvis.ops.delivery import (
    LiveDisabled,
    LiveNotReady,
    disable_live,
    enable_live,
    select_transport,
    send_connection_test,
)
from jarvis.ops.notify import (
    Notification,
    NotifyStore,
    OwnerNotifier,
    SimulatedTelegramTransport,
)
from jarvis.ops.telegram_transport import OwnerChat, TelegramBotTransport

FAKE_TOKEN = "123456789:FAKE-token-for-tests-only-abcdefghij"  # noqa: S105 - a fake
OWNER = 4242
# The owner's chosen test text.
TEXT = (
    "🤖 PersonalJarvis ist erfolgreich mit Telegram verbunden. "  # i18n-allow
    "Phase 7 – Verbindungstest erfolgreich!"  # i18n-allow
)
DAY = date(2026, 10, 8)


def _telegram(users: list[int] | None = None, chat_id: str = "") -> Any:
    return SimpleNamespace(
        allowed_user_ids=[OWNER] if users is None else users, chat_id=chat_id, enabled=False
    )


class BotApi:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    def live(self, owner: Any) -> TelegramBotTransport:
        return TelegramBotTransport(
            owner=owner,
            token=lambda: FAKE_TOKEN,
            pool=HttpClientPool(transport=httpx.MockTransport(self.handler)),
        )


@pytest.fixture
def store(tmp_path: Path) -> NotifyStore:
    return NotifyStore(tmp_path / "ops.sqlite")


# --- The switch ----------------------------------------------------------------------


async def test_off_by_default(store: NotifyStore) -> None:
    settings = await store.settings()
    assert settings.live is False
    assert settings.to_dict()["transport"] == "simulated"
    assert isinstance(select_transport(settings, _telegram()), SimulatedTelegramTransport)


async def test_an_old_database_gets_the_switch_off(tmp_path: Path) -> None:
    db = tmp_path / "ops.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE ops_notify_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
        " enabled INTEGER NOT NULL DEFAULT 0, kinds TEXT NOT NULL, updated_ms INTEGER NOT NULL)"
    )
    con.execute("INSERT INTO ops_notify_settings VALUES (1, 1, 'daily_briefing', 5)")
    con.commit()
    con.close()
    settings = await NotifyStore(db).settings()
    assert (settings.enabled, settings.live) == (True, False)


@pytest.mark.parametrize(
    "token,telegram,reason",
    [
        (None, _telegram(), "token_missing"),
        (FAKE_TOKEN, _telegram([]), "owner_not_paired"),
        (FAKE_TOKEN, _telegram([OWNER, 99]), "owner_not_unique"),
        (FAKE_TOKEN, _telegram(chat_id="777"), "chat_id_mismatch"),
        (FAKE_TOKEN, _telegram([-100]), "owner_not_private"),
    ],
)
async def test_switching_on_needs_token_and_exactly_one_private_owner(
    store: NotifyStore, token: str | None, telegram: Any, reason: str
) -> None:
    with pytest.raises(LiveNotReady) as exc:
        await enable_live(store, telegram, token=lambda: token)
    assert exc.value.reason == reason
    assert (await store.settings()).live is False


async def test_on_then_off_and_any_settings_change_turns_it_off(store: NotifyStore) -> None:
    await store.save_settings(enabled=True, kinds=["daily_briefing"])
    on = await enable_live(store, _telegram(), token=lambda: FAKE_TOKEN)
    assert on.live and on.enabled and on.kinds == frozenset({"daily_briefing"})  # opt-in kept
    assert (await disable_live(store)).live is False
    await enable_live(store, _telegram(), token=lambda: FAKE_TOKEN)
    await store.save_settings(enabled=True, kinds=["daily_briefing"])  # an ordinary change
    assert (await store.settings()).live is False


async def test_select_transport_is_live_only_while_on(store: NotifyStore) -> None:
    built: list[OwnerChat] = []

    def factory(owner: Any) -> Any:
        built.append(owner())
        return "live-transport"

    on = await enable_live(store, _telegram(), token=lambda: FAKE_TOKEN)
    assert select_transport(on, _telegram(), live_factory=factory) == "live-transport"
    assert built == [OwnerChat(OWNER)]
    off = await disable_live(store)
    assert isinstance(
        select_transport(off, _telegram(), live_factory=factory), SimulatedTelegramTransport
    )


# --- The one test message --------------------------------------------------------------


async def test_the_test_message_needs_the_switch(store: NotifyStore) -> None:
    api = BotApi()
    with pytest.raises(LiveDisabled):
        await send_connection_test(store, api.live(lambda: OwnerChat(OWNER)), text=TEXT, day=DAY)
    assert api.requests == []


async def test_exactly_one_message_to_the_owner_only(
    store: NotifyStore, caplog: pytest.LogCaptureFixture
) -> None:
    api = BotApi()
    await enable_live(store, _telegram(), token=lambda: FAKE_TOKEN)
    transport = select_transport(await store.settings(), _telegram(), live_factory=api.live)
    with caplog.at_level(logging.DEBUG):
        first = await send_connection_test(store, transport, text=TEXT, day=DAY)
        again = await send_connection_test(store, transport, text=TEXT, day=DAY)
    assert (first.status, again.status) == ("delivered", "duplicate")
    [request] = api.requests
    body = json.loads(request.content)
    assert body == {"chat_id": OWNER, "text": TEXT, "disable_web_page_preview": True}
    assert FAKE_TOKEN not in caplog.text and str(OWNER) not in caplog.text
    settings = await store.settings()
    assert settings.enabled is False  # the general opt-in was not touched


async def test_after_the_test_nothing_else_goes_out(store: NotifyStore) -> None:
    api = BotApi()
    await enable_live(store, _telegram(), token=lambda: FAKE_TOKEN)
    transport = select_transport(await store.settings(), _telegram(), live_factory=api.live)
    await send_connection_test(store, transport, text=TEXT, day=DAY)
    await disable_live(store)
    # The briefing path, even if something triggered it: opt-in off, simulated.
    after = select_transport(await store.settings(), _telegram(), live_factory=api.live)
    report = await OwnerNotifier(store, after).deliver(
        [Notification("daily_briefing", "briefing:2026-10-08", "Briefing")]
    )
    assert report.outcomes[0].status == "disabled"
    assert len(api.requests) == 1


async def test_a_mismatched_owner_never_receives_anything(store: NotifyStore) -> None:
    api = BotApi()
    await enable_live(store, _telegram(), token=lambda: FAKE_TOKEN)
    # The pairing changes after the switch went on: the transport re-checks.
    transport = select_transport(
        await store.settings(), _telegram(chat_id="777"), live_factory=api.live
    )
    outcome = await send_connection_test(store, transport, text=TEXT, day=DAY)
    assert (outcome.status, outcome.error) == ("gave_up", "chat_id_mismatch")
    assert api.requests == []


# --- Routes ---------------------------------------------------------------------------


@pytest.fixture
def app(store: NotifyStore, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, BotApi]:
    from jarvis.ui.web import ops_routes

    api = BotApi()
    monkeypatch.setattr(delivery, "TelegramBotTransport", lambda owner: api.live(owner))
    monkeypatch.setattr(delivery, "default_token", lambda: FAKE_TOKEN)
    application = FastAPI()
    application.include_router(ops_routes.router)
    application.state.ops_notify_store = store
    application.state.config = SimpleNamespace(integrations=SimpleNamespace(telegram=_telegram()))
    return application, api


async def _call(app: FastAPI, method: str, url: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, url, **kw)


async def test_routes_switch_on_send_once_switch_off(app: tuple[FastAPI, BotApi]) -> None:
    application, api = app
    off = await _call(application, "POST", "/api/ops/notify/telegram/test", json={"text": TEXT})
    assert off.status_code == 409 and off.json()["detail"] == "live_disabled"

    on = await _call(application, "PUT", "/api/ops/notify/live", json={"enabled": True})
    assert on.status_code == 200 and on.json()["live"] is True
    sent = await _call(application, "POST", "/api/ops/notify/telegram/test", json={"text": TEXT})
    again = await _call(application, "POST", "/api/ops/notify/telegram/test", json={"text": TEXT})
    assert (sent.json()["status"], again.json()["status"]) == ("delivered", "duplicate")
    assert len(api.requests) == 1
    assert FAKE_TOKEN not in sent.text and str(OWNER) not in sent.text

    back = await _call(application, "PUT", "/api/ops/notify/live", json={"enabled": False})
    assert back.json()["live"] is False and back.json()["transport"] == "simulated"


async def test_route_refuses_live_without_a_paired_owner(app: tuple[FastAPI, BotApi]) -> None:
    application, _api = app
    application.state.config = SimpleNamespace(integrations=SimpleNamespace(telegram=_telegram([])))
    res = await _call(application, "PUT", "/api/ops/notify/live", json={"enabled": True})
    assert res.status_code == 409 and res.json()["detail"] == "owner_not_paired"


async def test_the_test_route_is_marked_dangerous_and_validates_text() -> None:
    from jarvis.ui.web import ops_routes

    route = next(r for r in ops_routes.router.routes if r.path == "/api/ops/notify/telegram/test")
    assert route.openapi_extra == {"x-jarvis-dangerous": True}
    assert TEXT and len(TEXT) <= delivery.TEST_TEXT_MAX
