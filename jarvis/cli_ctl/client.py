# jarvis/cli_ctl/client.py
"""Thin httpx wrapper that speaks to a Jarvis server with the control key."""
from __future__ import annotations

from typing import Any

import httpx


class ApiError(Exception):
    """A request failed. `status_code` is None for transport-level failures."""

    def __init__(self, message: str, status_code: int | None = None,
                 payload: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


class JarvisClient:
    def __init__(
        self,
        base_url: str,
        control_key: str | None,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {}
        if control_key:
            headers["Authorization"] = f"Bearer {control_key}"
        self._client = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout,
            transport=transport,
        )
        self.base_url = base_url

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        try:
            resp = self._client.request(
                method.upper(), path, params=params, json=json
            )
        except httpx.TransportError as exc:
            raise ApiError(
                f"Jarvis at {self.base_url} is unreachable: {exc}", None
            ) from exc

        if resp.status_code >= 400:
            detail: Any
            try:
                body = resp.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = resp.text
            raise ApiError(
                f"HTTP {resp.status_code}: {detail}",
                resp.status_code,
                payload=detail,
            )

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JarvisClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
