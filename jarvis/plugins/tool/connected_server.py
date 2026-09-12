"""Bundled MCP bridge for fixed, documented service REST operations.

Run with ``python -m jarvis.plugins.tool.connected_server <plugin-id>``.
The marketplace supplies only that plugin's access token through the process
environment. No native APIs or optional cloud SDKs are imported at startup.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from string import Formatter
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from .connected_operations import BASES, OPERATIONS, Operation


def operation_schema(operation: Operation) -> dict[str, Any]:
    fields = [field for _, field, _, _ in Formatter().parse(operation.path) if field]
    properties: dict[str, Any] = {
        field: {"type": "string", "minLength": 1, "maxLength": 1024} for field in fields
    }
    if operation.query:
        properties["query_params"] = {
            "type": "object",
            "properties": {
                key: {"type": ["string", "integer", "boolean"]} for key in operation.query
            },
            "additionalProperties": False,
        }
    if operation.body != "none":
        body_type = {"html": "string", "binary": "string", "array": "array"}.get(
            operation.body, "object"
        )
        properties["body"] = {"type": body_type}
        fields.append("body")
        if operation.body == "binary":
            properties["body"]["description"] = "Base64-encoded file bytes, up to 20 MiB decoded."
            properties["body"]["maxLength"] = 28_000_000
    return {
        "type": "object",
        "properties": properties,
        "required": fields,
        "additionalProperties": False,
    }


class ConnectedRestClient:
    """One pool per connected service; requests never follow credential redirects."""

    def __init__(
        self, plugin_id: str, token: str, *, transport: Any = None, auth_type: str = "pat"
    ) -> None:
        if plugin_id not in OPERATIONS:
            raise ValueError("Unknown bundled connector")
        if not token or "\r" in token or "\n" in token:
            raise ValueError("Connect this plugin in the Plugins view first")
        self.plugin_id = plugin_id
        self._token = token
        self._auth_type = auth_type
        self._operations = {operation.name: operation for operation in OPERATIONS[plugin_id]}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(45, connect=10),
            transport=transport,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": op.name,
                "description": op.description,
                "inputSchema": operation_schema(op),
                "annotations": {
                    "readOnlyHint": op.method == "GET",
                    "destructiveHint": op.method != "GET",
                    "openWorldHint": True,
                },
            }
            for op in self._operations.values()
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        operation = self._operations.get(name)
        if operation is None:
            raise ValueError("Unknown connector operation")
        errors = list(Draft202012Validator(operation_schema(operation)).iter_errors(arguments))
        if errors:
            # Validation messages can contain entire submitted bodies. Only name
            # the constraint; do not echo mail content or accidentally pasted keys.
            raise ValueError(f"Invalid arguments ({errors[0].validator})")
        identifiers: dict[str, str] = {}
        for _, field, _, _ in Formatter().parse(operation.path):
            if field:
                value = arguments[field]
                if value in {".", ".."} or any(ord(c) < 32 for c in value):
                    raise ValueError("Invalid resource identifier")
                # OData search string literals escape apostrophes by doubling.
                if field == "query":
                    value = value.replace("'", "''")
                identifiers[field] = quote(value, safe="")
        path = operation.path.format(**identifiers)
        if (
            self.plugin_id == "onedrive"
            and name == "upload_file"
            and arguments.get("parent_id") == "root"
        ):
            path = path.replace("/items/root:", "/root:", 1)
        url = path if operation.path.startswith("https://") else BASES[self.plugin_id] + path
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": "Personal-Jarvis/1.0"}
        if self.plugin_id == "figma" and self._auth_type != "oauth":
            headers.pop("Authorization")
            headers["X-Figma-Token"] = self._token
        if self.plugin_id == "linkedin":
            headers["X-Restli-Protocol-Version"] = "2.0.0"
        if self.plugin_id == "meta" and name in {"list_posts", "create_post"}:
            # Page publishing requires a Page token, not the user's login
            # token. Resolve it server-side and never return it to the model.
            page = await self._client.get(
                BASES["meta"] + "/" + identifiers["page_id"],
                params={"fields": "access_token"},
                headers=headers,
            )
            if page.status_code != 200 or not page.json().get("access_token"):
                raise RuntimeError("Meta Page token unavailable; check Page role and permissions")
            headers["Authorization"] = "Bearer " + page.json()["access_token"]
        params = dict(operation.defaults)
        params.update(arguments.get("query_params", {}))
        kwargs: dict[str, Any] = {"headers": headers, "params": params}
        body: Any = arguments.get("body")
        if operation.body == "video":
            try:
                return await self._upload_video(url, headers, params, body)
            except httpx.HTTPError:
                raise RuntimeError(
                    "Video upload interrupted; inspect Studio before retrying"
                ) from None
        if operation.body == "binary":
            try:
                data = base64.b64decode(body, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("File body must be valid base64") from exc
            if len(data) > 20 * 1024 * 1024:
                raise ValueError("Use the provider app for uploads larger than 20 MiB")
            kwargs["content"] = data
            headers["Content-Type"] = "application/octet-stream"
        elif operation.body == "html":
            kwargs["content"] = body.encode("utf-8")
            headers["Content-Type"] = "text/html; charset=utf-8"
        elif operation.body == "billing_query":
            query = body.get("query", "")
            if (
                not isinstance(query, str)
                or not re.match(r"\s*SELECT\b", query, re.I)
                or ";" in query
            ):
                raise ValueError("Billing queries must be one GoogleSQL SELECT statement")
            if body.get("useLegacySql") is not False:
                raise ValueError("Set useLegacySql=false")
            if not str(body.get("maximumBytesBilled", "")).isdigit():
                raise ValueError("Set maximumBytesBilled to bound billable query work")
            kwargs["json"] = body
        elif operation.body != "none":
            kwargs["json"] = body
        try:
            response = await self._client.request(operation.method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"{self.plugin_id}: request failed ({type(exc).__name__}); "
                "outcome may be unknown, check before retrying a write"
            ) from None
        if response.status_code in {401, 403}:
            raise RuntimeError(
                f"{self.plugin_id}: HTTP {response.status_code}; "
                "reconnect in Plugins or check account permissions"
            )
        if response.status_code == 429:
            raise RuntimeError(f"{self.plugin_id}: provider rate limit; retry later")
        if not 200 <= response.status_code < 300:
            # Provider bodies may echo submitted secrets; never propagate them.
            raise RuntimeError(
                f"{self.plugin_id}: HTTP {response.status_code}; "
                "check resource IDs and request fields"
            )
        if response.status_code == 204 or not response.content:
            return {"accepted": True, "http_status": response.status_code}
        if "json" in response.headers.get("content-type", ""):
            return _without_credentials(response.json())
        return {"content": response.text, "content_type": response.headers.get("content-type")}

    async def _upload_video(
        self, url: str, headers: dict[str, str], params: dict[str, Any], body: dict[str, Any]
    ) -> Any:
        path = Path(body.get("file_path", ""))
        if not path.is_absolute() or path.suffix.lower() not in {
            ".mp4",
            ".mov",
            ".webm",
            ".mkv",
            ".avi",
        }:
            raise ValueError("Supply an absolute path to a video file")
        title = body.get("title")
        privacy = body.get("privacyStatus", "private")
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            raise ValueError("Supply a video title of 1 to 100 characters")
        if privacy not in {"private", "unlisted", "public"}:
            raise ValueError("Invalid privacyStatus")
        # Keep one handle across the handshake and upload, avoiding a path
        # replacement between stat and read. Upload writes are never retried.
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            size = os.fstat(handle.fileno()).st_size
            if not size:
                raise ValueError("The video file is empty")
            response = await self._client.post(
                url,
                headers={
                    **headers,
                    "X-Upload-Content-Length": str(size),
                    "X-Upload-Content-Type": "application/octet-stream",
                },
                params=params,
                json={
                    "snippet": {"title": title, "description": body.get("description", "")},
                    "status": {"privacyStatus": privacy},
                },
            )
            if response.status_code != 200:
                raise RuntimeError(f"YouTube upload initialization HTTP {response.status_code}")
            location = response.headers.get("Location", "")
            parsed = urlsplit(location)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "www.googleapis.com"
                or parsed.port not in {None, 443}
                or parsed.username
                or parsed.password
                or not parsed.path.startswith("/upload/youtube/")
            ):
                raise RuntimeError("YouTube returned an unexpected upload destination")

            async def chunks():
                while chunk := await asyncio.to_thread(handle.read, 1024 * 1024):
                    yield chunk

            uploaded = await self._client.put(
                location,
                headers={
                    **headers,
                    "Content-Length": str(size),
                    "Content-Type": "application/octet-stream",
                },
                content=chunks(),
            )
            if uploaded.status_code not in {200, 201}:
                raise RuntimeError(
                    f"YouTube upload HTTP {uploaded.status_code}; inspect Studio before retrying"
                )
            return uploaded.json()
        finally:
            await asyncio.to_thread(handle.close)


def _without_credentials(value: Any) -> Any:
    """Provider credentials are not result content, even if a fields query asks for them."""
    if isinstance(value, dict):
        return {
            key: _without_credentials(item)
            for key, item in value.items()
            if key.lower() not in {"access_token", "refresh_token", "client_secret"}
        }
    if isinstance(value, list):
        return [_without_credentials(item) for item in value]
    return value


async def serve(plugin_id: str) -> None:
    import mcp.server.stdio
    import mcp.types as types
    from mcp.server import Server

    client = ConnectedRestClient(
        plugin_id,
        os.environ.get("JARVIS_CONNECTOR_TOKEN", ""),
        auth_type=os.environ.get("JARVIS_CONNECTOR_AUTH_TYPE", "pat"),
    )

    @asynccontextmanager
    async def lifespan(server: Any):
        try:
            yield {}
        finally:
            await client.close()

    server = Server(plugin_id, lifespan=lifespan)

    @server.list_tools()
    async def list_tools():
        return [types.Tool(**definition) for definition in client.list_tools()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]):
        result = await client.call(name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    if len(sys.argv) != 2 or sys.argv[1] not in OPERATIONS:
        raise SystemExit("Supply one bundled connector ID")
    asyncio.run(serve(sys.argv[1]))
