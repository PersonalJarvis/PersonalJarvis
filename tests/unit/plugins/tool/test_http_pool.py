"""Tests for the shared HTTP keep-alive pool used by the REST router tools.

Regression guard for the plugin-latency fix: the gmail/vercel tools must reuse
ONE warm ``httpx.AsyncClient`` across calls instead of opening a fresh client
(new TLS handshake) per request.
"""

from __future__ import annotations

import httpx
import pytest

from jarvis.plugins.tool._http_pool import HttpClientPool
from jarvis.plugins.tool.gmail_rest import GmailRestTool


@pytest.mark.asyncio
async def test_pool_returns_same_client_within_a_loop() -> None:
    pool = HttpClientPool()
    try:
        assert pool.client() is pool.client()
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_pool_rebinds_after_close() -> None:
    pool = HttpClientPool()
    first = pool.client()
    await pool.aclose()
    second = pool.client()
    try:
        assert first is not second  # closed → a fresh client is created
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_gmail_reuses_one_client_across_calls() -> None:
    """list_messages twice must ride the SAME pooled client — the second call
    reuses the warm connection rather than re-handshaking."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": []})

    tool = GmailRestTool(
        access_token_provider=lambda: "at_1",
        transport=httpx.MockTransport(handler),
    )
    await tool.list_messages(max_results=1)
    first = tool._pool._client
    assert first is not None

    await tool.list_messages(max_results=1)
    assert tool._pool._client is first
