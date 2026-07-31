"""The custom source — connect a service nobody here has ever heard of.

The security half is the important half. This is the one connector that
fetches an address the user typed, so it is the only place in UltraWiki where
a misconfiguration could reach somewhere it should not: a cloud metadata
endpoint, or whatever else happens to be listening on this machine.

The rest guards the promise that a wrong field mapping fails visibly. A
connector that quietly yields nothing and reports success is what leaves
someone staring at an empty knowledge base with nothing anywhere explaining
why.

Offline throughout: every fetch is a fake.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from jarvis.ultrawiki.connectors.custom_source import (
    CustomSourceConnector,
    CustomSourceError,
    check_url,
    map_record,
    walk_path,
)
from jarvis.ultrawiki.types import ConnectorContext, RawItem, UWConnector


class _Response:
    def __init__(self, payload: Any, status: int = 200, *, text: str = "") -> None:
        self.status_code = status
        self.encoding = "utf-8"
        self.content = (
            text.encode("utf-8")
            if text
            else json.dumps(payload).encode("utf-8")
        )


class _FakeClient:
    """Records every call and answers from a queued list of responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def get(self, url: str, *, headers: dict, params: dict) -> Any:
        self.calls.append({"url": url, "headers": dict(headers), "params": dict(params)})
        if not self._responses:
            raise AssertionError("more requests than the test queued")
        return self._responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def connector_with(client: _FakeClient) -> CustomSourceConnector:
    connector = CustomSourceConnector()

    async def _client() -> Any:
        return client

    connector._client = _client  # type: ignore[method-assign]
    return connector


def ctx(**config: Any) -> ConnectorContext:
    config.setdefault("url", "https://example.com/api/items")
    config.setdefault("allow_private_network", True)  # example.com is fine, but
    return ConnectorContext(source_id="custom-1", config=config)


async def collect(connector: CustomSourceConnector, context: ConnectorContext) -> list[RawItem]:
    return [item async for item in connector.backfill(context)]


# ---------------------------------------------------------------------------
# URL safety — the half that matters
# ---------------------------------------------------------------------------


class TestUrlSafety:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/api",
            "http://localhost/api",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://10.0.0.5/internal",
            "http://192.168.1.10/api",
            "http://172.16.4.4/api",
            "http://[::1]/api",
        ],
    )
    def test_private_and_reserved_addresses_are_refused(self, url: str):
        """169.254.169.254 is the one that matters: it is the cloud metadata
        endpoint, and reaching it can hand out credentials."""
        with pytest.raises(CustomSourceError) as excinfo:
            check_url(url)
        assert "private" in str(excinfo.value) or "reserved" in str(excinfo.value)

    def test_a_deliberate_private_address_is_allowed_when_asked_for(self):
        assert check_url("http://192.168.1.10/api", allow_private=True)

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com"],
    )
    def test_only_http_and_https_are_fetched(self, url: str):
        with pytest.raises(CustomSourceError):
            check_url(url)

    def test_an_empty_or_hostless_address_is_refused(self):
        with pytest.raises(CustomSourceError):
            check_url("")
        with pytest.raises(CustomSourceError):
            check_url("https://")

    def test_a_public_address_passes(self):
        assert check_url("https://8.8.8.8/api") == "https://8.8.8.8/api"

    async def test_redirects_are_not_followed(self):
        """A redirect could aim anywhere, including back at a private range —
        which would walk straight around the check above."""
        import httpx

        connector = CustomSourceConnector()
        client = await connector._client()
        try:
            assert isinstance(client, httpx.AsyncClient)
            assert client.follow_redirects is False
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Reading records
# ---------------------------------------------------------------------------


