"""Token-refresh scheduler (Wave 2, #3).

OAuth access tokens expire (30 min HubSpot ... 24 h Linear). The scheduler
refreshes tokens nearing expiry via each plugin's AuthHandler and writes them
back; a `revoked` refresh drops the entry so the UI can prompt a reconnect.
"""
from __future__ import annotations

import logging
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
async def test_revoked_refresh_marks_needs_reauth_and_keeps_token() -> None:
    # A revoked refresh must NOT delete the entry — the plugin has to stay
    # visible with a "Reconnect" prompt. The only user-visible delete path is
    # an explicit DELETE. (This replaces the old drop-on-revoke behaviour.)
    store = _store()
    store.save("hubspot", _tokens(60))
    handler = _FakeHandler("hubspot", raise_exc=RuntimeError("revoked"))

    outcomes = await refresh_due_tokens(["hubspot"], store, lambda pid: handler)

    assert outcomes == {"hubspot": REVOKED}
    kept = store.load("hubspot")
    assert kept is not None, "revoked token must NOT be deleted"
    assert kept.needs_reauth is True


@pytest.mark.asyncio
async def test_successful_refresh_clears_stale_needs_reauth() -> None:
    store = _store()
    store.save("notion", Tokens(access="a0", refresh="r0", needs_reauth=True,
                                expires_at=datetime.now(UTC) + timedelta(seconds=60)))
    handler = _FakeHandler("notion", new_tokens=Tokens(access="a1", refresh="r1"))

    outcomes = await refresh_due_tokens(["notion"], store, lambda pid: handler)

    assert outcomes == {"notion": REFRESHED}
    healed = store.load("notion")
    assert healed.access == "a1" and healed.needs_reauth is False


@pytest.mark.asyncio
async def test_transient_refresh_failure_keeps_entry() -> None:
    store = _store()
    store.save("gmail", _tokens(60))
    handler = _FakeHandler("gmail", raise_exc=RuntimeError("HTTP 503"))

    outcomes = await refresh_due_tokens(["gmail"], store, lambda pid: handler)

    assert outcomes == {"gmail": FAILED}
    # Transient failure must NOT delete the token — only `revoked` does.
    assert store.load("gmail") is not None
    # ...and a transient (server-side) error must NOT falsely flag reauth.
    assert store.load("gmail").needs_reauth is False


@pytest.mark.asyncio
async def test_google_invalid_client_marks_needs_reauth() -> None:
    # Google reports OAuth errors via HTTP status + JSON body (not Slack's
    # `ok:false` shape), so the PkceLoopbackHandler raises
    # RuntimeError("refresh HTTP 401: {...invalid_client...}"). The scheduler
    # must classify this as an un-healable auth failure and flag needs_reauth —
    # otherwise the token rots forever as a "transient" FAILED (the live
    # 2026-06-07 Gmail bug: token expired 6 days, needs_reauth stayed False).
    store = _store()
    store.save("gmail", _tokens(60))
    handler = _FakeHandler(
        "gmail",
        raise_exc=RuntimeError(
            'refresh HTTP 401: {"error": "invalid_client", '
            '"error_description": "The OAuth client was not found."}'
        ),
    )

    outcomes = await refresh_due_tokens(["gmail"], store, lambda pid: handler)

    assert outcomes == {"gmail": REVOKED}
    kept = store.load("gmail")
    assert kept is not None, "must not delete — UI needs a Reconnect affordance"
    assert kept.needs_reauth is True


@pytest.mark.asyncio
async def test_google_invalid_grant_marks_needs_reauth() -> None:
    # Google returns invalid_grant as HTTP 400 + JSON body when the refresh
    # token is revoked/expired (e.g. the 7-day Testing-mode window). Must also
    # flag needs_reauth, not FAILED.
    store = _store()
    store.save("gmail", _tokens(60))
    handler = _FakeHandler(
        "gmail",
        raise_exc=RuntimeError('refresh HTTP 400: {"error": "invalid_grant"}'),
    )

    outcomes = await refresh_due_tokens(["gmail"], store, lambda pid: handler)

    assert outcomes == {"gmail": REVOKED}
    assert store.load("gmail").needs_reauth is True


