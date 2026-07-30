"""Connect a service nobody here has ever heard of.

The last gap in "import everything from every platform". A service with no
adapter and no export button had no route at all — and there will always be
one: a niche journalling app, a regional social network, a company's internal
tool, something released next month.

Two modes, both configured entirely in the app:

* **``http``** — a URL that returns JSON, an optional credential, a path to
  the list of records, and a mapping saying which field is the id, the title,
  the text, the timestamp and the link. Paging by cursor or page number;
  freshness by passing a since-parameter.
* **``feed``** — RSS or Atom, which a surprising share of services still emit
  and which needs no configuration beyond the address.

Why a mapping rather than a schema guess: every API names things differently
(``id`` / ``uuid`` / ``pk``, ``created_at`` / ``published`` / ``date``), and a
guess that is right nine times out of ten produces a silently wrong tenth.
Naming the fields takes a minute and never lies.

**Safety.** This connector fetches a URL the user typed, which makes it the
one place in UltraWiki where a misconfiguration could reach somewhere it
should not. Refused by default: any scheme other than http/https, and any
address that resolves into a private, loopback, link-local or reserved range.
The second is the SSRF guard — a URL like ``http://169.254.169.254/`` is a
cloud metadata endpoint, and ``http://localhost:8080/`` is whatever else runs
on this machine. A user who genuinely means their own network ticks
``allow_private_network`` and says so explicitly.

Everything it yields is a plain :class:`RawItem`; it touches no store, no
model and no embedding (design doc 02, hard rule 1).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from jarvis.ultrawiki.types import (
    AuthKind,
    ConnectorCapabilities,
    ConnectorContext,
    IncrementalMode,
    RawItem,
)

log = logging.getLogger(__name__)

__all__ = [
    "CustomSourceConnector",
    "CustomSourceError",
    "MODES",
    "check_url",
    "map_record",
    "walk_path",
]

#: The two shapes a user can configure.
MODES: tuple[str, ...] = ("http", "feed")

#: Bytes accepted from one response. A knowledge source that answers with more
#: than this per page is misconfigured, and reading it would be a memory
#: exhaustion the user never asked for.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

#: Pages walked in one run. Bounds a paging loop whose cursor never advances —
#: which is a normal API bug, not an attack, and would otherwise run forever.
MAX_PAGES = 200

#: Items accepted from one run, as the same kind of backstop.
MAX_ITEMS = 50_000

#: Seconds for one request.
REQUEST_TIMEOUT_S = 30.0

#: Characters kept from one record's text.
MAX_BODY_CHARS = 200_000

#: Field names tried when the user maps nothing, in order. A convenience for
#: the common case — NOT a schema guess: an unmapped field that matches none
#: of these stays empty and the item says so, rather than silently taking
#: whatever field happened to be first.
_DEFAULT_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "uuid", "guid", "key", "pk", "identifier", "_id"),
    "title": ("title", "name", "subject", "headline", "summary"),
    "body": ("body", "text", "content", "description", "message", "note"),
    "timestamp": (
        "timestamp", "created_at", "createdAt", "published", "published_at",
        "date", "updated_at", "updatedAt", "time",
    ),
    "permalink": ("permalink", "url", "link", "html_url", "href"),
}


class CustomSourceError(RuntimeError):
    """The source cannot be read. Raised so the reason reaches the source card.

    Deliberately raised rather than returning nothing: a run that yields zero
    items and reports success is the failure mode that leaves a user staring
    at an empty knowledge base with nothing anywhere explaining why.
    """


# ---------------------------------------------------------------------------
# URL safety
# ---------------------------------------------------------------------------


def check_url(url: str, *, allow_private: bool = False) -> str:
    """Return the URL if it is safe to fetch; raise otherwise.

    Resolution happens here rather than being left to the HTTP client so the
    decision is made on the ADDRESS, not on the hostname: ``internal.example``
    resolving to 10.0.0.5 has to be refused for the same reason the literal
    address would be.
    """
    text = str(url or "").strip()
    if not text:
        raise CustomSourceError("This source has no address configured.")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise CustomSourceError(
            f"{parsed.scheme or 'that'!r} is not an address this can fetch — "
            "use http:// or https://."
        )
    host = parsed.hostname or ""
    if not host:
        raise CustomSourceError(f"{text!r} has no host in it.")
    if allow_private:
        return text
    for address in _resolve(host):
        if not address.is_global:
            raise CustomSourceError(
                f"{host} resolves to {address}, which is on a private or "
                "reserved network. If that is deliberate — your own server, "
                "something on this machine — tick 'this is on my own network' "
                "on the source."
            )
    return text


def _resolve(host: str) -> list[Any]:
    """Every IP a hostname resolves to. A literal address resolves to itself."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise CustomSourceError(f"{host} could not be looked up ({exc}).") from exc
    found: list[Any] = []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError):
            continue
    if not found:
        raise CustomSourceError(f"{host} resolves to no usable address.")
    return found


