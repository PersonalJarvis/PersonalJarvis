"""google_drive tool — list, read, create, share and delete files via the
Google Drive REST API (v3).

Wired Node-free and MCP-free: the marketplace's PKCE-loopback flow stores the
user's Google OAuth token in the credential store (key ``plugin_google_drive_
tokens``); this tool reads that access token and calls the Drive REST API
directly, exactly like ``gmail_rest.py`` does for Gmail.

Why native instead of Google's hosted Drive MCP: the catalog used to point
Drive at ``https://drivemcp.googleapis.com/mcp/v1``. That endpoint is a Google
*Workspace* Developer-Preview server; for a consumer @gmail.com account it lets
``initialize`` through (200) but answers every data-plane call with 403
Forbidden (live forensic 2026-07-23: initialize 200 → notifications 202 →
tools/call 403, on every single Drive turn). Nothing client-side fixes that —
the raw Drive REST API v3 returns the same files with the same token, so we
talk to it directly.

Router-tier tool (like ``gmail``/``google_calendar``). Per-action risk tiers:
reads are ``safe``; create/folder are ``monitor`` (audited, no prompt — full
autonomy, matching the usage card); share and delete are ``ask`` (they expose
or destroy data — echo-confirm before acting).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
_DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"

# User-facing error strings (English per CLAUDE.md; the brain rephrases to the
# user's language). Kept distinct so the brain can tell "never connected" from
# "was connected, token died — reconnect".
_NOT_CONNECTED = "Google Drive is not connected — connect it in the Plugins view."
_NEEDS_RECONNECT = (
    "Google Drive authorization expired and could not be renewed — "
    "please reconnect Google Drive in the Plugins view."
)

# Reading a file's content into the model context is bounded like Gmail's body:
# a large document would blow the context and slow the voice turn for no answer
# value. We cap the extracted text and mark the truncation.
_DRIVE_CONTENT_CHAR_CAP = 4000

# Field projection for list/get — only what actually answers a file question,
# incl. the webViewLink (the "Drive URL" the usage card asks us to report back).
_FILE_FIELDS = (
    "id,name,mimeType,modifiedTime,size,webViewLink,"
    "owners(displayName,emailAddress),parents"
)
_LIST_FIELDS = f"files({_FILE_FIELDS}),nextPageToken"

# Google-native types can't be downloaded raw — they must be EXPORTED to a
# concrete format. Map the common ones to a text export MIME.
_GOOGLE_EXPORT_MIME: dict[str, str] = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.script": "application/vnd.google-apps.script+json",
}
_FOLDER_MIME = "application/vnd.google-apps.folder"


def _default_token_provider() -> str | None:
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("google_drive")
    return tokens.access if tokens is not None else None


async def _default_refresher(observed_access_token: str | None = None) -> bool:
    """Refresh the stored Google Drive token in place. Returns True on success.

    On an un-healable failure (revoked / invalid_client / placeholder client) it
    flags ``needs_reauth`` on the stored token so the Plugins view stops showing
    a green "connected" that lies and offers a Reconnect instead. Best-effort:
    any error returns False, never raises into the tool."""
    from jarvis.marketplace.connect_helpers import build_handler_from_catalog
    from jarvis.marketplace.refresh_scheduler import refresh_plugin_token
    from jarvis.marketplace.token_store import TokenStore

    store = TokenStore()
    attempt = await refresh_plugin_token(
        "google_drive",
        store,
        build_handler_from_catalog,
        force=True,
        observed_access_token=observed_access_token,
    )
    return attempt.usable


def _slim_file(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a raw Drive file resource to the fields that answer a file
    question — dropping the long ``owners`` list to a single display name."""
    owners = raw.get("owners") or []
    owner = ""
    if owners:
        o0 = owners[0]
        owner = o0.get("displayName") or o0.get("emailAddress") or ""
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "mimeType": raw.get("mimeType"),
        "modifiedTime": raw.get("modifiedTime"),
        "size": raw.get("size"),
        "url": raw.get("webViewLink"),
        "owner": owner,
    }


