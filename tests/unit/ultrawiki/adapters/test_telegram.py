"""The Telegram pull adapter, driven fully offline through MockTransport.

What these tests pin: forward capture of the update queue in ascending
update-id order, drain-through pagination, the runner's numeric cursor
becoming ``offset=cursor+1``, honest degradation of a non-numeric backfill
checkpoint to "whatever Telegram still holds", media as named placeholders
that are never downloaded, bounded 429 handling, the single-reader 409
surfaced plainly, and failure messages that never echo the bot credential
even though the Bot API embeds it in every URL.

The mock reproduces the Bot API's real ``getUpdates`` semantics — offset
filtering over ascending update ids, at most ``limit`` per page — so the
adapter's ordering and drain guarantees are earned, not assumed.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import telegram as tg
from jarvis.ultrawiki.types import ConnectorContext

_BOT_CREDENTIAL = "7000000001:AAE-telegram-bot-credential-value"

_PRIVATE = {"id": 4242, "type": "private", "first_name": "Ruth"}
_SUPERGROUP = {"id": -100123456789, "type": "supergroup", "title": "Acme HQ"}

#: 2026-02-25T06:13:20Z — pinned as a literal so the ISO mapping is tested,
#: not merely round-tripped through the adapter's own helper.
_DATE = 1_772_000_000
_DATE_ISO = "2026-02-25T06:13:20Z"


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Telegram plugin, without touching the host's keyring."""

    class _Tokens:
        access = _BOT_CREDENTIAL
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-telegram",
        config={"integration_id": "plugin:telegram"},
        secret_get=lambda _name: None,
    )


def _update(
    update_id: int,
    text: str = "",
    *,
    chat: dict[str, Any] | None = None,
    date: int = _DATE,
    key: str = "message",
    message_id: int | None = None,
    sender: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": message_id if message_id is not None else update_id,
        "chat": dict(chat if chat is not None else _PRIVATE),
        "date": date,
    }
    if sender is not None:
        message["from"] = sender
    elif key in ("message", "edited_message"):
        message["from"] = {"first_name": "Ruth", "username": "ruth"}
    if text:
        message["text"] = text
    if extra:
        message.update(extra)
    return {"update_id": update_id, key: message}


def _transport(
    updates: list[dict[str, Any]],
    *,
    seen: list[httpx.Request] | None = None,
    ratelimit_first: int = 0,
) -> httpx.MockTransport:
    """A faithful little Bot API: offset windows over ascending update ids."""
    remaining_429 = {"n": ratelimit_first}
    ordered = sorted(updates, key=lambda u: int(u["update_id"]))

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 1, "is_bot": True}}
            )
        if method == "getUpdates":
            if remaining_429["n"] > 0:
                remaining_429["n"] -= 1
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0"},
                    json={"ok": False, "parameters": {"retry_after": 0}},
                )
            params = request.url.params
            offset = int(params.get("offset", "0") or 0)
            limit = int(params.get("limit", "100"))
            window = [u for u in ordered if int(u["update_id"]) >= offset][:limit]
            return httpx.Response(200, json={"ok": True, "result": window})
        return httpx.Response(404, json={"ok": False, "description": "Not Found"})

    return httpx.MockTransport(handler)


async def _collect(checkpoint: str | None = None, **kwargs: Any) -> list:
    return [
        item
        async for item in tg.telegram_pull_adapter(_ctx(), checkpoint, **kwargs)
    ]


async def test_updates_become_items_oldest_to_newest():
    items = await _collect(
        transport=_transport(
            [
                _update(9, "third"),
                _update(7, "first"),
                _update(8, "second"),
            ]
        )
    )
    assert [i.external_id for i in items] == ["msg:4242:7", "msg:4242:8", "msg:4242:9"]
    first = items[0]
    assert first.title == "Ruth in Ruth"
    assert first.body == "Ruth · Ruth\n\nfirst"
    # A private chat has no web URL; the app link is the honest deepest link.
    assert first.permalink == "tg://openmessage?user_id=4242&message_id=7"
    assert first.timestamp_utc == _DATE_ISO
    assert first.thread_key == "chat:4242"
    assert first.author_raw == "Ruth"


async def test_the_queue_is_drained_through_every_page():
    updates = [_update(i, f"msg {i}") for i in range(1, 151)]
    seen: list[httpx.Request] = []
    items = await _collect(transport=_transport(updates, seen=seen))
    assert len(items) == 150
    assert items[0].external_id == "msg:4242:1"
    assert items[-1].external_id == "msg:4242:150"
    pages = [r for r in seen if r.url.path.endswith("/getUpdates")]
    assert len(pages) == 2  # 100 + 50
    assert pages[1].url.params.get("offset") == "101"


async def test_the_cursor_rides_on_the_key_the_sync_runner_advances():
    items = await _collect(transport=_transport([_update(41, "hi")]))
    assert items[0].metadata["max_rowid"] == 41


async def test_a_numeric_checkpoint_continues_strictly_after_it():
    updates = [_update(5, "old"), _update(6, "newer"), _update(7, "newest")]
    seen: list[httpx.Request] = []
    items = await _collect(checkpoint="5", transport=_transport(updates, seen=seen))
    first_page = next(r for r in seen if r.url.path.endswith("/getUpdates"))
    assert first_page.url.params.get("offset") == "6"
    assert [i.external_id for i in items] == ["msg:4242:6", "msg:4242:7"]


