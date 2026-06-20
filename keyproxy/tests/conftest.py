"""Make the repo root importable so ``import keyproxy`` works without install.

keyproxy is a standalone package run directly from the source tree (and from
its own Dockerfile via the working directory). When the tests are invoked as
``py -3.11 -m pytest keyproxy/`` from the repo root, the root is already on
``sys.path``; this conftest makes the suite robust to being run from anywhere.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def anyio_backend() -> str:
    """Pin the ``@pytest.mark.anyio`` tests to asyncio (no trio dependency)."""
    return "asyncio"


class _AsyncByteStream(httpx.AsyncByteStream):
    """A minimal real async stream so MockTransport responses are streamable.

    ``httpx.Response(content=...)`` produces an already-consumed buffered stream,
    which raises ``StreamConsumed`` under ``aiter_raw()``. A real vendor returns
    a streaming body; this mirrors that so the proxy's streaming path is
    exercised exactly as in production.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:  # pragma: no cover - trivial
        return None


def stream_response(
    status: int,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
    chunk_size: int = 0,
) -> httpx.Response:
    """Build a streamable ``httpx.Response`` for a MockTransport upstream."""
    if chunk_size and chunk_size > 0:
        chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)]
    else:
        chunks = [body] if body else []
    return httpx.Response(
        status, stream=_AsyncByteStream(chunks), headers=headers or {}
    )