# ---------------------------------------------------------------------------
# Reading a record out of arbitrary JSON
# ---------------------------------------------------------------------------


def walk_path(payload: Any, path: str) -> Any:
    """Follow a dotted path into parsed JSON. ``None`` when it does not exist.

    ``data.items`` walks two keys; ``data.0.items`` indexes a list. An empty
    path returns the payload itself, which is what an API answering a bare
    array needs.
    """
    current = payload
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _field(record: dict[str, Any], mapped: str, role: str) -> str:
    """One mapped field of a record as text.

    An explicit mapping wins and is followed exactly — including into nested
    paths. Without one, the conventional names for that role are tried, and
    anything else yields ``""``.
    """
    if mapped:
        value = walk_path(record, mapped)
        return "" if value is None else _stringify(value)
    for candidate in _DEFAULT_FIELDS.get(role, ()):
        if candidate in record and record[candidate] is not None:
            return _stringify(record[candidate])
    return ""


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, dict)):
        import json  # noqa: PLC0415 — only for the unusual shape

        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


_ISO_LIKE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?")


def _timestamp(raw: str) -> str:
    """A record's time as ISO-8601 UTC, or ``""`` when it is unreadable.

    Handles the three shapes that actually occur: ISO-8601, a Unix epoch (in
    seconds or milliseconds), and an RFC-2822 date from a feed. Anything else
    yields empty rather than a guess — a wrong timestamp puts a memory in the
    wrong year, which is worse than none.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.replace(".", "", 1).isdigit():
        try:
            number = float(text)
        except ValueError:
            return ""
        # Milliseconds if it is far past a plausible second-based date.
        if number > 3_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return ""
    match = _ISO_LIKE.match(text)
    if match:
        year, month, day, hour, minute, second = match.groups()
        try:
            moment = datetime(
                int(year), int(month), int(day), int(hour), int(minute),
                int(second or 0), tzinfo=UTC,
            )
        except ValueError:
            return ""
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        from email.utils import parsedate_to_datetime  # noqa: PLC0415 — feeds only

        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_record(
    record: Any, mapping: dict[str, str], *, source_url: str, index: int
) -> RawItem | None:
    """One API record as a :class:`RawItem`, or ``None`` when it holds nothing.

    A record with no text at all is dropped rather than stored empty: an item
    with an empty body ranks in search and says nothing, which is worse than
    not existing.
    """
    if not isinstance(record, dict):
        # A bare list of strings is a legitimate, if unusual, answer.
        text = _stringify(record).strip()
        if not text:
            return None
        return RawItem(
            external_id=f"{index:08d}",
            body=text[:MAX_BODY_CHARS],
            permalink=source_url,
            timestamp_utc="",
            title=text.splitlines()[0][:200],
        )

    body = _field(record, mapping.get("body", ""), "body")
    title = _field(record, mapping.get("title", ""), "title")
    if not body.strip() and not title.strip():
        return None
    external_id = _field(record, mapping.get("id", ""), "id") or f"{index:08d}"
    return RawItem(
        external_id=external_id[:400],
        body=(body or title)[:MAX_BODY_CHARS],
        permalink=_field(record, mapping.get("permalink", ""), "permalink") or source_url,
        timestamp_utc=_timestamp(_field(record, mapping.get("timestamp", ""), "timestamp")),
        title=title[:400],
        author_raw=_field(record, mapping.get("author", ""), "author"),
        metadata={"custom_source": True},
    )


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------

_ENTRY_RE = re.compile(r"<(item|entry)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _feed_field(entry: str, *names: str) -> str:
    """One element's text out of a feed entry, tags stripped."""
    for name in names:
        match = re.search(
            rf"<{name}\b[^>]*>(.*?)</{name}>", entry, re.DOTALL | re.IGNORECASE
        )
        if match:
            return _unescape(_TAG_RE.sub("", match.group(1))).strip()
        # Atom's <link href="..."/> carries its value in an attribute.
        attr = re.search(rf'<{name}\b[^>]*href="([^"]+)"', entry, re.IGNORECASE)
        if attr:
            return _unescape(attr.group(1)).strip()
    return ""


