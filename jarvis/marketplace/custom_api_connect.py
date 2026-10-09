"""Single-key setup checks. Discovery receives a name, never the supplied key."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from jarvis.core.http_guard import public_only_async
from jarvis.core.http_pool import HttpClientPool
from jarvis.marketplace.custom_api import ApiDefinition
from jarvis.marketplace.custom_api_discovery import ApiDiscoveryError
from jarvis.marketplace.custom_api_transport import QUERY_AUTH_EXTENSION, ApiTransport

log = logging.getLogger(__name__)
_ACCOUNT_READS = (
    "/v1/user",
    "/user",
    "/v1/me",
    "/me",
    "/account",
    "/v1/account",
    "/v1/models",
    "/models",
    "/openai/v1/models",
)


async def verify_api_key(
    definition: ApiDefinition, credential: str, *, transport: Any = None
) -> str:
    """Only documented, parameter-free account reads can verify setup."""
    action = next(
        (
            a
            for path in _ACCOUNT_READS
            for a in definition.actions
            if a.path == path
            and a.method == "GET"
            and not any(p.required or p.location == "path" for p in a.parameters)
        ),
        None,
    )
    if action is None or definition.auth.mode == "none":
        return "configured"
    headers = {"Accept": "application/json"}
    extensions = {}
    if definition.auth.mode == "bearer":
        headers["Authorization"] = f"Bearer {credential}"
    elif definition.auth.mode == "header":
        headers[definition.auth.header_name] = credential
    elif definition.auth.mode == "query":
        extensions[QUERY_AUTH_EXTENSION] = (definition.auth.header_name, credential)
    pool = HttpClientPool(
        timeout_s=8,
        transport=ApiTransport(transport),
        client_kwargs={
            "follow_redirects": False,
            "trust_env": False,
            **public_only_async(),
        },
    )
    try:
        async with (
            asyncio.timeout(10),
            pool.client().stream(
                "GET", definition.base_url + action.path, headers=headers, extensions=extensions
            ) as response,
        ):
            if 200 <= response.status_code < 300:
                return "verified"
            if response.status_code == 403:
                return "limited"
            if response.status_code == 401:
                raw = bytearray()
                async for part in response.aiter_bytes(8192):
                    raw.extend(part)
                    if len(raw) > 32_768:
                        break
                # Some restricted keys cannot read their account but can still
                # perform allowed operations. Only known machine codes qualify.
                if any(
                    code in raw
                    for code in (b'"missing_permissions"', b'"insufficient_permissions"')
                ):
                    return "limited"
                raise ApiDiscoveryError("invalid_key")
            return "configured"
    except (httpx.HTTPError, TimeoutError):
        log.warning("Custom API account check unavailable; key remains unverified")
        return "configured"
    finally:
        await pool.aclose()
