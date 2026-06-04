"""SymconActor — IP-Symcon JSON-RPC actor (ADR-0005, amended by ADR-0010).

The previous in-tree deterministic stand-in was moved to the tests/fakes
package per ADR-0010 — production src/ must not host test doubles. The
real, RPC-backed actor stays here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .jsonrpc import JsonRpcClient


@dataclass(slots=True)
class SymconActor:
    """Production-bound actor: invokes one IP-Symcon JSON-RPC method.

    The bridge passes ``params`` straight through as the JSON-RPC ``params``
    argument; callers shape the dict to whatever the IP-Symcon method expects.
    A hard wall-clock timeout converts a slow RPC into ``TimeoutError`` so the
    LATSEngine can produce a clean Diagnostic.
    """

    name: str
    method: str
    client: JsonRpcClient
    timeout_s: float = 5.0

    async def call(self, params: Any) -> dict:
        try:
            result = await asyncio.wait_for(
                self.client.call(self.method, params),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"SymconActor {self.name!r} exceeded {self.timeout_s:.1f}s"
            ) from exc
        if isinstance(result, dict):
            return result
        return {"value": result}
