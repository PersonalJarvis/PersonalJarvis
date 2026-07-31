"""The Dropbox pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: a stable id
(``path_lower`` — a re-run must upsert, not duplicate), a real web preview
link on every item, honest skipping of binaries BEFORE any byte is
downloaded, deterministic path order so the backfill checkpoint resumes
strictly after the last persisted id, and failure messages a user can act on
that never carry the token.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import dropbox as dbx
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Dropbox plugin, without touching the host's keyring."""

    class _Tokens:
        access = "sl.dbx-test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx(**config: Any) -> ConnectorContext:
    return ConnectorContext(
        source_id="src-dropbox",
        config={"integration_id": "plugin:dropbox", **config},
        secret_get=lambda _name: None,
    )


def _file(
    path_display: str,
    *,
    size: int = 64,
    modified: str = "2026-03-01T10:00:00Z",
    tag: str = "file",
    downloadable: bool = True,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        ".tag": tag,
        "name": path_display.rsplit("/", 1)[-1],
        "path_lower": path_display.lower(),
        "path_display": path_display,
        "id": f"id:{path_display}",
        "server_modified": modified,
        "client_modified": modified,
        "size": size,
        "rev": "0123456789abcdef",
    }
    if not downloadable:
        entry["is_downloadable"] = False
    return entry