@pytest.mark.asyncio
async def test_legacy_no_client_id_marks_needs_reauth() -> None:
    # A DCR token minted before client_id was persisted cannot be refreshed; the
    # handler fails soft with "reconnect required". The scheduler must flag
    # needs_reauth (not silent FAILED) so the UI offers Reconnect (live
    # 2026-06-08 audit: linear sat EXPIRED -151h with needs_reauth=False).
    store = _store()
    store.save("linear", _tokens(60))
    handler = _FakeHandler(
        "linear",
        raise_exc=RuntimeError(
            "refresh: no stored client_id — reconnect required to heal this connection"
        ),
    )

    outcomes = await refresh_due_tokens(["linear"], store, lambda pid: handler)

    assert outcomes == {"linear": REVOKED}
    assert store.load("linear").needs_reauth is True


@pytest.mark.asyncio
async def test_keep_alive_refreshes_no_expiry_token() -> None:
    # A token with a refresh token but no recorded expiry is never "near expiry",
    # so the default path skips it forever — its refresh token can then rot from
    # provider-side inactivity. keep_alive refreshes it proactively to keep the
    # connection alive forever (the core "log in once, stay forever" guarantee).
    store = _store()
    store.save("slack", Tokens(access="a0", refresh="r0", expires_at=None))
    handler = _FakeHandler("slack", new_tokens=Tokens(access="a1", refresh="r1"))

    outcomes = await refresh_due_tokens(
        ["slack"], store, lambda pid: handler, keep_alive_seconds=3600
    )

    assert outcomes == {"slack": REFRESHED}
    assert handler.calls == 1
    healed = store.load("slack")
    assert healed.access == "a1"
    assert "last_refreshed" in healed.extra  # stamped so the next cycle can skip


@pytest.mark.asyncio
async def test_keep_alive_skips_recently_refreshed() -> None:
    store = _store()
    store.save(
        "notion",
        Tokens(
            access="a0",
            refresh="r0",
            expires_at=datetime.now(UTC) + timedelta(hours=10),
            extra={"last_refreshed": datetime.now(UTC).isoformat()},
        ),
    )
    handler = _FakeHandler("notion", new_tokens=Tokens(access="nope"))

    outcomes = await refresh_due_tokens(
        ["notion"], store, lambda pid: handler, keep_alive_seconds=3600
    )

    assert outcomes == {"notion": SKIPPED}
    assert handler.calls == 0


@pytest.mark.asyncio
async def test_keep_alive_disabled_by_default_keeps_skip() -> None:
    # Backwards-compat: without keep_alive_seconds a not-near-expiry token is
    # still SKIPPED (the existing refresh_due_tokens contract is unchanged).
    store = _store()
    store.save("linear", Tokens(access="a0", refresh="r0", expires_at=None))
    handler = _FakeHandler("linear", new_tokens=Tokens(access="x"))

    outcomes = await refresh_due_tokens(["linear"], store, lambda pid: handler)

    assert outcomes == {"linear": SKIPPED}
    assert handler.calls == 0


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


def test_log_cycle_names_dead_plugins_at_warning(caplog) -> None:  # noqa: ANN001
    # A revoked connection must be named at WARNING so it doesn't rot unseen
    # behind an anonymous "revoked=1" count (live: linear sat dead 22 days
    # unnoticed). A healthy refresh must not appear in the warning.
    caplog.set_level(logging.WARNING)
    RefreshScheduler._log_cycle({"gmail": REVOKED, "notion": REFRESHED})

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("gmail" in r.getMessage() for r in warnings)
    assert not any("notion" in r.getMessage() for r in warnings)


def test_log_cycle_lists_every_dead_plugin(caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.WARNING)
    RefreshScheduler._log_cycle({"gmail": REVOKED, "linear": REVOKED, "slack": SKIPPED})

    msg = " ".join(
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    )
    assert "gmail" in msg and "linear" in msg


def test_log_cycle_no_warning_when_all_healthy(caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.WARNING)
    RefreshScheduler._log_cycle({"notion": REFRESHED, "slack": SKIPPED})

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_scheduler_keep_alive_refreshes_long_lived_token() -> None:
    # The scheduler must apply keep-alive so a long-lived / no-expiry token still
    # gets its refresh token exercised — the durable "stay connected forever" path.
    store = _store()
    store.save("slack", Tokens(access="a0", refresh="r0", expires_at=None))
    handler = _FakeHandler("slack", new_tokens=Tokens(access="a1", refresh="r1"))
    sched = RefreshScheduler(
        plugin_ids_fn=lambda: ["slack"],
        store=store,
        build_handler=lambda pid: handler,
        keep_alive_seconds=3600,
    )
    outcomes = await sched.run_once()
    assert outcomes == {"slack": REFRESHED}
    assert store.load("slack").access == "a1"
