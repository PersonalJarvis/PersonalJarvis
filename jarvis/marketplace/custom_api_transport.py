"""Bounded upload encoding and query-key injection below HTTP request logging."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from jarvis.marketplace.custom_api import ApiAction

UPLOAD_LIMIT = 64 * 1024 * 1024
QUERY_AUTH_EXTENSION = "jarvis_api_query_auth"


class ApiTransport(httpx.AsyncBaseTransport):
    """Keep query credentials off public request objects and HTTPX log lines."""

    def __init__(self, inner: Any = None) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._inner is None:
            self._inner = httpx.AsyncHTTPTransport(trust_env=False)
        credential = request.extensions.pop(QUERY_AUTH_EXTENSION, None)
        if credential is None:
            return await self._inner.handle_async_request(request)
        name, value = credential
        private = httpx.Request(
            request.method,
            request.url.copy_set_param(name, value),
            headers=request.headers,
            stream=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await self._inner.handle_async_request(private)
            response.request = request
            return response
        except httpx.RequestError as exc:
            exc.request = request
            raise
        finally:
            private.url = request.url

    async def aclose(self) -> None:
        if self._inner is not None:
            await self._inner.aclose()


def _file(value: Any, remaining: int) -> tuple[str, bytes, str]:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("Uploads require an absolute local file path")
    path = Path(value)
    if not path.is_file() or path.stat().st_size > remaining:
        raise ValueError("The upload is unavailable or exceeds the 64 MiB limit")
    with path.open("rb") as stream:
        content = stream.read(remaining + 1)
    if len(content) > remaining:
        raise ValueError("The upload exceeds the 64 MiB limit")
    return path.name, content, mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def encode_body(action: ApiAction, body: Any) -> dict[str, Any]:
    """File I/O is isolated by the caller's to_thread; all buffers are bounded."""
    if action.body_encoding == "json":
        return {"json": body}
    if action.body_encoding == "binary":
        return {"content": _file(body, UPLOAD_LIMIT)[1]}
    if not isinstance(body, dict):
        raise ValueError("This API expects named form fields")
    remaining = UPLOAD_LIMIT
    files = []
    fields: dict[str, Any] = {}
    for name, value in body.items():
        if name in action.file_fields:
            for item in value if isinstance(value, list) else [value]:
                if item is None:
                    continue
                upload = _file(item, remaining)
                remaining -= len(upload[1])
                files.append((name, upload))
        elif value is not None:
            fields[name] = (
                json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
            )
    if action.body_encoding == "form":
        return {"data": fields}
    # Force multipart even when this operation has only text form fields.
    files.extend((name, (None, value)) for name, value in fields.items())
    return {"files": files}
