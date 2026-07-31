"""Shared plumbing for the Google-family pull adapters (Gmail, Calendar, Drive).

The three Google integrations are separate marketplace plugins with separate
token entries (they share one OAuth *client*, never one grant), so each
adapter resolves its OWN plugin id — but everything else about talking to a
Google API is identical: bearer-token requests, JSON envelopes, 429/quota
behaviour with a ``Retry-After`` header, and the same honest error sentences
when a connection is missing, expired, or rejected. That sameness lives here
exactly once.

Nothing in this module is an adapter: it never yields items, never touches
the store, and never decides what is worth importing. It is the transport
floor the three adapters stand on, with an injectable ``httpx`` transport so
every test drives it offline.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import html as html_module
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

log = logging.getLogger(__name__)

__all__ = [
    "BODY_CAP",
    "TIMEOUT",
    "TRUNCATION_MARKER",
    "GoogleAdapterError",
    "cap_body",
    "cursor_cutoff",
    "decode_base64url",
    "decode_base64url_bytes",
    "google_get",
    "google_get_json",
    "google_token",
    "iso_utc_from_ms",
    "ms_to_ns",
    "strip_html",
    "to_ns",
]

#: Generous on read: exporting a large document is not instant, but a sync
#: must not hang forever on one item either.
TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

#: A single item's body never exceeds this many characters (memory-shaped
#: scope: a knowledge base wants the text, not a bulk archive).
BODY_CAP = 1_000_000

#: Appended when a body was cut at :data:`BODY_CAP` — the record must say it
#: is incomplete rather than imply the text ends where the cap fell.
TRUNCATION_MARKER = "\n\n[truncated: this item exceeded the 1 MB import cap]"

#: Bounded retries for rate-limited requests: respect ``Retry-After`` when
#: present, back off briefly when not, and give up honestly after this many
#: attempts — a sync must never spin on a throttled API.
_MAX_ATTEMPTS = 3
_MAX_RETRY_WAIT = 30.0


class GoogleAdapterError(RuntimeError):
    """A pull step failed. The message is safe to show and never holds the token."""


def google_token(plugin_id: str, product: str) -> str:
    """The marketplace token for one Google plugin, or an honest refusal.

    ``plugin_id`` is the marketplace id (``gmail`` / ``google_calendar`` /
    ``google_drive``); ``product`` is the display name the error sentences
    carry. The token itself never appears in any message.
    """
    from jarvis.marketplace.token_store import TokenStore  # noqa: PLC0415 — lazy

    try:
        tokens = TokenStore().load(plugin_id)
    except Exception as exc:  # noqa: BLE001 — a locked keyring is not a crash
        raise GoogleAdapterError(
            f"The {product} connection could not be read from this machine's "
            "credential store."
        ) from exc
    if tokens is None:
        raise GoogleAdapterError(
            f"{product} is not connected. Connect it in the Plugins store first."
        )
    if tokens.needs_reauth or not tokens.access:
        raise GoogleAdapterError(
            f"The {product} connection expired. Reconnect it in the Plugins store."
        )
    return str(tokens.access)


def _rate_limited(response: httpx.Response) -> bool:
    """True when the answer means "slow down", regardless of the status code.

    Google APIs signal throttling as plain 429 AND as 403 with a
    rate/quota reason in the body; both deserve the same bounded retry.
    """
    if response.status_code == 429:
        return True
    if response.status_code == 403:
        text = response.text.lower()
        return "ratelimit" in text or "rate limit" in text or "quota" in text
    return False


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after", "")
    try:
        wait = float(header)
    except ValueError:
        wait = float(2**attempt)
    return min(max(wait, 0.0), _MAX_RETRY_WAIT)


async def google_get(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    product: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    tolerate: tuple[int, ...] = (),
) -> httpx.Response:
    """One GET with bounded rate-limit retries; every failure becomes a sentence.

    ``tolerate`` lists status codes the CALLER wants to handle (e.g. a 404 on
    a probe, a 403 on one file's export) — those come back as the response.
    Credential failures (401) and exhausted rate budgets always raise: they
    concern the whole sync, never a single item.
    """
    request_headers = {"Authorization": f"Bearer {token}"}
    if headers:
        request_headers.update(headers)

    response: httpx.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await client.get(url, headers=request_headers, params=params)
        except httpx.HTTPError as exc:
            raise GoogleAdapterError(
                f"{product} was unreachable ({type(exc).__name__}). Check this "
                "machine's internet connection."
            ) from exc
        if not _rate_limited(response):
            break
        if attempt + 1 >= _MAX_ATTEMPTS:
            break
        await asyncio.sleep(_retry_wait(response, attempt))
    if response is None:  # pragma: no cover - loop always runs at least once
        raise GoogleAdapterError(f"{product} could not be reached.")

    if _rate_limited(response):
        raise GoogleAdapterError(
            f"{product}'s rate limit is exhausted. The next sync continues "
            "where this one stopped."
        )
    if response.status_code in tolerate:
        return response
    if response.status_code == 401:
        raise GoogleAdapterError(
            f"{product} rejected the connection. Reconnect it in the Plugins store."
        )
    if response.status_code == 403:
        raise GoogleAdapterError(
            f"{product} denied access (HTTP 403). The connection may lack the "
            "required scope — reconnect it in the Plugins store."
        )
    if response.status_code >= 400:
        raise GoogleAdapterError(
            f"{product} answered HTTP {response.status_code} for this request."
        )
    return response


async def google_get_json(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    product: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A JSON GET; a non-object answer degrades to an empty dict."""
    response = await google_get(client, url, token, product, params=params)
    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleAdapterError(
            f"{product} answered with something that is not JSON."
        ) from exc
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# Time — the cursor convention shared with the reference adapter
# ---------------------------------------------------------------------------


def to_ns(iso: str) -> int:
    """ISO-8601 → nanoseconds since the epoch; 0 when unparseable."""
    if not iso:
        return 0
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000_000)


