"""The Google Drive pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: Google-native
documents exported to text, text-shaped files downloaded with a source-side
byte bound, binaries skipped honestly instead of swallowed, a deterministic
oldest-modified-first walk so the checkpoint convention holds, per-file
degradation instead of all-or-nothing failure, and refusals that never carry
the token.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import _google as g
from jarvis.ultrawiki.adapters import google_drive as gd
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Google Drive plugin, without touching the host's keyring."""

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
        source_id="src-gdrive",
        config={"integration_id": "plugin:google_drive"},
        secret_get=lambda _name: None,
    )


_DOC = "application/vnd.google-apps.document"
_SHEET = "application/vnd.google-apps.spreadsheet"
_FOLDER = "application/vnd.google-apps.folder"


def _file(
    file_id: str,
    name: str,
    mime: str,
    *,
    size: int = 0,
    created: str = "2026-01-01T10:00:00Z",
    modified: str = "2026-02-01T10:00:00Z",
    description: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "createdTime": created,
        "modifiedTime": modified,
        "webViewLink": f"https://drive.google.com/x/{file_id}/view",
        "owners": [{"displayName": "Ada", "emailAddress": "ada@example.test"}],
    }
    if size:
        row["size"] = str(size)
    if description:
        row["description"] = description
    return row


def _transport(
    *,
    listing: list[list[dict[str, Any]]],
    exports: dict[str, str] | None = None,
    contents: dict[str, str] | None = None,
    export_status: dict[str, int] | None = None,
    download_status: dict[str, int] | None = None,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake Drive API: a paged listing plus per-file export/download."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/drive/v3/files":
            index = int(request.url.params.get("pageToken") or 0)
            page = listing[index] if index < len(listing) else []
            payload: dict[str, Any] = {"files": page}
            if index + 1 < len(listing):
                payload["nextPageToken"] = str(index + 1)
            return httpx.Response(200, json=payload)
        if path.endswith("/export"):
            file_id = path.split("/files/")[1].rsplit("/", 1)[0]
            status = (export_status or {}).get(file_id, 200)
            if status != 200:
                return httpx.Response(status)
            return httpx.Response(200, text=(exports or {}).get(file_id, ""))
        if path.startswith("/drive/v3/files/"):
            file_id = path.rsplit("/", 1)[1]
            status = (download_status or {}).get(file_id, 200)
            if status not in (200, 206):
                return httpx.Response(status)
            return httpx.Response(status, text=(contents or {}).get(file_id, ""))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _collect(transport: httpx.MockTransport, checkpoint: str | None = None) -> list:
    return [
        item
        async for item in gd.google_drive_pull_adapter(
            _ctx(), checkpoint, transport=transport
        )
    ]


async def test_a_google_doc_is_exported_to_plain_text():
    doc = _file("f1", "Design notes", _DOC)
    items = await _collect(
        _transport(listing=[[doc]], exports={"f1": "The exported document text."})
    )
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "f1"
    assert item.title == "Design notes"
    assert item.permalink == "https://drive.google.com/x/f1/view"
    assert item.timestamp_utc == "2026-01-01T10:00:00Z"
    assert item.author_raw == "Ada"
    assert "The exported document text." in item.body
    assert f"Google Drive · Design notes · {_DOC}" in item.body
    # The cursor rides on the key the sync runner advances.
    assert item.metadata["mtime_ns"] == g.to_ns("2026-02-01T10:00:00Z")


async def test_a_spreadsheet_exports_as_csv_and_a_text_file_downloads_bounded():
    sheet = _file("f1", "Budget", _SHEET)
    notes = _file("f2", "notes.md", "text/markdown", size=100)
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(
            listing=[[sheet, notes]],
            exports={"f1": "a,b\n1,2"},
            contents={"f2": "# Notes"},
            seen=seen,
        )
    )
    assert [item.external_id for item in items] == ["f1", "f2"]
    export_call = next(r for r in seen if r.url.path.endswith("/export"))
    assert export_call.url.params.get("mimeType") == "text/csv"
    download_call = next(
        r for r in seen if r.url.params.get("alt") == "media"
    )
    # Bounded at the source: an oversized file never crosses the wire whole.
    assert download_call.headers.get("Range") == f"bytes=0-{g.BODY_CAP - 1}"


async def test_binaries_folders_and_shortcuts_are_skipped_honestly():
    listing = [
        [
            _file("f1", "photo.png", "image/png"),
            _file("f2", "archive.zip", "application/zip"),
            _file("f3", "Projects", _FOLDER),
            _file("f4", "scan.pdf", "application/pdf"),
            _file("f5", "notes.txt", "text/plain"),
        ]
    ]
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(listing=listing, contents={"f5": "readable"}, seen=seen)
    )
    # Only the text file becomes an item; nothing binary was even requested.
    assert [item.external_id for item in items] == ["f5"]
    fetched = {
        r.url.path.rsplit("/", 1)[1] for r in seen if r.url.params.get("alt") == "media"
    }
    assert fetched == {"f5"}


