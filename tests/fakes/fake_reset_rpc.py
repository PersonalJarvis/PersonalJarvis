"""Deterministic account/reset app-server; no provider or model traffic."""

from __future__ import annotations

from typing import Any


class FakeResetRpc:
    def __init__(self, payload: dict[str, Any], outcome: Any = "reset") -> None:
        self.payload = payload
        self.outcome = outcome
        self.account = {"type": "chatgpt", "email": "seat@example.test"}
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self.failure: str | None = None
        self.options: tuple[Any, ...] = ()

    def factory(self, *options: Any) -> FakeResetRpc:
        self.options = options
        return self

    async def __aenter__(self) -> FakeResetRpc:
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.closed = True

    async def send(self, payload: dict) -> None:
        self.calls.append((payload["method"], {}))

    async def request(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if method == self.failure:
            raise ConnectionError("private provider details must not escape")
        if method == "account/read":
            return {"account": self.account}
        if method == "account/rateLimits/read":
            return self.payload
        if method == "account/rateLimitResetCredit/consume":
            return {"outcome": self.outcome}
        return {}
