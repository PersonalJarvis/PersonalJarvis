"""The Slack pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: stable
per-thread and per-day ids (a re-run must upsert, not duplicate), a real deep
link on every item, chronological ``author: text`` rendering, per-scope
degradation instead of all-or-nothing failure, bounded 429 handling, and
failure messages a user can act on that never carry the token.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import slack as sl
from jarvis.ultrawiki.types import ConnectorContext

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Slack plugin, without touching the host's keyring."""

    class _Tokens:
        access = "xoxb-test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-slack",
        config={"integration_id": "plugin:slack"},
        secret_get=lambda _name: None,
    )


def _ts(iso: str, micro: str = "000100") -> str:
    moment = datetime.fromisoformat(iso).replace(tzinfo=UTC)
    return f"{int(moment.timestamp())}.{micro}"


def _msg(iso: str, user: str = "U1", text: str = "hello", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": "message", "ts": _ts(iso), "user": user, "text": text}
    base.update(overrides)
    return base


def _channel(cid: str = "C1", name: str = "general", created: int = 100, **overrides: Any):
    base: dict[str, Any] = {"id": cid, "name": name, "created": created}
    base.update(overrides)
    return base


_USERS = [
    {"id": "U1", "name": "alice", "profile": {"display_name": "Alice"}},
    {"id": "U2", "name": "bob", "profile": {"display_name": "Bob"}},
]


def _json(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def _transport(
    *,
    channels: dict[str, list[dict[str, Any]]] | None = None,
    history: dict[str, list[list[dict[str, Any]]]] | None = None,
    replies: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    users: list[dict[str, Any]] | None = None,
    auth: dict[str, Any] | None = None,
    missing_scope_types: frozenset[str] = frozenset(),
    history_errors: dict[str, str] | None = None,
    rate_limit_first_history: int = 0,
    uploads: dict[str, bytes] | None = None,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake Slack workspace: dispatches by Web API method path."""
    remaining_429 = {"n": rate_limit_first_history}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        params = request.url.params
        if path == "/api/auth.test":
            return _json(auth or {"ok": True, "url": "https://acme.slack.com/"})
        if path == "/api/users.list":
            return _json({"ok": True, "members": users if users is not None else _USERS})
        if path == "/api/conversations.list":
            conv_type = params.get("types") or ""
            if conv_type in missing_scope_types:
                return _json({"ok": False, "error": "missing_scope"})
            return _json({"ok": True, "channels": (channels or {}).get(conv_type, [])})
        if path == "/api/conversations.history":
            channel = params.get("channel") or ""
            if remaining_429["n"] > 0:
                remaining_429["n"] -= 1
                return httpx.Response(429, headers={"Retry-After": "0"})
            if channel in (history_errors or {}):
                return _json({"ok": False, "error": (history_errors or {})[channel]})
            pages = (history or {}).get(channel, [[]])
            index = int(params.get("cursor") or 0)
            page = pages[index] if index < len(pages) else []
            next_cursor = str(index + 1) if index + 1 < len(pages) else ""
            return _json(
                {
                    "ok": True,
                    "messages": page,
                    "has_more": bool(next_cursor),
                    "response_metadata": {"next_cursor": next_cursor},
                }
            )
        if path == "/api/conversations.replies":
            key = (params.get("channel") or "", params.get("ts") or "")
            return _json({"ok": True, "messages": (replies or {}).get(key, [])})
        if path.startswith("/files/"):
            raw = (uploads or {}).get(path.rsplit("/", 1)[1])
            if raw is None:
                return httpx.Response(404)
            return httpx.Response(200, content=raw)
        return _json({"ok": False, "error": "unknown_method"})

    return httpx.MockTransport(handler)


async def _collect(
    transport: httpx.MockTransport, checkpoint: str | None = None
) -> list:
    return [
        item
        async for item in sl.slack_pull_adapter(_ctx(), checkpoint, transport=transport)
    ]


async def test_a_thread_becomes_one_chronological_item():
    parent_ts = _ts("2026-03-01T10:00:00")
    parent = _msg(
        "2026-03-01T10:00:00",
        text="Shall we ship?",
        thread_ts=parent_ts,
        reply_count=2,
    )
    thread = [
        parent,
        _msg("2026-03-01T10:05:00", user="U2", text="Yes", thread_ts=parent_ts),
        _msg("2026-03-01T11:00:00", user="U1", text="Done", thread_ts=parent_ts),
    ]
    items = await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": [[parent]]},
            replies={("C1", parent_ts): thread},
        )
    )
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == f"C1:{parent_ts}"
    assert item.thread_key == "C1"
    # The canonical archive deep link, constructed instead of paying one
    # chat.getPermalink request per item.
    assert item.permalink == "https://acme.slack.com/archives/C1/p" + parent_ts.replace(".", "")
    assert item.timestamp_utc == "2026-03-01T10:00:00Z"
    # Parent and replies as chronological "author: text" lines.
    body = item.body
    assert body.index("Alice: Shall we ship?") < body.index("Bob: Yes") < body.index("Alice: Done")
    assert item.author_raw == "Alice"
    assert item.metadata["kind"] == "thread"
    assert item.metadata["mtime_ns"] == sl._to_ns(_ts("2026-03-01T11:00:00"))


async def test_plain_messages_group_into_one_item_per_day():
    history = [
        _msg("2026-03-01T10:00:00", text="first"),
        _msg("2026-03-01T11:00:00", user="U2", text="second"),
        _msg("2026-03-02T09:00:00", text="third"),
    ]
    items = await _collect(
        _transport(channels={"public_channel": [_channel()]}, history={"C1": [history]})
    )
    # One item per day, oldest first — deterministic order for the checkpoint.
    assert [i.external_id for i in items] == ["C1:2026-03-01", "C1:2026-03-02"]
    first = items[0]
    assert first.body.index("Alice: first") < first.body.index("Bob: second")
    assert first.timestamp_utc == "2026-03-01T10:00:00Z"
    assert first.thread_key == "C1"
    assert first.metadata["kind"] == "day"
    # The cursor rides on the key the sync runner advances.
    assert first.metadata["mtime_ns"] == sl._to_ns(_ts("2026-03-01T11:00:00"))


async def test_unknown_user_ids_fall_back_to_the_raw_id():
    history = [
        _msg("2026-03-01T10:00:00", text="<@U1> please look"),
        _msg("2026-03-01T11:00:00", user="U9", text="on it"),
    ]
    items = await _collect(
        _transport(channels={"public_channel": [_channel()]}, history={"C1": [history]})
    )
    body = items[0].body
    # Mentions resolve through the cached users.list lookup...
    assert "Alice: @Alice please look" in body
    # ...and an id the roster does not know stays visible as itself.
    assert "U9: on it" in body


async def test_a_type_the_token_cannot_list_degrades_instead_of_failing():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": [[_msg("2026-03-01T10:00:00")]]},
            missing_scope_types=frozenset({"private_channel", "mpim", "im"}),
            seen=seen,
        )
    )
    assert len(items) == 1  # the readable type still imported fully
    requested = {
        r.url.params.get("types")
        for r in seen
        if r.url.path == "/api/conversations.list"
    }
    # Every type the maintainer wants is at least ASKED for; scopes decide.
    assert requested == {"public_channel", "private_channel", "mpim", "im"}


async def test_an_unreadable_conversation_is_skipped_not_fatal():
    """A bot token lists public channels it was never invited into; their
    history answers not_in_channel and must not sink the readable ones."""
    items = await _collect(
        _transport(
            channels={"public_channel": [_channel(), _channel("C2", "private-ish", 200)]},
            history={"C1": [[_msg("2026-03-01T10:00:00")]]},
            history_errors={"C2": "not_in_channel"},
        )
    )
    assert [i.metadata["channel"] for i in items] == ["C1"]


async def test_rate_limited_requests_wait_and_retry():
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": [[_msg("2026-03-01T10:00:00")]]},
            rate_limit_first_history=1,
            seen=seen,
        )
    )
    assert len(items) == 1
    history_calls = [r for r in seen if r.url.path == "/api/conversations.history"]
    assert len(history_calls) == 2  # the 429, then the honoured retry


async def test_a_persistent_rate_limit_raises_an_honest_bounded_error():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(sl.SlackAdapterError, match="rate limit"):
        await _collect(httpx.MockTransport(handler))
    assert len(seen) == sl._MAX_ATTEMPTS  # bounded, never an endless retry loop


async def test_a_revoked_token_says_what_to_do_and_never_echoes_it():
    with pytest.raises(sl.SlackAdapterError) as excinfo:
        await _collect(_transport(auth={"ok": False, "error": "token_revoked"}))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "xoxb-test" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    seen: list[httpx.Request] = []
    with pytest.raises(sl.SlackAdapterError, match="not connected"):
        await _collect(_transport(seen=seen))
    assert seen == []  # refused before a single request left the machine


async def test_a_numeric_checkpoint_narrows_history_by_whole_days():
    moment = datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC)
    seen: list[httpx.Request] = []
    await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": [[]]},
            seen=seen,
        ),
        checkpoint=str(int(moment.timestamp() * 1_000_000_000)),
    )
    call = next(r for r in seen if r.url.path == "/api/conversations.history")
    # Start of the PREVIOUS UTC day: a mid-day cut would rebuild a half day
    # under the full day's id; the rewound overlap upserts as unchanged.
    expected = f"{int(datetime(2026, 3, 3, tzinfo=UTC).timestamp())}.000000"
    assert call.url.params.get("oldest") == expected


async def test_a_backfill_checkpoint_is_read_as_no_cursor():
    """The bridge passes the last item's external_id during a backfill; that
    is not a cursor, and mis-parsing it would silently skip history."""
    seen: list[httpx.Request] = []
    await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": [[]]},
            seen=seen,
        ),
        checkpoint="C1:2026-03-01",
    )
    call = next(r for r in seen if r.url.path == "/api/conversations.history")
    assert call.url.params.get("oldest") is None


async def test_an_oversized_item_is_capped_with_a_marker():
    huge = _msg("2026-03-01T10:00:00", text="x" * 1_100_000)
    items = await _collect(
        _transport(channels={"public_channel": [_channel()]}, history={"C1": [[huge]]})
    )
    body = items[0].body
    assert body.endswith("[truncated — this item exceeded the 1 MB import cap]")
    assert len(body.encode("utf-8")) < 1_100_000


async def test_join_and_leave_noise_is_not_memory():
    history = [
        _msg("2026-03-01T09:00:00", text="Alice has joined", subtype="channel_join"),
        _msg("2026-03-01T10:00:00", text="the real conversation"),
        _msg("2026-03-02T09:00:00", user="U2", text="Bob left", subtype="channel_leave"),
    ]
    items = await _collect(
        _transport(channels={"public_channel": [_channel()]}, history={"C1": [history]})
    )
    # A day holding only housekeeping produces no item at all.
    assert [i.external_id for i in items] == ["C1:2026-03-01"]
    assert "joined" not in items[0].body


async def test_history_pages_are_walked_to_the_end():
    pages = [
        [_msg("2026-03-01T10:00:00", text="from page one")],
        [_msg("2026-03-01T11:00:00", user="U2", text="from page two")],
    ]
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": pages},
            seen=seen,
        )
    )
    assert len(items) == 1
    assert "from page one" in items[0].body
    assert "from page two" in items[0].body
    history_calls = [r for r in seen if r.url.path == "/api/conversations.history"]
    assert len(history_calls) == 2


async def test_a_dm_is_labelled_as_a_conversation_with_its_person():
    items = await _collect(
        _transport(
            channels={"im": [{"id": "D1", "user": "U1", "is_im": True, "created": 5}]},
            history={"D1": [[_msg("2026-03-01T10:00:00", text="just between us")]]},
        )
    )
    assert len(items) == 1
    assert "DM with @Alice" in items[0].body
    assert items[0].thread_key == "D1"


def _docx(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f"<w:document xmlns:w='x'><w:body><w:p><w:r><w:t>{text}"
            "</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


def _upload(file_id: str, name: str, mimetype: str, size: int = 500) -> dict[str, Any]:
    return {
        "id": file_id,
        "name": name,
        "title": name,
        "mimetype": mimetype,
        "size": size,
        "url_private_download": f"https://files.slack.com/files/{file_id}",
        "permalink": f"https://acme.slack.com/files/{file_id}",
    }


async def test_a_shared_document_becomes_its_own_item():
    """Shared files are where a channel's decisions live — the deck, the spec.
    Recording only that "a file was shared" leaves the memory able to say a
    document exists and nothing about what it says."""
    upload = _upload("F1", "Roadmap.docx", _DOCX_MIME)
    message = _msg("2026-03-01T10:00:00", text="here you go", files=[upload])
    items = await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": [[message]]},
            uploads={"F1": _docx("Ship the importer in April")},
        )
    )
    file_items = [item for item in items if item.metadata.get("kind") == "file"]
    assert len(file_items) == 1
    assert file_items[0].external_id.endswith("#file-F1")
    assert "Ship the importer in April" in file_items[0].body
    assert file_items[0].thread_key == "C1"
    # The transcript still mentions it, so the conversation stays readable.
    day = next(item for item in items if item.metadata.get("kind") == "day")
    assert "[file: Roadmap.docx]" in day.body


async def test_a_shared_photo_is_never_downloaded():
    """No OCR ships here; one request per image would buy nothing."""
    upload = _upload("F2", "team.png", "image/png")
    message = _msg("2026-03-01T10:00:00", text="look", files=[upload])
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": [[message]]},
            seen=seen,
        )
    )
    assert not [item for item in items if item.metadata.get("kind") == "file"]
    assert all("/files/" not in request.url.path for request in seen)


async def test_an_unreachable_upload_does_not_sink_the_conversation():
    """The transcript must survive a dead file server; the file says why."""
    upload = _upload("F3", "notes.docx", _DOCX_MIME)
    message = _msg("2026-03-01T10:00:00", text="attached", files=[upload])
    items = await _collect(
        _transport(
            channels={"public_channel": [_channel()]},
            history={"C1": [[message]]},
            uploads={},
        )
    )
    assert any(item.metadata.get("kind") == "day" for item in items)
    file_item = next(item for item in items if item.metadata.get("kind") == "file")
    assert file_item.metadata["content_missing"] is True


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(sl.INTEGRATION_ID)
    assert spec is not None and spec.id == "slack"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(sl.INTEGRATION_ID, sl.slack_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:slack") is True
    finally:
        plugin_bridge.unregister_pull_adapter(sl.INTEGRATION_ID)
