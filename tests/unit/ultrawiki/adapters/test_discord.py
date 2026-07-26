"""The Discord pull adapter, driven fully offline through MockTransport.

What these tests pin: full history through pagination, day-chunk emission in
deterministic oldest-to-newest order, the checkpoint conventions (resume
strictly after an external_id; a numeric cursor becomes ``after=``), quiet
skipping of channels the bot may not read, bounded 429 handling, and failure
messages a user can act on that never carry the bot token.

The mock reproduces Discord's real pagination semantics — ``before``/``after``
windows over snowflake-ordered messages, newest first in every response — so
the adapter's ordering guarantees are earned, not assumed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import discord as dc
from jarvis.ultrawiki.types import ConnectorContext

_GUILD = {"id": "111", "name": "Acme HQ"}
_CHANNEL = {"id": "222", "name": "general", "type": 0}


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Discord plugin, without touching the host's keyring."""

    class _Tokens:
        access = "discord_bot_secret_value"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-discord",
        config={"integration_id": "plugin:discord"},
        secret_get=lambda _name: None,
    )


def _snowflake(iso: str, sequence: int = 0) -> str:
    """A realistic message id: creation time is encoded, as on real Discord.

    The adapter converts time cursors into snowflakes and the mock filters by
    id — both only line up when ids and timestamps agree, exactly as live.
    """
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    ms = int(moment.timestamp() * 1000)
    return str(((ms - 1_420_070_400_000) << 22) + sequence)


def _message(iso: str, text: str, *, author: str = "ruth", sequence: int = 0) -> dict:
    return {
        "id": _snowflake(iso, sequence),
        "content": text,
        "timestamp": iso.replace("Z", "+00:00"),
        "author": {"username": author},
        "attachments": [],
        "embeds": [],
    }


def _transport(
    channels: dict[str, list[dict[str, Any]]],
    *,
    guilds: list[dict[str, Any]] | None = None,
    guild_channels: list[dict[str, Any]] | None = None,
    forbidden: set[str] = frozenset(),
    uploads: dict[str, bytes] | None = None,
    seen: list[httpx.Request] | None = None,
    ratelimit_first: int = 0,
) -> httpx.MockTransport:
    """A faithful little Discord: snowflake windows, newest-first pages."""
    remaining_429 = {"n": ratelimit_first}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if request.url.host == "cdn.discordapp.com":
            raw = (uploads or {}).get(path.rsplit("/", 1)[1])
            if raw is None:
                return httpx.Response(404)
            return httpx.Response(200, content=raw)
        if path == "/api/v10/users/@me/guilds":
            if request.url.params.get("after"):
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=guilds if guilds is not None else [_GUILD])
        if path.startswith("/api/v10/guilds/") and path.endswith("/channels"):
            rows = guild_channels if guild_channels is not None else [_CHANNEL]
            return httpx.Response(200, json=rows)
        if path.startswith("/api/v10/channels/") and path.endswith("/messages"):
            channel_id = path.split("/")[4]
            if channel_id in forbidden:
                return httpx.Response(403, json={"message": "Missing Access", "code": 50001})
            if remaining_429["n"] > 0:
                remaining_429["n"] -= 1
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"retry_after": 0})
            messages = sorted(
                channels.get(channel_id, []), key=lambda m: int(m["id"])
            )
            params = request.url.params
            limit = int(params.get("limit", "50"))
            if "after" in params:
                window = [m for m in messages if int(m["id"]) > int(params["after"])]
                window = window[:limit]
            elif "before" in params:
                window = [m for m in messages if int(m["id"]) < int(params["before"])]
                window = window[-limit:]
            else:
                window = messages[-limit:]
            return httpx.Response(200, json=list(reversed(window)))  # newest first
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.MockTransport(handler)


async def _collect(checkpoint: str | None = None, **kwargs: Any) -> list:
    return [
        item
        async for item in dc.discord_pull_adapter(_ctx(), checkpoint, **kwargs)
    ]