async def test_a_backfill_checkpoint_degrades_to_the_unconfirmed_remainder():
    """A resume external_id is not a position in a replayable stream — the
    stream is gone. The adapter reads whatever Telegram still holds, which IS
    the un-imported remainder; upserts absorb any overlap."""
    seen: list[httpx.Request] = []
    items = await _collect(
        checkpoint="msg:4242:7",
        transport=_transport([_update(12, "still queued")], seen=seen),
    )
    first_page = next(r for r in seen if r.url.path.endswith("/getUpdates"))
    assert "offset" not in first_page.url.params
    assert [i.external_id for i in items] == ["msg:4242:12"]


async def test_group_and_channel_posts_carry_real_permalinks():
    channel = {
        "id": -1009876543210,
        "type": "channel",
        "title": "Announcements",
        "username": "acme_announce",
    }
    items = await _collect(
        transport=_transport(
            [
                _update(20, "release is out", key="channel_post", chat=channel),
                _update(21, "group talk", chat=_SUPERGROUP),
            ]
        )
    )
    by_id = {i.external_id: i for i in items}
    public = by_id["msg:-1009876543210:20"]
    assert public.permalink == "https://t.me/acme_announce/20"
    assert public.author_raw == "Announcements"  # channel posts carry no sender
    private_group = by_id["msg:-100123456789:21"]
    assert private_group.permalink == "https://t.me/c/123456789/21"
    assert "Acme HQ" in private_group.body


async def test_media_is_named_never_downloaded():
    seen: list[httpx.Request] = []
    items = await _collect(
        transport=_transport(
            [
                _update(
                    30,
                    extra={
                        "photo": [{"file_id": "p1"}],
                        "caption": "the whiteboard",
                    },
                ),
                _update(
                    31,
                    extra={"document": {"file_id": "d1", "file_name": "report.pdf"}},
                ),
            ],
            seen=seen,
        )
    )
    assert "the whiteboard [photo]" in items[0].body
    assert "[file: report.pdf]" in items[1].body
    # Only Bot-API method calls — never a file download endpoint.
    assert all("/file/" not in r.url.path for r in seen)


async def test_an_edit_re_yields_the_same_external_id():
    items = await _collect(
        transport=_transport(
            [
                _update(40, "first wording", message_id=7),
                _update(41, "final wording", message_id=7, key="edited_message"),
            ]
        )
    )
    assert [i.external_id for i in items] == ["msg:4242:7", "msg:4242:7"]
    assert items[0].metadata["edited"] is False
    assert items[1].metadata["edited"] is True
    assert "final wording" in items[1].body


async def test_service_updates_yield_nothing():
    items = await _collect(
        transport=_transport(
            [
                _update(50, extra={"sticker": {"file_id": "s1"}}),
                _update(51, extra={"new_chat_members": [{"id": 2}]}),
                {"update_id": 52, "callback_query": {"id": "cq", "data": "x"}},
            ]
        )
    )
    assert items == []


async def test_a_429_is_retried_within_bounds_and_then_succeeds():
    seen: list[httpx.Request] = []
    items = await _collect(
        transport=_transport([_update(60, "hi")], seen=seen, ratelimit_first=1)
    )
    assert len(items) == 1
    pages = [r for r in seen if r.url.path.endswith("/getUpdates")]
    assert len(pages) == 2  # the 429 plus the successful retry


async def test_the_single_reader_conflict_is_surfaced_plainly():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 1, "is_bot": True}}
            )
        return httpx.Response(
            409,
            json={
                "ok": False,
                "description": "Conflict: terminated by other getUpdates request",
            },
        )

    with pytest.raises(tg.TelegramAdapterError, match="one reader"):
        await _collect(transport=httpx.MockTransport(handler))


async def test_a_rejected_credential_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    with pytest.raises(tg.TelegramAdapterError) as excinfo:
        await _collect(transport=httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert _BOT_CREDENTIAL not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(tg.TelegramAdapterError, match="not connected"):
        await _collect(transport=_transport([]))


async def test_an_oversized_message_is_truncated_with_a_marker(monkeypatch):
    monkeypatch.setattr(tg, "_MAX_BODY_CHARS", 60)
    items = await _collect(transport=_transport([_update(70, "x" * 500)]))
    assert len(items) == 1
    assert items[0].body.endswith(tg._TRUNCATION_MARKER)
    assert len(items[0].body) == 60 + len(tg._TRUNCATION_MARKER)


def test_the_integration_id_matches_the_curated_roster():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    # The id must resolve to the roster's Telegram card, or the reader serves
    # a name nothing offers.
    spec = connector_catalog.bridge_entry_for(tg.INTEGRATION_ID)
    assert spec is not None
    assert spec.id == "telegram"

    plugin_bridge.register_pull_adapter(tg.INTEGRATION_ID, tg.telegram_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:telegram") is True
    finally:
        plugin_bridge.unregister_pull_adapter(tg.INTEGRATION_ID)
