"""The generic streaming reverse-proxy core.

One handler serves ``/p/{provider_id}/{path:path}`` for every HTTP method:

1. Extract the inbound per-user token per the vendor's credential rule.
2. ``tokens.verify(token)`` — fail-closed 401 if missing / unknown / revoked.
3. ``config.lookup(provider_id)`` -> (vendor, real_base, real_key); 404 if
   unknown.
4. Build the upstream request: target ``real_base.rstrip('/') + '/' + path``,
   copy method / query (minus the gemini ``key``) / body, forward only an
   allowlist of safe headers (hop-by-hop + inbound auth stripped), then set the
   real credential per the vendor rule.
5. Stream the upstream response back unchanged (status, headers, body).
6. Best-effort ``usage.record(...)`` — a parse miss records null counts and
   NEVER fails the response; metering does not block or alter the response.

The handler is wired into the app by :func:`keyproxy.app.create_app`, which
injects the outbound ``httpx.AsyncClient`` (so tests can supply a fake upstream)
plus the token / usage / config dependencies.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from . import vendors
from .config import ProxyConfig
from .tokens import TokenStore
from .usage import UsageStore

_log = logging.getLogger("keyproxy.passthrough")

# Hop-by-hop headers (RFC 7230 §6.1) must not be forwarded by a proxy. Plus any
# inbound auth header — the proxy sets the real credential itself.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_INBOUND_AUTH = frozenset({"authorization", "x-api-key", "x-goog-api-key"})
# Never copy the inbound Host (it is the proxy host, not the vendor's) or the
# request's content-length (httpx recomputes it for the new body).
_DROP_REQUEST_HEADERS = _HOP_BY_HOP | _INBOUND_AUTH | {"host", "content-length"}
# Hop-by-hop response headers + ones httpx/Starlette will set themselves.
_DROP_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length", "content-encoding"}

# Cap on the buffered body we keep for usage parsing; the streamed response to
# the client is never capped. A response larger than this still streams fully;
# we just stop accumulating the metering buffer.
_USAGE_BUFFER_CAP = 1_000_000  # 1 MB


def _safe_request_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    items = headers.items() if hasattr(headers, "items") else headers.items()
    for key, value in items:
        if key.lower() in _DROP_REQUEST_HEADERS:
            continue
        out[key] = value
    return out


def _safe_response_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _DROP_RESPONSE_HEADERS:
            continue
        out[key] = value
    return out


async def handle_passthrough(
    request: Request,
    provider_id: str,
    path: str,
) -> Response:
    config: ProxyConfig = request.app.state.config
    tokens: TokenStore = request.app.state.tokens
    usage: UsageStore = request.app.state.usage
    upstream: httpx.AsyncClient = request.app.state.upstream

    # We need the vendor before token extraction (extraction is vendor-shaped),
    # but we must not reveal whether a provider exists before auth. Resolve the
    # vendor from the wire contract first (this is public, non-secret info).
    contract = vendors.resolve_provider(provider_id)

    # Lower-case the inbound headers once for vendor extraction.
    lower_headers = {k.lower(): v for k, v in request.headers.items()}
    query_params = dict(request.query_params)

    # If the provider is not in the wire contract at all, we cannot know which
    # credential slot to read. Try every inbound slot so a valid token still
    # authenticates (then we 404 the unknown provider).
    if contract is not None:
        vendor = contract[0]
        token = vendors.extract_inbound_token(
            vendor, lower_headers, query=query_params
        )
    else:
        vendor = None
        token = (
            vendors.extract_inbound_token(
                "openai_compatible", lower_headers, query=query_params
            )
            or vendors.extract_inbound_token(
                "anthropic", lower_headers, query=query_params
            )
            or vendors.extract_inbound_token(
                "gemini", lower_headers, query=query_params
            )
        )

    # 2. Auth — fail closed.
    token_id = tokens.verify(token)
    if token_id is None:
        return JSONResponse(
            {"error": "invalid_token", "detail": "missing, unknown, or revoked token"},
            status_code=401,
        )

    # 3. Provider lookup — 404 with ONE generic message whether the provider is
    # unknown OR known-but-unkeyed, so an authenticated client cannot enumerate
    # which known providers have a real key loaded.
    looked = config.lookup(provider_id)
    if looked is None:
        return JSONResponse(
            {"error": "provider_not_available", "detail": "provider not available"},
            status_code=404,
        )
    vendor, real_base, real_key = looked

    # 4. Build the upstream request.
    body = await request.body()
    out_headers = _safe_request_headers(request.headers)
    out_query = dict(query_params)
    # Strip the inbound gemini ?key= before placement (defence in depth — the
    # placement function also pops it).
    out_headers, out_query = vendors.place_outbound_credential(
        vendor, headers=out_headers, query=out_query, real_key=real_key
    )

    target_url = real_base.rstrip("/") + "/" + path.lstrip("/")

    upstream_request = upstream.build_request(
        request.method,
        target_url,
        params=out_query or None,
        headers=out_headers,
        content=body if body else None,
    )

    # 5. Stream the upstream response back.
    try:
        upstream_response = await upstream.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        # The upstream/proxy could not be reached — honest 502 (distinct from a
        # 401 bad-key or a 429 rate-limit, which pass through with their status).
        # The exception string can carry the real vendor base URL, so it is
        # logged server-side ONLY; the client gets a static message.
        _log.warning(
            "upstream unreachable for provider %s: %s",
            provider_id,
            exc.__class__.__name__,
        )
        return JSONResponse(
            {
                "error": "upstream_unreachable",
                "detail": "upstream vendor could not be reached",
            },
            status_code=502,
        )

    usage_buffer = bytearray()

    async def body_iterator():
        try:
            async for chunk in upstream_response.aiter_raw():
                if len(usage_buffer) < _USAGE_BUFFER_CAP:
                    usage_buffer.extend(chunk[: _USAGE_BUFFER_CAP - len(usage_buffer)])
                yield chunk
        finally:
            # Both the close and the metering are best-effort cleanup that runs
            # after the body has streamed; neither may surface as a stream error.
            try:
                await upstream_response.aclose()
            except Exception:  # noqa: BLE001 — close failure must not break the stream
                _log.debug("upstream response close failed", exc_info=True)
            # 6. Best-effort metering — never fails the response (we are past
            # the point where the body is already streamed).
            try:
                _record_usage(
                    usage,
                    token_id=token_id,
                    provider_id=provider_id,
                    vendor=vendor,
                    body=bytes(usage_buffer),
                )
            except Exception:  # noqa: BLE001 — metering must never break the stream
                _log.debug("usage recording failed", exc_info=True)

    response_headers = _safe_response_headers(upstream_response.headers)
    return StreamingResponse(
        body_iterator(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


def _record_usage(
    usage: UsageStore,
    *,
    token_id: str,
    provider_id: str,
    vendor: str,
    body: bytes,
) -> None:
    parsed: Any = None
    try:
        parsed = vendors.parse_usage(vendor, body)
    except Exception:  # noqa: BLE001 — metering must never raise
        parsed = None
    usage.record(token_id=token_id, provider_id=provider_id, parsed=parsed)
