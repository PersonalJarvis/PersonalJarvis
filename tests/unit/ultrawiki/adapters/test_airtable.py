"""The Airtable pull adapter, driven fully offline through MockTransport.

What makes this reader trustworthy rather than merely functional: a stable
composite id (``<base>:<table>:<record>`` — a re-run must upsert, not
duplicate), a real deep link on every record, fields flattened in the table's
own schema order, attachments listed by name and URL but never downloaded,
a deterministic external-id order so the backfill checkpoint resumes strictly
after the last persisted id, honest full-rescan freshness (Airtable has no
global modified-since filter), and failure messages a user can act on that
never carry the token.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jarvis.ultrawiki.adapters import airtable as at
from jarvis.ultrawiki.types import ConnectorContext


@pytest.fixture(autouse=True)
def marketplace_token(monkeypatch):
    """A connected Airtable plugin, without touching the host's keyring."""

    class _Tokens:
        access = "pat-airtable-test"
        needs_reauth = False

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )


def _ctx() -> ConnectorContext:
    return ConnectorContext(
        source_id="src-airtable",
        config={"integration_id": "plugin:airtable"},
        secret_get=lambda _name: None,
    )


def _base(base_id: str = "appBASE01", name: str = "Product") -> dict[str, Any]:
    return {"id": base_id, "name": name, "permissionLevel": "read"}