async def test_listing_pages_are_walked_to_the_end():
    pages = [
        [_file("f1", "one.txt", "text/plain")],
        [_file("f2", "two.txt", "text/plain")],
    ]
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(listing=pages, contents={"f1": "one", "f2": "two"}, seen=seen)
    )
    assert [item.external_id for item in items] == ["f1", "f2"]
    list_calls = [r for r in seen if r.url.path == "/drive/v3/files"]
    assert len(list_calls) == 2


async def test_the_listing_is_non_trashed_and_ordered_by_modified_time():
    seen: list[httpx.Request] = []
    await _collect(_transport(listing=[[]], seen=seen))
    call = next(r for r in seen if r.url.path == "/drive/v3/files")
    assert call.url.params.get("q") == "trashed = false"
    assert call.url.params.get("orderBy") == "modifiedTime"


async def test_a_numeric_checkpoint_narrows_the_query_by_a_day():
    ns = g.to_ns("2026-03-04T12:00:00Z")
    seen: list[httpx.Request] = []
    await _collect(_transport(listing=[[]], seen=seen), checkpoint=str(ns))
    call = next(r for r in seen if r.url.path == "/drive/v3/files")
    # One day earlier, so a boundary can never skip a file; the rewound
    # overlap upserts as unchanged.
    assert call.url.params.get("q") == (
        "trashed = false and modifiedTime > '2026-03-03T12:00:00Z'"
    )


async def test_a_backfill_checkpoint_resumes_strictly_after_the_file_id():
    listing = [
        [
            _file("f1", "one.txt", "text/plain", modified="2026-02-01T10:00:00Z"),
            _file("f2", "two.txt", "text/plain", modified="2026-02-02T10:00:00Z"),
            _file("f3", "three.txt", "text/plain", modified="2026-02-03T10:00:00Z"),
        ]
    ]
    seen: list[httpx.Request] = []
    items = await _collect(
        _transport(listing=listing, contents={"f3": "three"}, seen=seen),
        checkpoint="f2",
    )
    assert [item.external_id for item in items] == ["f3"]
    fetched = {
        r.url.path.rsplit("/", 1)[1] for r in seen if r.url.params.get("alt") == "media"
    }
    # Files before the resume point are never content-fetched again.
    assert fetched == {"f3"}


async def test_a_vanished_checkpoint_id_degrades_to_a_full_walk():
    listing = [
        [
            _file("f1", "one.txt", "text/plain"),
            _file("f2", "two.txt", "text/plain"),
        ]
    ]
    items = await _collect(
        _transport(listing=listing, contents={"f1": "one", "f2": "two"}),
        checkpoint="trashed-file-id",
    )
    # Silently skipping the whole Drive would be the real bug.
    assert [item.external_id for item in items] == ["f1", "f2"]


async def test_a_failed_export_degrades_to_metadata_with_an_honest_note():
    doc = _file("f1", "Locked doc", _DOC)
    ok = _file("f2", "notes.txt", "text/plain")
    items = await _collect(
        _transport(
            listing=[[doc, ok]],
            export_status={"f1": 403},
            contents={"f2": "still imported"},
        )
    )
    # One stubborn file must never sink the whole walk.
    assert [item.external_id for item in items] == ["f1", "f2"]
    assert "[content could not be exported (HTTP 403)" in items[0].body
    assert "still imported" in items[1].body


async def test_an_oversized_download_is_marked_truncated():
    big = _file("f1", "huge.txt", "text/plain", size=g.BODY_CAP + 5)
    items = await _collect(
        _transport(
            listing=[[big]],
            contents={"f1": "the first megabyte"},
            download_status={"f1": 206},
        )
    )
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
    with pytest.raises(gd.GoogleAdapterError, match="not connected"):
        await _collect(_transport(listing=[[]], seen=seen))
    assert seen == []  # refused before a single request left the machine


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})

    with pytest.raises(gd.GoogleAdapterError) as excinfo:
        await _collect(httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "ya29.test" not in message


def test_the_timestamp_is_iso_utc():
    # Guard the contract shape once: parseable, UTC-anchored.
    doc = _file("f1", "Design notes", _DOC)
    item = gd.item_from_file(doc, "text", False)
    assert item is not None
    parsed = datetime.fromisoformat(item.timestamp_utc.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.astimezone(UTC).year == 2026


def test_the_integration_id_matches_the_catalog_and_the_bridge():
    from jarvis.ultrawiki import connector_catalog
    from jarvis.ultrawiki.connectors import plugin_bridge

    spec = connector_catalog.bridge_entry_for(gd.INTEGRATION_ID)
    assert spec is not None and spec.id == "google_drive"
    # The id must match what list_candidates reports, or the reader is
    # registered under a name nothing looks up.
    plugin_bridge.register_pull_adapter(gd.INTEGRATION_ID, gd.google_drive_pull_adapter)
    try:
        assert plugin_bridge.has_pull_adapter("plugin:google_drive") is True
    finally:
        plugin_bridge.unregister_pull_adapter(gd.INTEGRATION_ID)
