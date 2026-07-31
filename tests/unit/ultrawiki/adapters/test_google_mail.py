"""The Gmail pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: a stable
external id per message with a real deep link, a deterministic oldest-to-newest
walk so the checkpoint convention holds, text/plain preferred over stripped
HTML with the snippet as the honest floor, attachments counted but never
downloaded, and failure messages a user can act on that never carry the token.
"""

from __future__ import annotations

import base64
import io
import zipfile
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import _google as g
from jarvis.ultrawiki.adapters import google_mail as gm
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Gmail plugin, without touching the host's keyring."""

    class _Tokens:
        access = "ya29.test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-gmail",
        config={"integration_id": "plugin:gmail"},
        secret_get=lambda _name: None,
    )


def _b64(text: str) -> str:
    """Gmail-shaped base64url: unpadded."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


def _message(
    message_id: str,
    iso: str,
    *,
    subject: str = "Quarterly plan",
    sender: str = "Ada <ada@example.test>",
    to: str = "me@example.test",
    plain: str | None = "plain body",
    html: str | None = None,
    attachments: int = 0,
    snippet: str = "",
    thread: str = "",
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if plain is not None:
        parts.append(
            {"mimeType": "text/plain", "filename": "", "body": {"data": _b64(plain)}}
        )
    if html is not None:
        parts.append(
            {"mimeType": "text/html", "filename": "", "body": {"data": _b64(html)}}
        )
    for i in range(attachments):
        parts.append(
            {
                "mimeType": "application/pdf",
                "filename": f"report-{i}.pdf",
                "body": {"attachmentId": f"att-{i}", "size": 12345},
            }
        )
    return {
        "id": message_id,
        "threadId": thread or f"t-{message_id}",
        "internalDate": str(_ms(iso)),
        "snippet": snippet,
        "historyId": "4711",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
            ],
            "parts": parts,
        },
    }


def _transport(
    *,
    listing: list[list[dict[str, Any]]],
    messages: dict[str, dict[str, Any]],
    attachments: dict[str, bytes] | None = None,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake Gmail: a paged newest-first id listing plus per-id full fetches.

    ``attachments`` maps an attachment id to its RAW bytes; an id that is
    absent answers 404, which is the "deleted between listing and fetch" case
    every walk has to survive.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if "/attachments/" in path:
            raw = (attachments or {}).get(path.rsplit("/", 1)[1])
            if raw is None:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "data": base64.urlsafe_b64encode(raw).decode().rstrip("="),
                    "size": len(raw),
                },
            )
        if path == "/gmail/v1/users/me/messages":
            index = int(request.url.params.get("pageToken") or 0)
            page = listing[index] if index < len(listing) else []
            payload: dict[str, Any] = {
                "messages": [{"id": row["id"], "threadId": row["threadId"]} for row in page]
            }
            if index + 1 < len(listing):
                payload["nextPageToken"] = str(index + 1)
            return httpx.Response(200, json=payload)
        if path.startswith("/gmail/v1/users/me/messages/"):
            body = messages.get(path.rsplit("/", 1)[1])
            if body is None:
                return httpx.Response(404)
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _collect(transport: httpx.MockTransport, checkpoint: str | None = None) -> list:
    return [
        item
        async for item in gm.gmail_pull_adapter(_ctx(), checkpoint, transport=transport)
    ]


async def test_messages_arrive_oldest_first_with_stable_ids_and_deep_links():
    older = _message("m1", "2026-03-01T10:00:00")
    newer = _message("m2", "2026-03-02T10:00:00")
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(listing=[[newer, older]], messages={"m1": older, "m2": newer}, seen=seen)
    )
    # The API lists newest first; the walk must still be oldest to newest so
    # the checkpoint convention (resume strictly after) holds.
    assert [item.external_id for item in items] == ["m1", "m2"]
    first = items[0]
    assert first.permalink == "https://mail.google.com/mail/u/0/#all/m1"
    assert first.title == "Quarterly plan"
    assert first.timestamp_utc == "2026-03-01T10:00:00Z"
    assert first.thread_key == "t-m1"
    assert first.author_raw == "Ada <ada@example.test>"
    assert "from Ada <ada@example.test>" in first.body
    assert "to me@example.test" in first.body
    # The cursor rides on the key the sync runner advances.
    assert first.metadata["mtime_ns"] == _ms("2026-03-01T10:00:00") * 1_000_000
    # Bodies come from ONE format=full fetch per message.
    fetch = next(r for r in seen if r.url.path.endswith("/messages/m1"))
    assert fetch.url.params.get("format") == "full"


async def test_the_plain_text_part_is_preferred_over_html():
    message = _message(
        "m1", "2026-03-01T10:00:00", plain="the plain truth", html="<p>the html copy</p>"
    )
    items = await _collect(_transport(listing=[[message]], messages={"m1": message}))
    assert "the plain truth" in items[0].body
    assert "html copy" not in items[0].body


async def test_an_html_only_message_is_stripped_to_readable_text():
    message = _message(
        "m1",
        "2026-03-01T10:00:00",
        plain=None,
        html="<html><body><p>First&nbsp;line</p><p>Second <b>line</b></p></body></html>",
    )
    items = await _collect(_transport(listing=[[message]], messages={"m1": message}))
    body = items[0].body
    assert "First line" in body
    assert "Second line" in body
    # No markup survives; the "<" in the header is the sender's address.
    assert "<p>" not in body
    assert "<b>" not in body


async def test_a_message_with_no_decodable_part_falls_back_to_the_snippet():
    message = _message(
        "m1", "2026-03-01T10:00:00", plain=None, snippet="the short preview"
    )
    items = await _collect(_transport(listing=[[message]], messages={"m1": message}))
    assert "the short preview" in items[0].body


def _docx(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f"<w:document xmlns:w='x'><w:body><w:p><w:r><w:t>{text}"
            "</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


async def test_the_message_names_its_attachments():
    """A count is unsearchable. The name is how a person looks for the thing."""
    message = _message("m1", "2026-03-01T10:00:00", attachments=2)
    items = await _collect(
        _transport(
            listing=[[message]],
            messages={"m1": message},
            attachments={"att-0": _docx("first"), "att-1": _docx("second")},
        )
    )
    assert "Attachments: report-0.pdf, report-1.pdf" in items[0].body
    assert items[0].metadata["attachments"] == 2


async def test_a_readable_attachment_becomes_its_own_item():
    """Where the invoice actually lives. A mail body says "see attached", so a
    memory that stops at the body knows only that something was attached."""
    message = _message("m1", "2026-03-01T10:00:00", attachments=1)
    message["payload"]["parts"][-1]["filename"] = "Invoice 2026-03.docx"
    items = await _collect(
        _transport(
            listing=[[message]],
            messages={"m1": message},
            attachments={"att-0": _docx("Amount due: 1200 EUR")},
        )
    )
    assert [item.external_id for item in items] == ["m1", "m1#att-1"]
    child = items[1]
    assert child.title == "Invoice 2026-03.docx"
    assert "Amount due: 1200 EUR" in child.body
    # It stays attached to the conversation it arrived in.
    assert child.thread_key == items[0].thread_key
    assert child.timestamp_utc == items[0].timestamp_utc
    assert child.metadata["parent_external_id"] == "m1"


async def test_a_photo_attachment_never_costs_a_request():
    """No OCR ships here, so fetching an image would spend a request per
    message to import nothing."""
    message = _message("m1", "2026-03-01T10:00:00", attachments=1)
    part = message["payload"]["parts"][-1]
    part["filename"] = "signature.png"
    part["mimeType"] = "image/png"
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(listing=[[message]], messages={"m1": message}, seen=seen)
    )
    assert [item.external_id for item in items] == ["m1"]
    assert all("/attachments/" not in request.url.path for request in seen)
    # It is still NAMED in the message, so nothing is hidden.
    assert "signature.png" in items[0].body


async def test_an_attachment_that_cannot_be_fetched_does_not_sink_the_sync():
    """One file deleted between listing and fetch must cost that file only —
    and it still becomes an item saying so."""
    message = _message("m1", "2026-03-01T10:00:00", attachments=1)
    items = await _collect(
        _transport(listing=[[message]], messages={"m1": message}, attachments={})
    )
    assert [item.external_id for item in items] == ["m1", "m1#att-1"]
    assert items[1].metadata["content_missing"] is True


async def test_listing_pages_are_walked_to_the_end():
    first = _message("m1", "2026-03-01T10:00:00")
    second = _message("m2", "2026-03-02T10:00:00")
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            listing=[[second], [first]],
            messages={"m1": first, "m2": second},
            seen=seen,
        )
    )
    assert [item.external_id for item in items] == ["m1", "m2"]
    list_calls = [r for r in seen if r.url.path == "/gmail/v1/users/me/messages"]
    assert len(list_calls) == 2


async def test_a_numeric_checkpoint_narrows_the_listing_by_a_day():
    ns = g.to_ns("2026-03-04T12:00:00Z")
    seen: list[httpx.Request] = []
    await _collect(_transport(listing=[[]], messages={}, seen=seen), checkpoint=str(ns))
    call = next(r for r in seen if r.url.path == "/gmail/v1/users/me/messages")
    # One day earlier, so a boundary can never skip a message; the rewound
    # overlap upserts as unchanged.
    expected = int(datetime(2026, 3, 3, 12, 0, 0, tzinfo=UTC).timestamp())
    assert call.url.params.get("q") == f"after:{expected}"


async def test_a_backfill_checkpoint_resumes_strictly_after_the_message_id():
    rows = [_message(f"m{i}", f"2026-03-0{i}T10:00:00") for i in (1, 2, 3)]
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            listing=[[rows[2], rows[1], rows[0]]],
            messages={row["id"]: row for row in rows},
            seen=seen,
        ),
        checkpoint="m2",
    )
    assert [item.external_id for item in items] == ["m3"]
    fetched = {r.url.path.rsplit("/", 1)[1] for r in seen if "/messages/" in r.url.path}
    # Already-imported messages are never body-fetched again.
    assert fetched == {"m3"}


async def test_a_vanished_checkpoint_id_degrades_to_a_full_walk():
    rows = [_message(f"m{i}", f"2026-03-0{i}T10:00:00") for i in (1, 2)]
    items = await _collect(
        _transport(
            listing=[[rows[1], rows[0]]], messages={row["id"]: row for row in rows}
        ),
        checkpoint="deleted-id",
    )
    # Silently skipping the whole mailbox would be the real bug.
    assert [item.external_id for item in items] == ["m1", "m2"]


async def test_an_oversized_body_is_capped_and_marked():
    message = _message("m1", "2026-03-01T10:00:00", plain="x" * (g.BODY_CAP + 10))
    items = await _collect(_transport(listing=[[message]], messages={"m1": message}))
    assert items[0].body.endswith(g.TRUNCATION_MARKER)
    assert items[0].metadata["truncated"] is True


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    seen: list[httpx.Request] = []
    with pytest.raises(gm.GoogleAdapterError, match="not connected"):
        await _collect(_transport(listing=[[]], messages={}, seen=seen))
    assert seen == []  # refused before a single request left the machine


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(gm.INTEGRATION_ID)
    assert spec is not None and spec.id == "gmail"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(gm.INTEGRATION_ID, gm.gmail_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:gmail") is True
    finally:
        plugin_bridge.unregister_pull_adapter(gm.INTEGRATION_ID)