def _unescape(text: str) -> str:
    from html import unescape  # noqa: PLC0415 — tiny, local

    return unescape(text.replace("<![CDATA[", "").replace("]]>", ""))


def _feed_items(markup: str, source_url: str) -> Iterator[RawItem]:
    """Parse an RSS or Atom document with the stdlib only.

    Regex rather than an XML parser on purpose: real feeds in the wild are
    frequently not well-formed (unescaped ampersands, stray tags), and a
    strict parser rejects the whole document over one bad character in one
    entry. This reads the entries it can and skips the rest.
    """
    for index, match in enumerate(_ENTRY_RE.finditer(markup)):
        entry = match.group(0)
        title = _feed_field(entry, "title")
        body = _feed_field(entry, "content:encoded", "content", "description", "summary")
        if not title and not body:
            continue
        yield RawItem(
            external_id=(
                _feed_field(entry, "guid", "id")
                or _feed_field(entry, "link")
                or f"{index:08d}"
            )[:400],
            body=(body or title)[:MAX_BODY_CHARS],
            permalink=_feed_field(entry, "link") or source_url,
            timestamp_utc=_timestamp(
                _feed_field(entry, "pubDate", "published", "updated", "dc:date")
            ),
            title=title[:400],
            author_raw=_feed_field(entry, "author", "dc:creator"),
            metadata={"custom_source": True, "feed": True},
        )


# ---------------------------------------------------------------------------
# The connector
# ---------------------------------------------------------------------------


