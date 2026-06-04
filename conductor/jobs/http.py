"""HttpHandler — HTTP-Request via httpx, response-body als Run-Output."""
from __future__ import annotations

import time
from typing import Any

from .base import HandlerResult

_BODY_CAP = 64 * 1024


def _match_status(status: int, pattern: str) -> bool:
    """``'2xx'`` / ``'200'`` / ``'3xx'`` / exakter Code."""
    p = pattern.strip().lower()
    if p.endswith("xx") and len(p) == 3 and p[0].isdigit():
        return (status // 100) == int(p[0])
    try:
        return status == int(p)
    except ValueError:
        return False


class HttpHandler:
    async def execute(
        self,
        spec: Any,
        input_data: dict[str, Any],  # noqa: ARG002
    ) -> HandlerResult:
        import httpx

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=spec.timeout_s) as client:
                r = await client.request(
                    method=spec.method,
                    url=spec.url,
                    headers=spec.headers or None,
                    content=spec.body,
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