def _escape_drive_q(value: str) -> str:
    """Escape a literal for a Drive ``q`` string clause (single-quote + backslash)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveRestTool:
    name: str = "google_drive"
    risk_tier: str = "monitor"
    description: str = (
        "List, search, read, create, share and delete files in the user's "
        "connected Google Drive. Use for 'what's in my Drive', 'find the file "
        "named X', 'read that document', 'create a file', 'share it with X', "
        "'delete that file'. Actions: list_files (search/list — pass a plain "
        "search_text, or a raw Drive q query for power users; empty lists the "
        "most recently modified files), get_file (metadata by id), read_file "
        "(extract text content by id; Google Docs/Sheets are exported to text), "
        "create_file (name + text content, optional folder id), create_folder "
        "(name, optional parent folder id), share_file (id + either an "
        "emailAddress to share with one person, or public=true for a view link), "
        "delete_file (id). Requires the Google Drive plugin to be connected in "
        "the Plugins view. Always report the file name and its Drive URL back."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_files",
                    "get_file",
                    "read_file",
                    "create_file",
                    "create_folder",
                    "share_file",
                    "delete_file",
                ],
                "default": "list_files",
            },
            "search_text": {
                "type": "string",
                "description": "Plain text to match in file name or full text (list_files)",
            },
            "query": {
                "type": "string",
                "description": "Raw Drive 'q' query for power users; overrides search_text",
            },
            "max_results": {"type": "integer", "default": 15},
            "file_id": {"type": "string", "description": "target file id"},
            "name": {
                "type": "string",
                "description": "file/folder name (create_file/create_folder)",
            },
            "content": {"type": "string", "description": "text content (create_file)"},
            "mime_type": {
                "type": "string",
                "description": "MIME type for create_file (default text/plain)",
            },
            "parent_id": {
                "type": "string",
                "description": "target folder id for create_file/create_folder (optional)",
            },
            "email_address": {
                "type": "string",
                "description": "share with this Google account (share_file)",
            },
            "public": {
                "type": "boolean",
                "description": "share_file: make it viewable by anyone with the link",
            },
            "role": {
                "type": "string",
                "enum": ["reader", "writer", "commenter"],
                "default": "reader",
                "description": "permission role for share_file",
            },
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
        # Keep-alive pool: one warm TLS connection to googleapis.com reused
        # across list/get/read instead of a fresh handshake per request.
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
        """Run an authenticated Drive call; on a 401 refresh once and retry.

        Centralises the self-heal so every action recovers from an expired
        access token instead of returning a hard auth error. Returns the call's
        result on success, or a ``{"error": ...}`` dict the caller maps to a
        ToolResult."""
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

    async def _get_json(self, path: str, params: dict[str, Any], headers: dict[str, str]):
        client = self._pool.client()
        resp = await client.get(f"{_DRIVE_BASE}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _get_text(self, path: str, params: dict[str, Any], headers: dict[str, str]) -> str:
        client = self._pool.client()
        resp = await client.get(f"{_DRIVE_BASE}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.text

    # -- public actions (also directly unit-testable) -----------------------

    async def list_files(
        self, *, search_text: str = "", query: str = "", max_results: int = 15
    ) -> dict[str, Any]:
        q = query.strip()
        if not q and search_text.strip():
            esc = _escape_drive_q(search_text.strip())
            q = f"(name contains '{esc}' or fullText contains '{esc}')"
        # Never surface trashed files.
        q = f"({q}) and trashed = false" if q else "trashed = false"
        params = {
            "q": q,
            "pageSize": max(1, min(int(max_results), 100)),
            "fields": _LIST_FIELDS,
            "orderBy": "modifiedTime desc",
            "spaces": "drive",
            "corpora": "user",
        }
        out = await self._with_auth_retry(
            lambda headers: self._get_json("/files", params, headers)
        )
        if isinstance(out, dict) and out.get("error"):
            return out
        files = [_slim_file(f) for f in (out.get("files") or [])]
        return {"files": files, "count": len(files)}

    async def get_file(self, *, file_id: str) -> dict[str, Any]:
        out = await self._with_auth_retry(
            lambda headers: self._get_json(
                f"/files/{file_id}", {"fields": _FILE_FIELDS}, headers
            )
        )
        if isinstance(out, dict) and out.get("error"):
            return out
        return _slim_file(out)

    async def read_file(self, *, file_id: str) -> dict[str, Any]:
        meta = await self.get_file(file_id=file_id)
        if isinstance(meta, dict) and meta.get("error"):
            return meta
        mime = meta.get("mimeType") or ""
        text: str | None = None
        if mime in _GOOGLE_EXPORT_MIME:
            export_mime = _GOOGLE_EXPORT_MIME[mime]
            text = await self._with_auth_retry(
                lambda headers: self._get_text(
                    f"/files/{file_id}/export", {"mimeType": export_mime}, headers
                )
            )
        elif mime.startswith("text/") or mime in (
            "application/json",
            "application/xml",
            "application/csv",
        ):
            text = await self._with_auth_retry(
                lambda headers: self._get_text(f"/files/{file_id}", {"alt": "media"}, headers)
            )
        if isinstance(text, dict) and text.get("error"):
            return text
        if text is None:
            return {**meta, "content": None, "note": "binary file — not read as text"}
        if len(text) > _DRIVE_CONTENT_CHAR_CAP:
            text = text[:_DRIVE_CONTENT_CHAR_CAP] + "… [truncated]"
        return {**meta, "content": text}

    async def create_file(
        self,
        *,
        name: str,
        content: str = "",
        mime_type: str = "text/plain",
        parent_id: str = "",
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {"name": name}
        if parent_id.strip():
            metadata["parents"] = [parent_id.strip()]
        boundary = "jarvis_drive_multipart_boundary"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
            f"{content}\r\n"
            f"--{boundary}--"
        )

        async def _do(headers: dict[str, str]):
            client = self._pool.client()
            h = {
                **headers,
                "Content-Type": f"multipart/related; boundary={boundary}",
            }
            resp = await client.post(
                _DRIVE_UPLOAD,
                params={"uploadType": "multipart", "fields": _FILE_FIELDS},
                content=body.encode("utf-8"),
                headers=h,
            )
            resp.raise_for_status()
            return resp.json()

        out = await self._with_auth_retry(_do)
        if isinstance(out, dict) and out.get("error"):
            return out
        return _slim_file(out)

    async def create_folder(self, *, name: str, parent_id: str = "") -> dict[str, Any]:
        metadata: dict[str, Any] = {"name": name, "mimeType": _FOLDER_MIME}
        if parent_id.strip():
            metadata["parents"] = [parent_id.strip()]

        async def _do(headers: dict[str, str]):
            client = self._pool.client()
            resp = await client.post(
                f"{_DRIVE_BASE}/files",
                params={"fields": _FILE_FIELDS},
                json=metadata,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

        out = await self._with_auth_retry(_do)
        if isinstance(out, dict) and out.get("error"):
            return out
        return _slim_file(out)

    async def share_file(
        self,
        *,
        file_id: str,
        email_address: str = "",
        public: bool = False,
        role: str = "reader",
    ) -> dict[str, Any]:
        if email_address.strip():
            perm = {"type": "user", "role": role, "emailAddress": email_address.strip()}
        elif public:
            perm = {"type": "anyone", "role": role}
        else:
            return {"error": "share_file needs either email_address or public=true"}

        async def _do(headers: dict[str, str]):
            client = self._pool.client()
            resp = await client.post(
                f"{_DRIVE_BASE}/files/{file_id}/permissions",
                json=perm,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

        out = await self._with_auth_retry(_do)
        if isinstance(out, dict) and out.get("error"):
            return out
        # Return the file's shareable link so the brain can read it back.
        meta = await self.get_file(file_id=file_id)
        return {"shared": True, "permission": out, "file": meta}

    async def delete_file(self, *, file_id: str) -> dict[str, Any]:
        async def _do(headers: dict[str, str]):
            client = self._pool.client()
            resp = await client.delete(f"{_DRIVE_BASE}/files/{file_id}", headers=headers)
            resp.raise_for_status()
            return {"deleted": True, "file_id": file_id}

        return await self._with_auth_retry(_do)

    # -- Tool protocol ------------------------------------------------------

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        """Per-action risk tier (consulted by ``RiskTierEvaluator``).

        Reads are ``safe``; create/folder are ``monitor`` (audited, no prompt —
        full autonomy, matching the usage card). ``share_file`` and
        ``delete_file`` are consequential (they expose data outward or destroy
        it), so they keep the ``ask`` tier + the two-turn echo-confirm. An
        unrecognised action stays conservative (``ask``), never silently safe."""
        action = (args.get("action") or "list_files").strip()
        if action in ("list_files", "get_file", "read_file"):
            return "safe"
        if action in ("create_file", "create_folder"):
            return "monitor"
        return "ask"  # share_file / delete_file / unknown

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "list_files").strip()
        try:
            if action == "list_files":
                out = await self.list_files(
                    search_text=args.get("search_text", ""),
                    query=args.get("query", ""),
                    max_results=int(args.get("max_results", 15)),
                )
            elif action == "get_file":
                fid = args.get("file_id")
                if not fid:
                    return ToolResult(success=False, output=None, error="file_id missing")
                out = await self.get_file(file_id=fid)
            elif action == "read_file":
                fid = args.get("file_id")
                if not fid:
                    return ToolResult(success=False, output=None, error="file_id missing")
                out = await self.read_file(file_id=fid)
            elif action == "create_file":
                nm = args.get("name")
                if not nm:
                    return ToolResult(success=False, output=None, error="name missing")
                out = await self.create_file(
                    name=nm,
                    content=args.get("content", ""),
                    mime_type=args.get("mime_type", "text/plain"),
                    parent_id=args.get("parent_id", ""),
                )
            elif action == "create_folder":
                nm = args.get("name")
                if not nm:
                    return ToolResult(success=False, output=None, error="name missing")
                out = await self.create_folder(name=nm, parent_id=args.get("parent_id", ""))
            elif action == "share_file":
                fid = args.get("file_id")
                if not fid:
                    return ToolResult(success=False, output=None, error="file_id missing")
                out = await self.share_file(
                    file_id=fid,
                    email_address=args.get("email_address", ""),
                    public=bool(args.get("public", False)),
                    role=args.get("role", "reader"),
                )
            elif action == "delete_file":
                fid = args.get("file_id")
                if not fid:
                    return ToolResult(success=False, output=None, error="file_id missing")
                out = await self.delete_file(file_id=fid)
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))

        if isinstance(out, dict) and out.get("error"):
            return ToolResult(success=False, output=None, error=out["error"])
        return ToolResult(success=True, output=out)