async def test_a_channel_becomes_day_chunks_oldest_to_newest():
    transport = _transport(
        {
            "222": [
                _message("2026-03-02T09:00:00Z", "second day", sequence=1),
                _message("2026-03-01T10:00:00Z", "first day, first", sequence=1),
                _message("2026-03-01T11:30:00Z", "first day, second", sequence=2),
            ]
        }
    )
    items = await _collect(transport=transport)
    assert [i.external_id for i in items] == [
        "discord:111:222:2026-03-01",
        "discord:111:222:2026-03-02",
    ]
    first = items[0]
    # Evidence deep-links to the first message of the chunk.
    assert first.permalink == (
        f"https://discord.com/channels/111/222/{_snowflake('2026-03-01T10:00:00Z', 1)}"
    )
    assert first.timestamp_utc == "2026-03-01T10:00:00Z"
    assert first.thread_key == "111/222"
    assert "Acme HQ · #general · 2026-03-01 · 2 message(s)" in first.body
    assert "[10:00] ruth: first day, first" in first.body
    assert "[11:30] ruth: first day, second" in first.body
    assert first.metadata["message_count"] == 2


async def test_full_history_is_walked_through_every_page():
    day = "2026-03-01"
    messages = [
        _message(f"{day}T10:00:00Z", f"msg {i}", sequence=i) for i in range(250)
    ]
    seen: list[httpx.Request] = []
    items = await _collect(transport=_transport({"222": messages}, seen=seen))
    assert len(items) == 1
    assert items[0].metadata["message_count"] == 250
    assert "msg 0" in items[0].body
    assert "msg 249" in items[0].body
    history_calls = [r for r in seen if r.url.path.endswith("/messages")]
    assert len(history_calls) == 3  # 100 + 100 + 50


async def test_the_cursor_rides_on_the_key_the_sync_runner_advances():
    items = await _collect(
        transport=_transport({"222": [_message("2026-03-01T10:00:00Z", "hi", sequence=1)]})
    )
    assert items[0].metadata["mtime_ns"] == dc._to_ns("2026-03-01T10:00:00Z")
    assert items[0].metadata["mtime_ns"] > 0


async def test_a_numeric_checkpoint_walks_forward_from_that_day():
    """The runner's ns cursor becomes ``after=<snowflake at the day start>`` —
    re-asking from the boundary keeps day chunks complete."""
    messages = [
        _message("2026-03-01T10:00:00Z", "old", sequence=1),
        _message("2026-03-03T09:00:00Z", "new", sequence=1),
    ]
    seen: list[httpx.Request] = []
    items = await _collect(
        checkpoint=str(dc._to_ns("2026-03-03T12:00:00Z")),
        transport=_transport({"222": messages}, seen=seen),
    )
    history = next(r for r in seen if r.url.path.endswith("/messages"))
    assert "after" in history.url.params
    assert [i.external_id for i in items] == ["discord:111:222:2026-03-03"]


async def test_a_backfill_checkpoint_resumes_strictly_after_it():
    """The bridge hands back the last emitted external_id mid-walk; the same
    channel continues after that day and earlier channels are skipped."""
    older = {"id": "200", "name": "announcements", "type": 5}
    channels = [older, _CHANNEL]
    data = {
        "200": [_message("2026-02-01T08:00:00Z", "already imported", sequence=1)],
        "222": [
            _message("2026-03-01T10:00:00Z", "already imported too", sequence=1),
            _message("2026-03-02T10:00:00Z", "still missing", sequence=1),
        ],
    }
    items = await _collect(
        checkpoint="discord:111:222:2026-03-01",
        transport=_transport(data, guild_channels=channels),
    )
    assert [i.external_id for i in items] == ["discord:111:222:2026-03-02"]


async def test_unreadable_channels_are_skipped_not_fatal():
    channels = [
        {"id": "221", "name": "private-ops", "type": 0},
        _CHANNEL,
    ]
    data = {"222": [_message("2026-03-01T10:00:00Z", "visible", sequence=1)]}
    items = await _collect(
        transport=_transport(data, guild_channels=channels, forbidden={"221"})
    )
    assert [i.external_id for i in items] == ["discord:111:222:2026-03-01"]


async def test_voice_and_category_channels_are_not_walked():
    channels = [
        {"id": "300", "name": "Voice Lounge", "type": 2},
        {"id": "301", "name": "a-category", "type": 4},
        _CHANNEL,
    ]
    seen: list[httpx.Request] = []
    await _collect(
        transport=_transport(
            {"222": [_message("2026-03-01T10:00:00Z", "hi", sequence=1)]},
            guild_channels=channels,
            seen=seen,
        )
    )
    walked = {r.url.path.split("/")[4] for r in seen if r.url.path.endswith("/messages")}
    assert walked == {"222"}