class TestPaths:
    def test_a_dotted_path_walks_dicts_and_lists(self):
        payload = {"data": {"items": [{"id": 1}, {"id": 2}]}}
        assert walk_path(payload, "data.items")[1]["id"] == 2
        assert walk_path(payload, "data.items.0.id") == 1

    def test_a_missing_path_is_none_rather_than_an_error(self):
        assert walk_path({"a": 1}, "b.c.d") is None

    def test_an_empty_path_is_the_payload_itself(self):
        """An API answering a bare array needs this."""
        payload = [{"id": 1}]
        assert walk_path(payload, "") is payload


class TestMapping:
    def test_an_explicit_mapping_is_followed_exactly(self):
        record = {"pk": "x1", "headline": "Title", "note": "Body text"}
        item = map_record(
            record,
            {"id": "pk", "title": "headline", "body": "note"},
            source_url="https://example.com",
            index=0,
        )
        assert item.external_id == "x1"
        assert item.title == "Title"
        assert item.body == "Body text"

    def test_conventional_names_are_tried_when_nothing_is_mapped(self):
        item = map_record(
            {"id": "a", "title": "T", "content": "C", "created_at": "2024-03-01T10:00:00Z"},
            {},
            source_url="https://example.com",
            index=0,
        )
        assert item.external_id == "a"
        assert item.timestamp_utc == "2024-03-01T10:00:00Z"

    def test_an_unmapped_unconventional_field_stays_empty_rather_than_guessed(self):
        """A guess that is right nine times in ten produces a silently wrong
        tenth — an item filed under the wrong year, say."""
        item = map_record(
            {"xyzzy": "some text", "plugh": "2024-03-01"},
            {"body": "xyzzy"},
            source_url="https://example.com",
            index=0,
        )
        assert item.timestamp_utc == ""

    def test_a_record_with_no_text_is_dropped(self):
        """An empty item ranks in search and says nothing."""
        assert map_record({"id": "x"}, {}, source_url="https://e.com", index=0) is None

    def test_a_nested_field_can_be_mapped(self):
        item = map_record(
            {"id": "1", "author": {"name": "Ada"}, "text": "hi"},
            {"author": "author.name"},
            source_url="https://e.com",
            index=0,
        )
        assert item.author_raw == "Ada"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2024-03-01T10:00:00Z", "2024-03-01T10:00:00Z"),
            ("2024-03-01 10:00", "2024-03-01T10:00:00Z"),
            ("1709287200", "2024-03-01T10:00:00Z"),
            ("1709287200000", "2024-03-01T10:00:00Z"),  # milliseconds
            ("Fri, 01 Mar 2024 10:00:00 +0000", "2024-03-01T10:00:00Z"),
        ],
    )
    def test_the_time_shapes_that_actually_occur_are_read(self, raw, expected):
        item = map_record(
            {"text": "x", "timestamp": raw}, {}, source_url="https://e.com", index=0
        )
        assert item.timestamp_utc == expected

    def test_an_unreadable_time_is_empty_rather_than_wrong(self):
        """A wrong timestamp files a memory in the wrong year."""
        item = map_record(
            {"text": "x", "timestamp": "last Tuesday"},
            {},
            source_url="https://e.com",
            index=0,
        )
        assert item.timestamp_utc == ""


# ---------------------------------------------------------------------------
# The HTTP mode end to end
# ---------------------------------------------------------------------------


