"""gmail tool — read + send mail via the Gmail REST API.

Wired Node-free: the marketplace's PKCE-loopback flow stores the user's Gmail
OAuth token in the credential store (key ``plugin_gmail_tokens``); this tool
reads that access token and calls the Gmail REST API directly. Keeping it in
the marketplace token model is what satisfies the "stays connected until you
disconnect" requirement (the refresh scheduler keeps the token fresh).

Router-tier tool (persona-mandate set), like ``search_web`` — risk_tier ``ask``
because sending mail is consequential (echo-confirm before send).
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# User-facing error strings (English per CLAUDE.md; the brain rephrases to the
# user's language). Kept distinct so the brain can tell "never connected" from
# "was connected, token died — reconnect".
_NOT_CONNECTED = "Gmail is not connected — connect it in the Plugins view."
_NEEDS_RECONNECT = (
    "Gmail authorization expired and could not be renewed — "
    "please reconnect Gmail in the Plugins view."
)

# A raw Gmail ``format=full`` message is ~20k+ chars of Received/ARC/DKIM
# headers, the full MIME part tree and base64 body — feeding it into the model
# context slowed a voice turn to ~20 s and added no answer value (live bug
# 2026-07-01 "Was steht alles in meinen E-Mails drin?"). We project a read down
# to the fields that actually answer a mail question and cap the plain-text body.
_GMAIL_BODY_CHAR_CAP = 2000


def _decode_b64url(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _strip_html(html: str) -> str:
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _find_part_data(payload: dict[str, Any], mime: str) -> str | None:
    """Depth-first search for the first MIME part of ``mime`` that carries body
    data, returning the raw base64url string (or ``None``)."""
    if payload.get("mimeType") == mime:
        data = (payload.get("body") or {}).get("data")
        if data:
            return data
    for part in payload.get("parts") or []:
        found = _find_part_data(part, mime)
        if found:
            return found
    return None


def _slim_gmail_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a raw Gmail ``format=full`` message to sender/recipients/subject/
    date/label-state/snippet plus a decoded, length-capped plain-text body.
    Prefers a ``text/plain`` part; falls back to a stripped ``text/html`` part."""
    payload = raw.get("payload") or {}
    headers = {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in (payload.get("headers") or [])
    }
    plain = _find_part_data(payload, "text/plain")
    if plain:
        body = _decode_b64url(plain)
    else:
        html = _find_part_data(payload, "text/html")
        body = _strip_html(_decode_b64url(html)) if html else ""
    if len(body) > _GMAIL_BODY_CHAR_CAP:
        body = body[:_GMAIL_BODY_CHAR_CAP] + "… [truncated]"
    return {
        "id": raw.get("id"),
        "threadId": raw.get("threadId"),
        "labelIds": raw.get("labelIds", []),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": raw.get("snippet", ""),
        "body": body,
    }


def _default_token_provider() -> str | None:
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("gmail")
    return tokens.access if tokens is not None else None


async def _default_refresher(observed_access_token: str | None = None) -> bool:
    """Refresh the stored Gmail token in place. Returns True on success.

    On an un-healable failure (revoked / invalid_client / placeholder client)
    it flags ``needs_reauth`` on the stored token so the Plugins view stops
    showing a green "connected" that lies and offers a Reconnect instead.
    Best-effort: any error returns False, never raises into the tool."""
    from jarvis.marketplace.connect_helpers import build_handler_from_catalog
    from jarvis.marketplace.refresh_scheduler import refresh_plugin_token
    from jarvis.marketplace.token_store import TokenStore

    store = TokenStore()
    attempt = await refresh_plugin_token(
        "gmail",
        store,
        build_handler_from_catalog,
        force=True,
        observed_access_token=observed_access_token,
    )
    return attempt.usable

