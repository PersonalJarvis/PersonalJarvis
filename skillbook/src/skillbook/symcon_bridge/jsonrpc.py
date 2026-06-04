"""JSON-RPC 2.0 client for IP-Symcon (ADR-0005).

The HTTP layer is injectable via ``http_post`` so tests stay offline and the
production path uses stdlib ``urllib.request`` wrapped in ``asyncio.to_thread``
— no extra HTTP dependency.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class JsonRpcError(RuntimeError):
    """Raised on JSON-RPC ``error`` envelope or malformed response."""


HttpPostFn = Callable[[str, bytes, float], Awaitable[bytes]]


async def _default_http_post(url: str, body: bytes, timeout_s: float) -> bytes:
    def _post() -> bytes:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.read()

    return await asyncio.to_thread(_post)


@dataclass(slots=True)
class JsonRpcClient:
    url: str
    timeout_s: float = 5.0
    http_post: HttpPostFn = _default_http_post
    _id_counter: itertools.count = field(
        default_factory=lambda: itertools.count(1), init=False, repr=False
    )

    async def call(self, method: str, params: Any) -> Any:
        envelope = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": next(self._id_counter),
        }
        body = json.dumps(envelope).encode("utf-8")
        try:
            raw = await self.http_post(self.url, body, self.timeout_s)
        except urllib.error.URLError as exc:
            raise JsonRpcError(f"transport failure: {exc}") from exc

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JsonRpcError(f"malformed response: {exc}") from exc

        if not isinstance(decoded, dict):
            raise JsonRpcError("response not a JSON object")
        if "error" in decoded:
            err = decoded["error"]
            msg = err.get("message", "unknown") if isinstance(err, dict) else str(err)
            raise JsonRpcError(f"server error: {msg}")
        if "result" not in decoded:
            raise JsonRpcError("response missing 'result' field")
        return decoded["result"]