class TestHttpMode:
    async def test_a_simple_endpoint_is_read(self):
        client = _FakeClient([_Response([{"id": "1", "text": "hello"}])])
        items = await collect(connector_with(client), ctx())
        assert [item.body for item in items] == ["hello"]
        assert client.closed, "the client must always be closed"

    async def test_a_credential_is_sent_as_configured_and_never_stored_inline(self):
        client = _FakeClient([_Response([{"id": "1", "text": "x"}])])
        context = ctx(
            secret_slot="my-api-key",  # noqa: S106 — a slot NAME; the value is not here
            auth_style="header",
            auth_name="X-Token",
        )
        context.secret_get = lambda slot: "s3cret" if slot == "my-api-key" else ""
        await collect(connector_with(client), context)
        assert client.calls[0]["headers"]["X-Token"] == "s3cret"
        # The config itself carries only the SLOT name, never the value.
        assert "s3cret" not in json.dumps(context.config)

    async def test_bearer_is_the_default_style(self):
        client = _FakeClient([_Response([{"id": "1", "text": "x"}])])
        context = ctx(secret_slot="k")  # noqa: S106 — a slot name, not a secret
        context.secret_get = lambda slot: "abc"
        await collect(connector_with(client), context)
        assert client.calls[0]["headers"]["Authorization"] == "Bearer abc"

    async def test_cursor_paging_walks_until_the_cursor_stops_moving(self):
        client = _FakeClient(
            [
                _Response({"items": [{"id": "1", "text": "a"}], "next": "c2"}),
                _Response({"items": [{"id": "2", "text": "b"}], "next": "c3"}),
                _Response({"items": [{"id": "3", "text": "c"}], "next": ""}),
            ]
        )
        items = await collect(
            connector_with(client),
            ctx(items_path="items", cursor_path="next", cursor_param="cursor"),
        )
        assert [item.body for item in items] == ["a", "b", "c"]
        assert client.calls[1]["params"]["cursor"] == "c2"

    async def test_a_cursor_that_never_advances_does_not_loop_forever(self):
        """A stuck cursor is an ordinary API bug, not an attack.

        Two requests: the first cursor is legitimately new, the second repeats
        it and ends the walk. Without the guard this would run to the page
        ceiling and re-import the same page two hundred times.
        """
        client = _FakeClient(
            [_Response({"items": [{"id": "1", "text": "a"}], "next": "same"})] * 5
        )
        items = await collect(
            connector_with(client),
            ctx(items_path="items", cursor_path="next", cursor_param="cursor"),
        )
        assert len(client.calls) == 2
        assert len(items) == 2

    async def test_a_cursor_that_alternates_between_two_values_also_stops(self):
        """A→B→A→B would run to the ceiling if only the LAST value were
        compared; every cursor already used ends the walk."""
        client = _FakeClient(
            [
                _Response({"items": [{"id": "1", "text": "a"}], "next": "B"}),
                _Response({"items": [{"id": "2", "text": "b"}], "next": "A"}),
                _Response({"items": [{"id": "3", "text": "c"}], "next": "B"}),
                _Response({"items": [{"id": "4", "text": "d"}], "next": "A"}),
            ]
        )
        await collect(
            connector_with(client),
            ctx(items_path="items", cursor_path="next", cursor_param="cursor"),
        )
        assert len(client.calls) == 3, "the repeat of B must end it"

    async def test_page_paging_stops_at_the_first_empty_page(self):
        client = _FakeClient(
            [
                _Response({"items": [{"id": "1", "text": "a"}]}),
                _Response({"items": []}),
            ]
        )
        items = await collect(
            connector_with(client), ctx(items_path="items", page_param="page")
        )
        assert len(items) == 1
        assert client.calls[1]["params"]["page"] == "2"

    async def test_the_incremental_cursor_is_passed_as_the_since_parameter(self):
        client = _FakeClient([_Response([])])
        connector = connector_with(client)
        context = ctx(since_param="updated_since")
        [item async for item in connector.incremental(context, "2024-01-01T00:00:00Z")]
        assert client.calls[0]["params"]["updated_since"] == "2024-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Failing visibly
# ---------------------------------------------------------------------------