def _table(
    table_id: str = "tblT01",
    name: str = "Ideas",
    fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if fields is None:
        fields = [
            {"id": "fldName", "name": "Name", "type": "singleLineText"},
            {"id": "fldNotes", "name": "Notes", "type": "multilineText"},
        ]
    return {
        "id": table_id,
        "name": name,
        "primaryFieldId": fields[0]["id"] if fields else "",
        "fields": fields,
    }


def _record(
    record_id: str = "rec01",
    fields: dict[str, Any] | None = None,
    created: str = "2026-03-01T10:00:00Z",
) -> dict[str, Any]:
    return {
        "id": record_id,
        "createdTime": created,
        "fields": fields if fields is not None else {"Name": "Wake-word fix"},
    }


def _transport(
    bases: list[dict[str, Any]],
    tables: dict[str, Any],
    records: dict[str, Any],
    *,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """Serve the three Airtable endpoints from plain dicts.

    ``tables`` maps a base id to its table list (or an int HTTP status to
    answer with); ``records`` maps ``"<base>/<table>"`` to a record list (or a
    status). Offset paging gets its own handlers in the tests that pin it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/v0/meta/bases":
            return httpx.Response(200, json={"bases": bases})
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:3] == ["v0", "meta", "bases"] and parts[4] == "tables":
            payload = tables.get(parts[3])
            if isinstance(payload, int):
                return httpx.Response(payload, json={})
            return httpx.Response(200, json={"tables": payload or []})
        if len(parts) == 3 and parts[0] == "v0":
            payload = records.get(f"{parts[1]}/{parts[2]}")
            if isinstance(payload, int):
                return httpx.Response(payload, json={})
            return httpx.Response(200, json={"records": payload or []})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


async def _collect(checkpoint: str | None = None, **kwargs: Any) -> list:
    return [
        item
        async for item in at.airtable_pull_adapter(_ctx(), checkpoint, **kwargs)
    ]


async def test_a_record_becomes_a_linkable_dated_item():
    transport = _transport(
        [_base()],
        {"appBASE01": [_table()]},
        {"appBASE01/tblT01": [_record(fields={"Name": "Wake-word fix", "Notes": "Ship it"})]},
    )
    items = await _collect(transport=transport)
    assert len(items) == 1
    item = items[0]
    # Stable per source: a second run must upsert this same row, not add one.
    assert item.external_id == "appBASE01:tblT01:rec01"
    # Evidence has to lead back to where it lives — the record's deep link.
    assert item.permalink == "https://airtable.com/appBASE01/tblT01/rec01"
    assert item.timestamp_utc == "2026-03-01T10:00:00Z"
    assert item.title == "Wake-word fix"
    assert item.thread_key == "appBASE01:tblT01"
    assert "Product · Ideas · record" in item.body
    assert "Name: Wake-word fix" in item.body
    assert "Notes: Ship it" in item.body
    assert item.metadata["mtime_ns"] == at._to_ns("2026-03-01T10:00:00Z")


async def test_fields_follow_the_tables_own_schema_order():
    """The schema order is the one the user arranged and reads the record in;
    keys the schema does not know follow, sorted."""
    fields = [
        {"id": "fldA", "name": "Alpha", "type": "singleLineText"},
        {"id": "fldB", "name": "Beta", "type": "singleLineText"},
    ]
    record = _record(fields={"Zulu": "last", "Beta": "two", "Alpha": "one", "Mike": "extra"})
    transport = _transport(
        [_base()],
        {"appBASE01": [_table(fields=fields)]},
        {"appBASE01/tblT01": [record]},
    )
    items = await _collect(transport=transport)
    lines = items[0].body.splitlines()
    assert lines[2:] == ["Alpha: one", "Beta: two", "Mike: extra", "Zulu: last"]


async def test_attachments_are_listed_by_name_and_url_never_downloaded():
    seen: list[httpx.Request] = []
    fields = [
        {"id": "fldName", "name": "Name", "type": "singleLineText"},
        {"id": "fldFiles", "name": "Files", "type": "multipleAttachments"},
    ]
    record = _record(
        fields={
            "Name": "Spec",
            "Files": [
                {"filename": "photo.jpg", "url": "https://dl.airtable.com/photo.jpg", "size": 9}
            ],
        }
    )
    transport = _transport(
        [_base()],
        {"appBASE01": [_table(fields=fields)]},
        {"appBASE01/tblT01": [record]},
        seen=seen,
    )
    items = await _collect(transport=transport)
    assert "Files: photo.jpg (https://dl.airtable.com/photo.jpg)" in items[0].body
    # Listed, never fetched: every request stayed on the Airtable API host.
    assert all(request.url.host == "api.airtable.com" for request in seen)


async def test_a_tracked_modified_time_wins_over_created_time():
    fields = [
        {"id": "fldName", "name": "Name", "type": "singleLineText"},
        {"id": "fldMod", "name": "Last touched", "type": "lastModifiedTime"},
    ]
    record = _record(fields={"Name": "x", "Last touched": "2026-03-05T09:00:00Z"})
    transport = _transport(
        [_base()],
        {"appBASE01": [_table(fields=fields)]},
        {"appBASE01/tblT01": [record]},
    )
    items = await _collect(transport=transport)
    assert items[0].timestamp_utc == "2026-03-05T09:00:00Z"
    assert items[0].metadata["modified_time"] == "2026-03-05T09:00:00Z"
    assert items[0].metadata["created_time"] == "2026-03-01T10:00:00Z"
    assert items[0].metadata["mtime_ns"] == at._to_ns("2026-03-05T09:00:00Z")


async def test_values_render_human_readably():
    fields = [
        {"id": "fldName", "name": "Name", "type": "singleLineText"},
        {"id": "fldDone", "name": "Done", "type": "checkbox"},
        {"id": "fldOwner", "name": "Owner", "type": "singleCollaborator"},
        {"id": "fldTags", "name": "Tags", "type": "multipleSelects"},
    ]
    record = _record(
        fields={
            "Name": "x",
            "Done": True,
            "Owner": {"id": "usr1", "email": "ana@example.com", "name": "Ana"},
            "Tags": ["a", "b"],
        }
    )
    transport = _transport(
        [_base()],
        {"appBASE01": [_table(fields=fields)]},
        {"appBASE01/tblT01": [record]},
    )
    items = await _collect(transport=transport)
    assert "Done: yes" in items[0].body
    assert "Owner: Ana" in items[0].body
    assert "Tags: a, b" in items[0].body


async def test_a_record_with_an_empty_primary_field_still_gets_a_title():
    transport = _transport(
        [_base()],
        {"appBASE01": [_table()]},
        {"appBASE01/tblT01": [_record(fields={})]},
    )
    items = await _collect(transport=transport)
    assert items[0].title == "Ideas record rec01"
    assert items[0].body == "Product · Ideas · record"


async def test_offset_paging_walks_every_base_and_record_page():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/meta/bases":
            if request.url.params.get("offset") == "b2":
                return httpx.Response(200, json={"bases": [_base("appB", "Second")]})
            return httpx.Response(
                200, json={"bases": [_base("appA", "First")], "offset": "b2"}
            )
        if path.endswith("/tables"):
            return httpx.Response(200, json={"tables": [_table()]})
        if request.url.params.get("offset") == "r2":
            return httpx.Response(200, json={"records": [_record("rec02")]})
        assert request.url.params.get("pageSize") == "100"
        return httpx.Response(200, json={"records": [_record("rec01")], "offset": "r2"})

    items = await _collect(transport=httpx.MockTransport(handler))
    assert [item.external_id for item in items] == [
        "appA:tblT01:rec01",
        "appA:tblT01:rec02",
        "appB:tblT01:rec01",
        "appB:tblT01:rec02",
    ]


async def test_the_walk_is_sorted_by_external_id():
    """Registry order is whatever the API returns; the yield order must be the
    one order the checkpoint can resume from."""
    transport = _transport(
        [_base("appB"), _base("appA")],
        {"appB": [_table("tblZ")], "appA": [_table("tblM"), _table("tblA")]},
        {
            "appB/tblZ": [_record("rec1")],
            "appA/tblM": [_record("rec2"), _record("rec1")],
            "appA/tblA": [_record("rec9")],
        },
    )
    items = await _collect(transport=transport)
    ids = [item.external_id for item in items]
    assert ids == sorted(ids)
    assert ids[0] == "appA:tblA:rec9"
    assert len(ids) == 4


async def test_a_backfill_checkpoint_resumes_strictly_after_it():
    seen: list[httpx.Request] = []
    transport = _transport(
        [_base("appA")],
        {"appA": [_table("tbl0"), _table("tbl1"), _table("tbl2")]},
        {
            "appA/tbl0": [_record("rec1")],
            "appA/tbl1": [_record("rec1"), _record("rec2"), _record("rec3")],
            "appA/tbl2": [_record("rec1")],
        },
        seen=seen,
    )
    items = await _collect(checkpoint="appA:tbl1:rec2", transport=transport)
    assert [item.external_id for item in items] == ["appA:tbl1:rec3", "appA:tbl2:rec1"]
    # A table whose every record sorts before the checkpoint is skipped
    # without a single records request.
    assert "/v0/appA/tbl0" not in [request.url.path for request in seen]


async def test_a_numeric_cursor_is_honestly_a_full_rescan():
    """Airtable's list API has no global modified-since filter, so a numeric
    cursor cannot narrow the walk server-side; mis-filtering would silently
    lose records. It deliberately reads as a full rescan — the idempotent
    upserts make that correct."""
    transport = _transport(
        [_base()],
        {"appBASE01": [_table()]},
        {"appBASE01/tblT01": [_record()]},
    )
    cursor = str(at._to_ns("2026-03-04T12:00:00Z"))
    items = await _collect(checkpoint=cursor, transport=transport)
    assert [item.external_id for item in items] == ["appBASE01:tblT01:rec01"]


async def test_a_denied_base_is_skipped_not_fatal():
    """A missing scope on ONE base must not abandon everything the token CAN
    read."""
    transport = _transport(
        [_base("appA"), _base("appB")],
        {"appA": 403, "appB": [_table()]},
        {"appB/tblT01": [_record()]},
    )
    items = await _collect(transport=transport)
    assert [item.external_id for item in items] == ["appB:tblT01:rec01"]


async def test_a_denied_table_is_skipped_not_fatal():
    transport = _transport(
        [_base("appA")],
        {"appA": [_table("tblA"), _table("tblB")]},
        {"appA/tblA": 403, "appA/tblB": [_record()]},
    )
    items = await _collect(transport=transport)
    assert [item.external_id for item in items] == ["appA:tblB:rec01"]


async def test_a_rate_limited_request_waits_and_retries_bounded():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/meta/bases":
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"bases": [_base()]})
        if request.url.path.endswith("/tables"):
            return httpx.Response(200, json={"tables": [_table()]})
        return httpx.Response(200, json={"records": [_record()]})

    items = await _collect(transport=httpx.MockTransport(handler))
    assert attempts["count"] == 2
    assert len(items) == 1


async def test_a_pathological_long_text_field_is_truncated_with_a_marker():
    huge = "x" * (at._MAX_BODY_BYTES + 100_000)
    transport = _transport(
        [_base()],
        {"appBASE01": [_table()]},
        {"appBASE01/tblT01": [_record(fields={"Name": "big", "Notes": huge})]},
    )
    items = await _collect(transport=transport)
    assert len(items) == 1
    body = items[0].body
    assert body.endswith(at._TRUNCATION_MARKER)
    marker_bytes = len(at._TRUNCATION_MARKER.encode("utf-8"))
    assert len(body.encode("utf-8")) <= at._MAX_BODY_BYTES + marker_bytes


async def test_a_rejected_token_says_what_to_do_and_never_echoes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "AUTHENTICATION_REQUIRED"}})

    with pytest.raises(at.AirtableAdapterError) as excinfo:
        await _collect(transport=httpx.MockTransport(handler))
    message = str(excinfo.value)
    assert "Reconnect" in message
    assert "pat-airtable-test" not in message


async def test_an_unconnected_plugin_refuses_before_any_request(monkeypatch):
    class _Store:
        def load(self, _plugin_id):
            return None

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(at.AirtableAdapterError, match="not connected"):
        await _collect(transport=_transport([], {}, {}))


async def test_an_expired_connection_is_named_as_such(monkeypatch):
    class _Tokens:
        access = "pat-old"
        needs_reauth = True

    class _Store:
        def load(self, _plugin_id):
            return _Tokens()

    monkeypatch.setattr(
        "jarvis.marketplace.token_store.TokenStore", lambda *a, **k: _Store()
    )
    with pytest.raises(at.AirtableAdapterError, match="expired"):
        await _collect(transport=_transport([], {}, {}))


def test_the_integration_id_matches_the_curated_catalog():
    """The adapter must register under the id the picker actually routes."""
    from jarvis.ultrawiki import connector_catalog

    spec = connector_catalog.bridge_entry_for(at.INTEGRATION_ID)
    assert spec is not None
    assert spec.id == "airtable"