class CustomSourceConnector:
    """Read a user-configured HTTP endpoint or feed as raw items.

    Config keys:

    - ``mode``: ``"http"`` (default) or ``"feed"``.
    - ``url``: the address to fetch. Required.
    - ``secret_slot``: name of a stored credential, resolved through the Jarvis
      secret chain. Never a literal key in the config (AP-12).
    - ``auth_style``: ``"bearer"`` (default), ``"header"`` or ``"query"``.
    - ``auth_name``: header or query-parameter name for the two latter styles.
    - ``items_path``: dotted path to the record list (``""`` = the whole body).
    - ``fields``: mapping of ``id`` / ``title`` / ``body`` / ``timestamp`` /
      ``permalink`` / ``author`` onto the record's own field names.
    - ``page_param`` + ``page_start``: page-number paging.
    - ``cursor_path`` + ``cursor_param``: cursor paging.
    - ``since_param``: parameter carrying the incremental cursor.
    - ``allow_private_network``: fetch an address on a private range.
    """

    id = "custom-source"
    label = "Custom source"
    auth = AuthKind.APIKEY
    capabilities = ConnectorCapabilities(
        backfill=True,
        # Cursor rather than NONE even without a since-parameter: a re-read is
        # idempotent, so the worst case is repeated work, never duplicates.
        incremental=IncrementalMode.CURSOR,
        deletes=False,
        refresh_interval_s=900.0,
        reconcile_interval_s=86_400.0,
    )

    async def backfill(
        self, ctx: ConnectorContext, checkpoint: str | None = None
    ) -> AsyncIterator[RawItem]:
        async for item in self._read(ctx, since=""):
            yield item

    async def incremental(
        self, ctx: ConnectorContext, cursor: str | None = None
    ) -> AsyncIterator[RawItem]:
        async for item in self._read(ctx, since=str(cursor or "")):
            yield item

    # -- internals ---------------------------------------------------------

    async def _read(self, ctx: ConnectorContext, *, since: str) -> AsyncIterator[RawItem]:
        config = ctx.config or {}
        mode = str(config.get("mode") or "http").strip().lower()
        if mode not in MODES:
            raise CustomSourceError(
                f"{mode!r} is not a mode this understands — use "
                f"{' or '.join(MODES)}."
            )
        url = check_url(
            str(config.get("url") or ""),
            allow_private=bool(config.get("allow_private_network")),
        )
        client = await self._client()
        try:
            if mode == "feed":
                markup = await self._fetch_text(client, url, config, ctx, params={})
                for item in _feed_items(markup, url):
                    yield item
                return
            async for item in self._read_http(client, url, config, ctx, since):
                yield item
        finally:
            await client.aclose()

    async def _read_http(
        self,
        client: Any,
        url: str,
        config: dict[str, Any],
        ctx: ConnectorContext,
        since: str,
    ) -> AsyncIterator[RawItem]:
        import json  # noqa: PLC0415 — lazy

        mapping = {
            str(key): str(value)
            for key, value in (config.get("fields") or {}).items()
        }
        items_path = str(config.get("items_path") or "")
        page_param = str(config.get("page_param") or "")
        cursor_param = str(config.get("cursor_param") or "")
        cursor_path = str(config.get("cursor_path") or "")
        since_param = str(config.get("since_param") or "")

        params: dict[str, str] = {}
        if since_param and since:
            params[since_param] = since
        page = int(config.get("page_start") or 1)
        cursor = ""
        seen_cursors: set[str] = set()
        seen = 0

        for _round in range(MAX_PAGES):
            call_params = dict(params)
            if page_param:
                call_params[page_param] = str(page)
            if cursor_param and cursor:
                call_params[cursor_param] = cursor

            raw = await self._fetch_text(client, url, config, ctx, params=call_params)
            try:
                payload = json.loads(raw)
            except ValueError as exc:
                raise CustomSourceError(
                    f"{url} did not answer with JSON ({exc}). If it is an RSS "
                    "or Atom feed, set this source's mode to 'feed'."
                ) from exc

            records = walk_path(payload, items_path)
            if records is None and not items_path:
                records = payload
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list):
                raise CustomSourceError(
                    f"{url} answered with no list of records at "
                    f"{items_path or 'the top level'}. Point 'items_path' at "
                    "the field holding the list."
                )
            if not records:
                return

            for record in records:
                item = map_record(record, mapping, source_url=url, index=seen)
                seen += 1
                if item is not None:
                    yield item
                if seen >= MAX_ITEMS:
                    log.info(
                        "custom source %s: stopped at the %d-item ceiling",
                        ctx.source_id,
                        MAX_ITEMS,
                    )
                    return

            if cursor_path:
                next_cursor = walk_path(payload, cursor_path)
                next_cursor = "" if next_cursor is None else str(next_cursor)
                # Any cursor already used ends the walk, not just a repeat of
                # the previous one: an API that alternates A→B→A→B is an
                # ordinary bug, and comparing only against the last value
                # would follow it until the page ceiling.
                if not next_cursor or next_cursor in seen_cursors:
                    return
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                continue
            if page_param:
                page += 1
                continue
            return

    async def _client(self) -> Any:
        import httpx  # noqa: PLC0415 — lazy (AP-26)

        return httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_S,
            follow_redirects=False,  # a redirect could aim anywhere; see check_url
        )

    async def _fetch_text(
        self,
        client: Any,
        url: str,
        config: dict[str, Any],
        ctx: ConnectorContext,
        *,
        params: dict[str, str],
    ) -> str:
        import httpx  # noqa: PLC0415 — lazy

        headers = {"Accept": "application/json, application/xml, text/xml, */*"}
        call_params = dict(params)
        secret = self._secret(ctx, config)
        if secret:
            style = str(config.get("auth_style") or "bearer").strip().lower()
            name = str(config.get("auth_name") or "").strip()
            if style == "header":
                headers[name or "Authorization"] = secret
            elif style == "query":
                call_params[name or "token"] = secret
            else:
                headers["Authorization"] = f"Bearer {secret}"

        try:
            response = await client.get(url, headers=headers, params=call_params)
        except httpx.HTTPError as exc:
            raise CustomSourceError(f"{url} could not be reached ({exc}).") from exc
        if response.status_code >= 400:
            raise CustomSourceError(
                f"{url} answered {response.status_code}. "
                + (
                    "Check the credential on this source."
                    if response.status_code in (401, 403)
                    else "Check the address and any parameters."
                )
            )
        body = response.content
        if len(body) > MAX_RESPONSE_BYTES:
            raise CustomSourceError(
                f"{url} answered with more than "
                f"{MAX_RESPONSE_BYTES // (1024 * 1024)} MB in one response; "
                "use paging so it arrives in parts."
            )
        return body.decode(response.encoding or "utf-8", errors="replace")

    def _secret(self, ctx: ConnectorContext, config: dict[str, Any]) -> str:
        """The configured credential, resolved through the Jarvis secret chain.

        A literal key in the config is deliberately NOT supported (AP-12): it
        would end up in `jarvis.toml`, which is exactly where credentials must
        never live.
        """
        slot = str(config.get("secret_slot") or "").strip()
        if not slot or ctx.secret_get is None:
            return ""
        try:
            return str(ctx.secret_get(slot) or "")
        except Exception:  # noqa: BLE001 — a missing credential is not a crash
            log.debug("custom source: credential %s unavailable", slot, exc_info=True)
            return ""