class TestHonestFailure:
    async def test_a_wrong_items_path_says_so_instead_of_yielding_nothing(self):
        """Silence here is what leaves someone staring at an empty database."""
        client = _FakeClient([_Response({"results": [{"id": "1", "text": "a"}]})])
        with pytest.raises(CustomSourceError) as excinfo:
            await collect(connector_with(client), ctx(items_path="items"))
        assert "items_path" in str(excinfo.value)

    async def test_html_where_json_was_expected_suggests_the_feed_mode(self):
        client = _FakeClient([_Response(None, text="<rss><channel></channel></rss>")])
        with pytest.raises(CustomSourceError) as excinfo:
            await collect(connector_with(client), ctx())
        assert "feed" in str(excinfo.value)

    async def test_an_auth_failure_points_at_the_credential(self):
        client = _FakeClient([_Response({}, status=401)])
        with pytest.raises(CustomSourceError) as excinfo:
            await collect(connector_with(client), ctx())
        assert "credential" in str(excinfo.value)

    async def test_an_unknown_mode_is_refused_by_name(self):
        with pytest.raises(CustomSourceError) as excinfo:
            await collect(connector_with(_FakeClient([])), ctx(mode="graphql"))
        assert "graphql" in str(excinfo.value)

    async def test_an_oversized_response_is_refused_with_advice(self):
        import jarvis.ultrawiki.connectors.custom_source as module

        big = _Response(None, text="x" * 200)
        client = _FakeClient([big])
        original = module.MAX_RESPONSE_BYTES
        module.MAX_RESPONSE_BYTES = 100
        try:
            with pytest.raises(CustomSourceError) as excinfo:
                await collect(connector_with(client), ctx())
            assert "paging" in str(excinfo.value)
        finally:
            module.MAX_RESPONSE_BYTES = original


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>A blog</title>
  <item>
    <title>First post</title>
    <link>https://example.com/1</link>
    <guid>post-1</guid>
    <pubDate>Fri, 01 Mar 2024 10:00:00 +0000</pubDate>
    <description>Some &amp; content</description>
  </item>
  <item>
    <title>Second post</title>
    <link>https://example.com/2</link>
    <description><![CDATA[<p>Rich <b>text</b></p>]]></description>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom entry</title>
    <id>urn:uuid:1</id>
    <link href="https://example.com/a"/>
    <updated>2024-03-01T10:00:00Z</updated>
    <content>Body here</content>
  </entry>
</feed>"""


class TestFeedMode:
    async def test_an_rss_feed_is_read(self):
        client = _FakeClient([_Response(None, text=RSS)])
        items = await collect(connector_with(client), ctx(mode="feed"))
        assert [item.title for item in items] == ["First post", "Second post"]
        assert items[0].external_id == "post-1"
        assert items[0].timestamp_utc == "2024-03-01T10:00:00Z"
        assert "Some & content" in items[0].body

    async def test_html_inside_an_entry_becomes_readable_text(self):
        client = _FakeClient([_Response(None, text=RSS)])
        items = await collect(connector_with(client), ctx(mode="feed"))
        assert "Rich text" in items[1].body.replace("  ", " ")
        assert "<b>" not in items[1].body

    async def test_an_atom_feed_is_read_including_its_link_attribute(self):
        client = _FakeClient([_Response(None, text=ATOM)])
        items = await collect(connector_with(client), ctx(mode="feed"))
        assert items[0].permalink == "https://example.com/a"
        assert items[0].body == "Body here"

    async def test_a_malformed_feed_yields_what_it_can(self):
        """Real feeds are frequently not well-formed; a strict parser would
        reject the whole document over one bad character in one entry."""
        broken = RSS.replace("</channel>", "")
        client = _FakeClient([_Response(None, text=broken)])
        items = await collect(connector_with(client), ctx(mode="feed"))
        assert len(items) == 2


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_it_satisfies_the_connector_protocol():
    assert isinstance(CustomSourceConnector(), UWConnector)


def test_it_is_offered_as_a_built_in_source():
    from jarvis.ultrawiki.connector_catalog import get_connector
    from jarvis.ultrawiki.connectors import builtin_connectors

    assert "custom-source" in builtin_connectors()
    entry = get_connector("custom-source")
    assert entry is not None and entry.status == "available"
