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
from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _default_token_provider() -> str | None:
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("gmail")
    return tokens.access if tokens is not None else None


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
    ) -> None:
        self._token_provider = access_token_provider or _default_token_provider
        self._transport = transport

    # -- internal helpers ---------------------------------------------------

    def _bearer(self) -> dict[str, str] | None:
        token = self._token_provider()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "User-Agent": "Personal-Jarvis/1.0"}

    async def _get(self, path: str, params: dict[str, Any], headers: dict[str, str]):
        import httpx

        async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as client:
            resp = await client.get(f"{_GMAIL_BASE}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json_body: dict[str, Any], headers: dict[str, str]):
        import httpx

        async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as client:
            resp = await client.post(f"{_GMAIL_BASE}{path}", json=json_body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # -- public actions (also directly unit-testable) -----------------------

    async def list_messages(self, *, query: str = "", max_results: int = 10) -> dict[str, Any]:
        headers = self._bearer()
        if headers is None:
            return {"error": "Gmail is not connected — connect it in the Plugins view."}
        return await self._get(
            "/messages", {"q": query, "maxResults": max_results}, headers
        )

    async def get_message(self, *, message_id: str) -> dict[str, Any]:
        headers = self._bearer()
        if headers is None:
            return {"error": "Gmail is not connected — connect it in the Plugins view."}
        return await self._get(f"/messages/{message_id}", {"format": "full"}, headers)

    async def send_message(self, *, to: str, subject: str = "", body: str = "") -> dict[str, Any]:
        headers = self._bearer()
        if headers is None:
            return {"error": "Gmail is not connected — connect it in the Plugins view."}
        mime = f"To: {to}\r\nSubject: {subject}\r\n\r\n{body}"
        raw = base64.urlsafe_b64encode(mime.encode("utf-8")).decode("ascii")
        return await self._post("/messages/send", {"raw": raw}, headers)

    # -- Tool protocol ------------------------------------------------------

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
        return ToolResult(success=True, output=out)