class GmailRestTool:
    name: str = "gmail"
    risk_tier: str = "ask"
    description: str = (
        "Read and send email from the user's connected Gmail inbox. "
        "Use for 'check my mail', 'any new emails from X', 'read the last mail', "
        "'send an email to X'. Actions: list_messages (search the inbox), "
        "get_message (read one by id), send_message (compose + send). "
        "Requires the Gmail plugin to be connected in the Plugins view."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_messages", "get_message", "send_message"],
                "default": "list_messages",
            },
            "query": {"type": "string", "description": "Gmail search query (list_messages)"},
            "max_results": {"type": "integer", "default": 10},
            "message_id": {"type": "string", "description": "message id (get_message)"},
            "to": {"type": "string", "description": "recipient (send_message)"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["action"],
    }

    def __init__(
        self,
        access_token_provider: Callable[[], str | None] | None = None,
        transport: Any | None = None,
        token_refresher: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        from ._http_pool import HttpClientPool

        self._token_provider = access_token_provider or _default_token_provider
        self._transport = transport
        self._refresher = token_refresher
        # Keep-alive pool: reuse one client (one warm TLS connection to Gmail)
        # across list/get/send instead of a fresh handshake per request. The
        # tool is built once per BrainManager, so the connection stays warm for
        # the whole session (a read is list_messages + get_message = 2 hops).
        self._pool = HttpClientPool(transport=transport)

    # -- internal helpers ---------------------------------------------------

    def _bearer(self) -> dict[str, str] | None:
        token = self._token_provider()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "User-Agent": "Personal-Jarvis/1.0"}

    async def _with_auth_retry(
        self, do_call: Callable[[dict[str, str]], Awaitable[Any]]
    ) -> Any:
        """Run an authenticated Gmail call; on a 401 refresh once and retry.

        Centralises the self-heal so every action (list/get/send) recovers from
        an expired access token instead of returning a hard auth error. Returns
        the call's JSON on success, or a ``{"error": ...}`` dict the caller maps
        to a ToolResult."""
        import httpx

        headers = self._bearer()
        if headers is None:
            return {"error": _NOT_CONNECTED}
        try:
            return await do_call(headers)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
        # 401 — token likely expired. Try exactly one refresh + retry.
        observed_token = headers["Authorization"].removeprefix("Bearer ")
        try:
            if self._refresher is None:
                refreshed = bool(await _default_refresher(observed_token))
            else:
                refreshed = bool(await self._refresher())
        except Exception:  # noqa: BLE001 — refresher must never crash the tool
            refreshed = False
        if not refreshed:
            return {"error": _NEEDS_RECONNECT}
        headers = self._bearer()
        if headers is None:
            return {"error": _NEEDS_RECONNECT}
        return await do_call(headers)

    async def _get(self, path: str, params: dict[str, Any], headers: dict[str, str]):
        client = self._pool.client()
        resp = await client.get(f"{_GMAIL_BASE}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json_body: dict[str, Any], headers: dict[str, str]):
        client = self._pool.client()
        resp = await client.post(f"{_GMAIL_BASE}{path}", json=json_body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # -- public actions (also directly unit-testable) -----------------------

    async def list_messages(self, *, query: str = "", max_results: int = 10) -> dict[str, Any]:
        return await self._with_auth_retry(
            lambda headers: self._get(
                "/messages", {"q": query, "maxResults": max_results}, headers
            )
        )

    async def get_message(self, *, message_id: str) -> dict[str, Any]:
        return await self._with_auth_retry(
            lambda headers: self._get(
                f"/messages/{message_id}", {"format": "full"}, headers
            )
        )

    async def send_message(self, *, to: str, subject: str = "", body: str = "") -> dict[str, Any]:
        mime = f"To: {to}\r\nSubject: {subject}\r\n\r\n{body}"
        raw = base64.urlsafe_b64encode(mime.encode("utf-8")).decode("ascii")
        return await self._with_auth_retry(
            lambda headers: self._post("/messages/send", {"raw": raw}, headers)
        )

    # -- Tool protocol ------------------------------------------------------

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        """Per-action risk tier (consulted by ``RiskTierEvaluator``).

        Only ``send_message`` is consequential — it keeps the ``ask`` tier and
        the two-turn echo-confirm before sending. Reads (``list_messages`` /
        ``get_message``) are ``safe`` so a morning briefing can check unread
        mail without a spurious send confirmation (forensic 2026-06-19,
        session dc533e39). An unrecognised action stays conservative
        (``ask``), never silently safe."""
        action = (args.get("action") or "list_messages").strip()
        if action in ("list_messages", "get_message"):
            return "safe"
        return "ask"

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "list_messages").strip()
        try:
            if action == "list_messages":
                out = await self.list_messages(
                    query=args.get("query", ""),
                    max_results=int(args.get("max_results", 10)),
                )
            elif action == "get_message":
                mid = args.get("message_id")
                if not mid:
                    return ToolResult(success=False, output=None, error="message_id missing")
                out = await self.get_message(message_id=mid)
            elif action == "send_message":
                to = args.get("to")
                if not to:
                    return ToolResult(success=False, output=None, error="recipient 'to' missing")
                out = await self.send_message(
                    to=to, subject=args.get("subject", ""), body=args.get("body", "")
                )
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))

        if isinstance(out, dict) and out.get("error"):
            return ToolResult(success=False, output=None, error=out["error"])
        # A read returns the whole raw message; slim it before it reaches the
        # brain's context (list_messages is already just IDs, send returns a tiny
        # ack — neither needs projecting).
        if action == "get_message" and isinstance(out, dict):
            out = _slim_gmail_message(out)
        return ToolResult(success=True, output=out)