def _transport(
    pages: list[list[dict[str, Any]]],
    contents: dict[str, str | bytes],
    *,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A fake Dropbox. ``contents`` accepts bytes for real binary documents —
    the shape a PDF or a Word file actually arrives in."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/2/files/download":
            arg = json.loads(request.headers["Dropbox-API-Arg"])
            body = contents.get(arg["path"])
            if body is None:
                # The file vanished between list and download.
                return httpx.Response(409, json={"error_summary": "path/not_found/.."})
            if isinstance(body, bytes):
                return httpx.Response(200, content=body)
            return httpx.Response(200, content=body.encode())
        if path == "/2/files/list_folder":
            index = 0
        elif path == "/2/files/list_folder/continue":
            index = int(json.loads(request.content)["cursor"].split("-")[1])
        else:
            return httpx.Response(404, json={})
        return httpx.Response(
            200,
            json={
                "entries": pages[index],
                "cursor": f"cursor-{index + 1}",
                "has_more": index + 1 < len(pages),
            },
        )

    return httpx.MockTransport(handler)


def _downloaded_paths(seen: list[httpx.Request]) -> list[str]:
    return [
        json.loads(request.headers["Dropbox-API-Arg"])["path"]
        for request in seen
        if request.url.path == "/2/files/download"
    ]


async def _collect(
    checkpoint: str | None = None, *, ctx: ConnectorContext | None = None, **kwargs: Any
) -> list:
    return [
        item
        async for item in dbx.dropbox_pull_adapter(ctx or _ctx(), checkpoint, **kwargs)
    ]


async def test_a_text_file_becomes_a_linkable_dated_item():
    entry = _file("/Notes/Weekly Plan.md")
    transport = _transport([[entry]], {"/notes/weekly plan.md": "Plan the wake-word fix."})
    items = await _collect(transport=transport)
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == "/notes/weekly plan.md"
    # Evidence has to lead back to where it lives — the web preview link.
    assert item.permalink == "https://www.dropbox.com/home/Notes?preview=Weekly%20Plan.md"
    assert item.timestamp_utc == "2026-03-01T10:00:00Z"
    assert item.title == "Weekly Plan"
    assert item.thread_key == "/notes"
    assert item.body == "Plan the wake-word fix."
    assert item.metadata["mtime_ns"] == dbx._to_ns("2026-03-01T10:00:00Z")
    assert item.metadata["rev"] == "0123456789abcdef"


async def test_blobs_are_never_downloaded_but_documents_are():
    """The skip happens by extension BEFORE any byte moves — a photo library
    must not cost one request per photo just to be rejected.

    A PDF is not in that category, and treating it as one is what made
    "import my Dropbox" quietly mean "import the notes lying beside the
    documents".
    """
    seen: list[httpx.Request] = []
    pages = [[_file("/photo.jpg"), _file("/report.pdf"), _file("/notes.md")]]
    items = await _collect(
        transport=_transport(
            pages,
            {"/notes.md": "text", "/report.pdf": "%PDF-1.4 not readable"},
            seen=seen,
        )
    )
    assert [item.external_id for item in items] == ["/notes.md", "/report.pdf"]
    assert _downloaded_paths(seen) == ["/notes.md", "/report.pdf"]
    # The picture never moved a byte.
    assert "/photo.jpg" not in _downloaded_paths(seen)


async def test_a_word_document_is_imported_with_its_text():
    """The point of the whole change: a container is not decoded as UTF-8 (that
    produced a body of replacement characters, which is why these files were
    excluded), it is handed to the extractor."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document xmlns:w='x'><w:body><w:p><w:r><w:t>"
            "The quarterly ledger reconciliation"
            "</w:t></w:r></w:p></w:body></w:document>",
        )
    docx = buffer.getvalue()
    pages = [[_file("/ledger.docx", size=len(docx))]]
    items = await _collect(transport=_transport(pages, {"/ledger.docx": docx}))
    assert len(items) == 1
    assert "The quarterly ledger reconciliation" in items[0].body
    assert not items[0].metadata.get("content_missing")


async def test_a_document_with_no_readable_text_is_still_imported():
    """A scanned PDF is still a document that exists. It stays findable by
    name, folder and date, and says plainly what is missing."""
    pages = [[_file("/scan.pdf", size=40)]]
    items = await _collect(
        transport=_transport(pages, {"/scan.pdf": b"%PDF-1.4\nno text\n%%EOF"})
    )
    assert len(items) == 1
    assert items[0].metadata["content_missing"] is True
    assert "no text imported" in items[0].body


async def test_folders_deleted_and_non_downloadable_entries_are_ignored():
    seen: list[httpx.Request] = []
    pages = [
        [
            _file("/docs", tag="folder"),
            _file("/gone.md", tag="deleted"),
            _file("/paper.md", downloadable=False),
        ]
    ]
    items = await _collect(transport=_transport(pages, {}, seen=seen))
    assert items == []
    assert _downloaded_paths(seen) == []


async def test_the_extension_allowlist_is_configurable_per_source():
    """Same philosophy as the local-folder connector: a curated default the
    source config may override."""
    pages = [[_file("/build.log"), _file("/notes.md")]]
    items = await _collect(
        ctx=_ctx(extensions=["log"]),
        transport=_transport(pages, {"/build.log": "log text"}),
    )
    assert [item.external_id for item in items] == ["/build.log"]


async def test_cursor_paging_walks_every_page_in_path_order():
    """list_folder/continue is followed to the end, and the yield order is
    sorted by external_id — the only order the checkpoint can resume from."""
    seen: list[httpx.Request] = []
    pages = [[_file("/b.md")], [_file("/a.md")]]
    items = await _collect(
        transport=_transport(pages, {"/a.md": "A", "/b.md": "B"}, seen=seen)
    )
    assert [item.external_id for item in items] == ["/a.md", "/b.md"]
    assert len([r for r in seen if r.url.path == "/2/files/list_folder/continue"]) == 1


async def test_a_path_checkpoint_resumes_strictly_after_it():
    seen: list[httpx.Request] = []
    pages = [[_file("/a.md"), _file("/b.md"), _file("/c.md")]]
    contents = {"/a.md": "A", "/b.md": "B", "/c.md": "C"}
    items = await _collect(
        checkpoint="/b.md", transport=_transport(pages, contents, seen=seen)
    )
    assert [item.external_id for item in items] == ["/c.md"]
    # Skipped files are skipped before download, not after.
    assert _downloaded_paths(seen) == ["/c.md"]


async def test_a_numeric_cursor_skips_unchanged_files_before_download():
    seen: list[httpx.Request] = []
    pages = [
        [
            _file("/old.md", modified="2026-03-01T10:00:00Z"),
            _file("/new.md", modified="2026-03-03T10:00:00Z"),
        ]
    ]
    contents = {"/old.md": "old", "/new.md": "new"}
    cursor = str(dbx._to_ns("2026-03-02T00:00:00Z"))
    items = await _collect(
        checkpoint=cursor, transport=_transport(pages, contents, seen=seen)
    )
    assert [item.external_id for item in items] == ["/new.md"]
    assert _downloaded_paths(seen) == ["/new.md"]


async def test_oversized_files_say_so_instead_of_vanishing():
    """A file above the ceiling is not downloaded — but it IS recorded.

    Dropping it silently makes "too big to read" look exactly like "never
    existed", and only one of those two is the truth.
    """
    seen: list[httpx.Request] = []
    pages = [
        [
            _file("/huge.md", size=3 * 1024 * 1024),
            _file("/long.md", size=dbx._MAX_BODY_BYTES + 200_000),
        ]
    ]
    contents = {"/long.md": "x" * (dbx._MAX_BODY_BYTES + 100_000)}
    items = await _collect(transport=_transport(pages, contents, seen=seen))
    assert [item.external_id for item in items] == ["/huge.md", "/long.md"]
    # The oversized file still never cost a download.
    assert _downloaded_paths(seen) == ["/long.md"]
    assert items[0].metadata["content_missing"] is True
    assert "import limit" in items[0].metadata["content_missing_reason"]
    body = items[1].body
    assert body.endswith(dbx._TRUNCATION_MARKER)
    assert len(body) == dbx._MAX_BODY_BYTES + len(dbx._TRUNCATION_MARKER)


async def test_a_vanished_file_is_skipped_not_fatal():
    """One 409 between list and download must not sink the other files."""
    pages = [[_file("/gone.md"), _file("/here.md")]]
    items = await _collect(transport=_transport(pages, {"/here.md": "still here"}))
    assert [item.external_id for item in items] == ["/here.md"]


async def test_a_rate_limited_request_waits_and_retries_bounded():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/2/files/download":
            return httpx.Response(200, content=b"text")
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200, json={"entries": [_file("/a.md")], "cursor": "c", "has_more": False}
        )

    items = await _collect(transport=httpx.MockTransport(handler))
    assert [item.external_id for item in items] == ["/a.md"]
    assert attempts["count"] == 2


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error_summary": "invalid_access_token/.."})

    with pytest.raises(dbx.DropboxAdapterError) as excinfo:
        await _collect(transport=httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "sl.dbx-test" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(dbx.DropboxAdapterError, match="not connected"):
        await _collect(transport=_transport([[]], {}))


async def test_an_expired_connection_is_named_as_such(monkeypatch):
    class _Tokens:
        access = "sl.dbx-old"
        needs_reauth = True

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(dbx.DropboxAdapterError, match="expired"):
        await _collect(transport=_transport([[]], {}))


def test_the_integration_id_matches_the_curated_catalog():
    """The adapter must register under the id the picker actually routes."""
    from jarvis.ultrawiki import connector_catalog

    spec = connector_catalog.bridge_entry_for(dbx.INTEGRATION_ID)
    assert spec is not None
    assert spec.id == "dropbox"
