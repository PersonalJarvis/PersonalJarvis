"""HTTP jobs with a reusable connection pool and off-loop TLS setup."""
from __future__ import annotations

import asyncio
import time
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

from .base import HandlerResult

_BODY_CAP = 64 * 1024


def _match_status(status: int, pattern: str) -> bool:
    """Match a status family such as ``2xx`` or an exact status code."""
    p = pattern.strip().lower()
    if p.endswith("xx") and len(p) == 3 and p[0].isdigit():
        return (status // 100) == int(p[0])
    try:
        return status == int(p)
    except ValueError:
        return False


class _NoStoredCookies(DefaultCookiePolicy):
    def set_ok(self, cookie: Any, request: Any) -> bool:
        # Separate jobs may use different credentials against the same host.
        # Pool sockets, but never carry response cookies into another job.
        return False


class HttpHandler:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _build_client() -> Any:
        import httpx

        return httpx.AsyncClient(
            cookies=CookieJar(policy=_NoStoredCookies()),
            limits=httpx.Limits(
                max_connections=20, max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )

    async def _get_client(self) -> Any:
        async with self._lock:
            if self._client is None:
                # Loading trust roots can stall for seconds under memory
                # pressure. Even the first job must leave the UI loop free.
                build = asyncio.create_task(asyncio.to_thread(self._build_client))
                try:
                    self._client = await asyncio.shield(build)
                except asyncio.CancelledError:
                    # A thread cannot be cancelled. Close what it built before
                    # propagating cancellation, rather than leaking its pool.
                    client = await build
                    await client.aclose()
                    raise
            return self._client

    async def aclose(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
            if client is not None:
                await client.aclose()

    async def execute(
        self,
        spec: Any,
        input_data: dict[str, Any],  # noqa: ARG002
    ) -> HandlerResult:
        import httpx

        start = time.perf_counter()
        try:
            client = await self._get_client()
            r = await client.request(
                method=spec.method,
                url=spec.url,
                headers=spec.headers or None,
                content=spec.body,
                timeout=spec.timeout_s,
            )
        except httpx.TimeoutException:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"timeout after {spec.timeout_s}s",
                metrics={"duration_ms": duration_ms},
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - start) * 1000)
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"{type(exc).__name__}: {exc}",
                metrics={"duration_ms": duration_ms},
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        body = r.text
        if len(body) > _BODY_CAP:
            body = body[:_BODY_CAP] + "\n…(truncated)"

        matches = _match_status(r.status_code, spec.expect_status)
        metrics = {
            "duration_ms": duration_ms,
            "status_code": r.status_code,
            "response_bytes": len(r.content),
            "expect_status": spec.expect_status,
        }
        if matches:
            return HandlerResult(
                success=True, output=body, exit_code=0, metrics=metrics,
            )
        return HandlerResult(
            success=False,
            output=body,
            exit_code=r.status_code,
            error=f"status {r.status_code} does not match '{spec.expect_status}'",
            metrics=metrics,
        )