async def test_attachments_are_named_never_downloaded():
    message = _message("2026-03-01T10:00:00Z", "", sequence=1)
    message["attachments"] = [
        {"filename": "report.pdf", "url": "https://cdn.discordapp.com/report.pdf"}
    ]
    seen: list[httpx.Request] = []
    items = await _collect(transport=_transport({"222": [message]}, seen=seen))
    assert "[attachment: report.pdf]" in items[0].body
    assert all("cdn.discordapp.com" not in str(r.url) for r in seen)


async def test_an_oversized_day_is_truncated_with_a_marker(monkeypatch):
    monkeypatch.setattr(dc, "_MAX_BODY_CHARS", 80)
    messages = [
        _message("2026-03-01T10:00:00Z", "x" * 60, sequence=i) for i in range(1, 4)
    ]
    items = await _collect(transport=_transport({"222": messages}))
    assert len(items) == 1
    assert items[0].body.endswith(dc._TRUNCATION_MARKER)
    assert items[0].metadata["truncated"] is True


async def test_a_429_is_retried_within_bounds_and_then_succeeds():
    seen: list[httpx.Request] = []
    items = await _collect(
        transport=_transport(
            {"222": [_message("2026-03-01T10:00:00Z", "hi", sequence=1)]},
            seen=seen,
            ratelimit_first=1,
        )
    )
    assert len(items) == 1
    history_calls = [r for r in seen if r.url.path.endswith("/messages")]
    assert len(history_calls) == 2  # the 429 plus the successful retry


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "401: Unauthorized"})

    with pytest.raises(dc.DiscordAdapterError) as excinfo:
        await _collect(transport=httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "discord_bot_secret_value" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(dc.DiscordAdapterError, match="not connected"):
        await _collect(transport=_transport({}))


def _attachment(
    attachment_id: str, filename: str, content_type: str, size: int = 400
) -> dict[str, Any]:
    return {
        "id": attachment_id,
        "filename": filename,
        "content_type": content_type,
        "size": size,
        "url": f"https://cdn.discordapp.com/attachments/{attachment_id}",
    }


async def test_a_posted_document_becomes_its_own_item():
    """A posted spec is the substance of the conversation around it."""
    message = _message("2026-03-01T10:00:00Z", "here is the spec")
    message["attachments"] = [_attachment("A1", "spec.md", "text/markdown")]
    items = await _collect(
        transport=_transport(
            {"222": [message]},
            uploads={"A1": b"# Spec\n\nThe importer reads everything."},
        )
    )
    files = [item for item in items if item.metadata.get("kind") == "attachment"]
    assert len(files) == 1
    assert "The importer reads everything." in files[0].body
    assert files[0].external_id.endswith("#att-A1")
    # The transcript still names it.
    assert "[attachment: spec.md]" in items[0].body


async def test_a_posted_image_is_never_downloaded():
    """A meme-heavy channel must not cost one download per picture."""
    message = _message("2026-03-01T10:00:00Z", "look")
    message["attachments"] = [_attachment("A2", "cat.png", "image/png")]
    seen: list[httpx.Request] = []
    items = await _collect(transport=_transport({"222": [message]}, seen=seen))
    assert not [item for item in items if item.metadata.get("kind") == "attachment"]
    assert all(request.url.host != "cdn.discordapp.com" for request in seen)


async def test_a_dead_link_costs_that_file_only():
    message = _message("2026-03-01T10:00:00Z", "attached")
    message["attachments"] = [_attachment("A3", "notes.md", "text/markdown")]
    items = await _collect(transport=_transport({"222": [message]}, uploads={}))
    # The day chunk survived; the file says why it is empty.
    assert len(items) == 2
    assert items[1].metadata["content_missing"] is True


def test_the_integration_id_matches_the_curated_roster():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    # The id must resolve to the roster's Discord card, or the reader serves
    # a name nothing offers.
    spec = connector_catalog.bridge_entry_for(dc.INTEGRATION_ID)
    assert spec is not None
    assert spec.id == "discord"

    plugin_bridge.register_pull_adapter(dc.INTEGRATION_ID, dc.discord_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:discord") is True
    finally:
        plugin_bridge.unregister_pull_adapter(dc.INTEGRATION_ID)