def ms_to_ns(ms: Any) -> int:
    """Epoch milliseconds (Gmail's ``internalDate``) → nanoseconds; 0 on junk."""
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return 0
    return value * 1_000_000 if value > 0 else 0


def iso_utc_from_ms(ms: Any) -> str:
    """Epoch milliseconds → ``YYYY-MM-DDTHH:MM:SSZ``; empty string on junk."""
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def cursor_cutoff(checkpoint: str | None) -> datetime | None:
    """The stored numeric cursor as an aware UTC moment, one day earlier.

    The cursor is nanoseconds since the epoch (the sync runner's convention,
    advanced from ``metadata["mtime_ns"]``). A day is subtracted before
    querying so a boundary can never skip an item; re-yielded items upsert as
    ``unchanged``. A non-numeric checkpoint — the bridge passes the last
    item's external id during a backfill — is NOT a cursor and returns
    ``None``; the adapters resume positionally from it instead.
    """
    if not checkpoint:
        return None
    try:
        seconds = int(checkpoint) / 1_000_000_000
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC) - timedelta(days=1)


# ---------------------------------------------------------------------------
# Text — capping, HTML stripping, base64url
# ---------------------------------------------------------------------------


def cap_body(text: str) -> tuple[str, bool]:
    """``(body, truncated)`` — the text cut at :data:`BODY_CAP` and marked."""
    if len(text) <= BODY_CAP:
        return text, False
    return text[:BODY_CAP] + TRUNCATION_MARKER, True


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_BREAK_RE = re.compile(r"<\s*(br|/p|/div|/li|/tr|/h[1-6]|/blockquote)\s*/?\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


def strip_html(markup: str) -> str:
    """HTML → readable plain text. Regex only, never an LLM call (AP-11 spirit)."""
    text = _SCRIPT_STYLE_RE.sub(" ", markup)
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    # Unescape entities, then fold non-breaking spaces into ordinary ones:
    # \xa0 survives ``&nbsp;`` and reads as junk in a plain-text memory.
    text = html_module.unescape(text).replace("\xa0", " ")
    lines = [_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def decode_base64url_bytes(data: str) -> bytes:
    """Gmail's base64url payload → raw bytes; empty on junk, never a crash.

    Attachments must stop here rather than at :func:`decode_base64url`: a PDF
    or a Word file decoded as UTF-8 comes out as replacement characters, and
    what the extractor needs is the bytes exactly as they were sent.
    """
    if not data:
        return b""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, binascii.Error):
        return b""


def decode_base64url(data: str) -> str:
    """Gmail's base64url body data → text; empty string on junk, never a crash."""
    return decode_base64url_bytes(data).decode("utf-8", errors="replace")
