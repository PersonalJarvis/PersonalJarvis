"""Token-refresh scheduler (Wave 2, #3).

OAuth access tokens expire (30 min HubSpot ... 24 h Linear). The scheduler
refreshes tokens nearing expiry via each plugin's AuthHandler and writes them
back; a `revoked` refresh drops the entry so the UI can prompt a reconnect.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jarvis.marketplace.refresh_scheduler import (
    FAILED,
    REFRESHED,
    REVOKED,
    SKIPPED,
    RefreshScheduler,
    refresh_due_tokens,
)
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


def _store() -> TokenStore:
    return TokenStore(InMemoryBackend())


def _tokens(expires_in_seconds: int | None, refresh: str | None = "r0") -> Tokens:
    exp = (
        datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
        if expires_in_seconds is not None
        else None
    )
    return Tokens(access="a0", refresh=refresh, expires_at=exp)


class _FakeHandler:
    def __init__(self, plugin_id: str, new_tokens=None, raise_exc=None) -> None:
        self.plugin_id = plugin_id
        self._new = new_tokens
        self._raise = raise_exc
        self.calls = 0

    async def refresh(self, current: Tokens) -> Tokens:
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        return self._new

    async def start(self, plugin_spec):  # noqa: ANN001, ANN201 (protocol stub)
        raise NotImplementedError

    async def await_completion(self, session):  # noqa: ANN001, ANN201
        raise NotImplementedError

    def auth_header(self, tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access}"}


@pytest.mark.asyncio
async def test_refreshes_token_near_expiry() -> None:
    store = _store()
    store.save("notion", _tokens(60))  # 60s left < 600s threshold
    new = Tokens(access="a1", refresh="r1")
    handler = _FakeHandler("notion", new_tokens=new)

    outcomes = await refresh_due_tokens(["notion"], store, lambda pid: handler)

    assert outcomes == {"notion": REFRESHED}
    assert handler.calls == 1
    assert store.load("notion").access == "a1"


@pytest.mark.asyncio
async def test_skips_token_not_near_expiry() -> None:
    store = _store()
    store.save("linear", _tokens(3600))  # 1h left > 600s threshold
    handler = _FakeHandler("linear", new_tokens=Tokens(access="nope"))

    outcomes = await refresh_due_tokens(["linear"], store, lambda pid: handler)

    assert outcomes == {"linear": SKIPPED}
    assert handler.calls == 0
    assert store.load("linear").access == "a0"


@pytest.mark.asyncio
async def test_skips_plugin_without_tokens() -> None:
    store = _store()
    outcomes = await refresh_due_tokens(["ghost"], store, lambda pid: None)
    assert outcomes == {"ghost": SKIPPED}


@pytest.mark.asyncio
async def test_skips_token_without_refresh() -> None:
    store = _store()
    store.save("pat-only", _tokens(60, refresh=None))
    handler = _FakeHandler("pat-only")
    outcomes = await refresh_due_tokens(["pat-only"], store, lambda pid: handler)
    assert outcomes == {"pat-only": SKIPPED}
    assert handler.calls == 0


@pytest.mark.asyncio
async def test_revoked_refresh_drops_entry() -> None:
    store = _store()
    store.save("hubspot", _tokens(60))
    handler = _FakeHandler("hubspot", raise_exc=RuntimeError("revoked"))

    outcomes = await refresh_due_tokens(["hubspot"], store, lambda pid: handler)

    assert outcomes == {"hubspot": REVOKED}
    assert store.load("hubspot") is None


@pytest.mark.asyncio
async def test_transient_refresh_failure_keeps_entry() -> None:
    store = _store()
    store.save("gmail", _tokens(60))
    handler = _FakeHandler("gmail", raise_exc=RuntimeError("HTTP 503"))

    outcomes = await refresh_due_tokens(["gmail"], store, lambda pid: handler)

    assert outcomes == {"gmail": FAILED}
    # Transient failure must NOT delete the token — only `revoked` does.
    assert store.load("gmail") is not None


@pytest.mark.asyncio
async def test_scheduler_run_once_delegates() -> None:
    store = _store()
    store.save("notion", _tokens(60))
    handler = _FakeHandler("notion", new_tokens=Tokens(access="a1", refresh="r1"))
    sched = RefreshScheduler(
        plugin_ids_fn=lambda: ["notion"],
        store=store,
        build_handler=lambda pid: handler,
    )
    outcomes = await sched.run_once()
    assert outcomes == {"notion": REFRESHED}
