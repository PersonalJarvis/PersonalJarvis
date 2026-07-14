"""Token-refresh scheduler (Wave 2, #3).

OAuth access tokens expire (30 min HubSpot ... 24 h Linear). The scheduler
refreshes tokens nearing expiry via each plugin's AuthHandler and writes them
back; a revoked grant is retained and flagged so the UI can prompt a reconnect.
"""

from __future__ import annotations

import asyncio
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
    refresh_plugin_token,
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


class _InFlightHandler(_FakeHandler):
    """Pause a provider refresh so a test can mutate the stored grant."""

    def __init__(self, plugin_id: str, new_tokens=None, raise_exc=None) -> None:
        super().__init__(plugin_id, new_tokens=new_tokens, raise_exc=raise_exc)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def refresh(self, current: Tokens) -> Tokens:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self._raise is not None:
            raise self._raise
        return self._new


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
async def test_needs_reauth_token_is_not_retried_every_cycle() -> None:
    store = _store()
    store.save(
        "notion",
        Tokens(
            access="a0",
            refresh="r0",
            needs_reauth=True,
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        ),
    )
    handler = _FakeHandler("notion", new_tokens=Tokens(access="a1", refresh="r1"))

    outcomes = await refresh_due_tokens(["notion"], store, lambda pid: handler)

    assert outcomes == {"notion": SKIPPED}
    kept = store.load("notion")
    assert kept.access == "a0" and kept.needs_reauth is True
    assert handler.calls == 0


@pytest.mark.asyncio
async def test_concurrent_forced_refresh_is_single_flight() -> None:
    store = _store()
    store.save("gmail", Tokens(access="a0", refresh="r0"))
    entered = asyncio.Event()
    release = asyncio.Event()

    class _BlockingHandler(_FakeHandler):
        async def refresh(self, current: Tokens) -> Tokens:
            self.calls += 1
            entered.set()
            await release.wait()
            return Tokens(access="a1", refresh="r1")

    handler = _BlockingHandler("gmail")
    observed = store.load("gmail").access
    first = asyncio.create_task(
        refresh_plugin_token(
            "gmail",
            store,
            lambda _plugin_id: handler,
            force=True,
            observed_access_token=observed,
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        refresh_plugin_token(
            "gmail",
            store,
            lambda _plugin_id: handler,
            force=True,
            observed_access_token=observed,
        )
    )
    release.set()
    attempts = await asyncio.gather(first, second)

    assert handler.calls == 1
    assert {attempt.outcome for attempt in attempts} == {REFRESHED, SKIPPED}
    assert all(attempt.usable for attempt in attempts)
    assert store.load("gmail").access == "a1"


@pytest.mark.asyncio
async def test_successful_refresh_does_not_overwrite_concurrent_reconnect() -> None:
    store = _store()
    store.save("gmail", Tokens(access="old-access", refresh="old-refresh"))
    handler = _InFlightHandler(
        "gmail",
        new_tokens=Tokens(access="stale-refresh-access", refresh="rotated-refresh"),
    )
    task = asyncio.create_task(
        refresh_plugin_token("gmail", store, lambda _plugin_id: handler, force=True)
    )
    await handler.entered.wait()

    new_grant = Tokens(
        access="new-grant-access",
        refresh="new-grant-refresh",
        extra={"grant": "replacement"},
    )
    store.save("gmail", new_grant)
    handler.release.set()
    attempt = await task

    assert attempt.outcome == SKIPPED
    assert attempt.usable is True
    assert attempt.access_changed is True
    assert store.load("gmail") == new_grant


@pytest.mark.asyncio
async def test_successful_refresh_does_not_restore_concurrent_disconnect() -> None:
    store = _store()
    store.save("gmail", Tokens(access="old-access", refresh="old-refresh"))
    handler = _InFlightHandler(
        "gmail",
        new_tokens=Tokens(access="stale-refresh-access", refresh="rotated-refresh"),
    )
    task = asyncio.create_task(
        refresh_plugin_token("gmail", store, lambda _plugin_id: handler, force=True)
    )
    await handler.entered.wait()

    store.delete("gmail")
    handler.release.set()
    attempt = await task

    assert attempt.outcome == SKIPPED
    assert attempt.usable is False
    assert store.load("gmail") is None


@pytest.mark.asyncio
async def test_terminal_refresh_error_does_not_poison_concurrent_reconnect() -> None:
    store = _store()
    store.save("gmail", Tokens(access="old-access", refresh="old-refresh"))
    handler = _InFlightHandler("gmail", raise_exc=RuntimeError("invalid_grant"))
    task = asyncio.create_task(
        refresh_plugin_token("gmail", store, lambda _plugin_id: handler, force=True)
    )
    await handler.entered.wait()

    new_grant = Tokens(access="new-grant-access", refresh="new-grant-refresh")
    store.save("gmail", new_grant)
    handler.release.set()
    attempt = await task

    assert attempt.outcome == SKIPPED
    assert attempt.usable is True
    assert attempt.access_changed is True
    assert store.load("gmail") == new_grant


@pytest.mark.asyncio
async def test_terminal_refresh_error_does_not_restore_concurrent_disconnect() -> None:
    store = _store()
    store.save("gmail", Tokens(access="old-access", refresh="old-refresh"))
    handler = _InFlightHandler("gmail", raise_exc=RuntimeError("invalid_grant"))
    task = asyncio.create_task(
        refresh_plugin_token("gmail", store, lambda _plugin_id: handler, force=True)
    )
    await handler.entered.wait()

    store.delete("gmail")
    handler.release.set()
    attempt = await task

    assert attempt.outcome == SKIPPED
    assert attempt.usable is False
    assert store.load("gmail") is None


@pytest.mark.asyncio
async def test_transient_refresh_error_reuses_concurrent_reconnect() -> None:
    store = _store()
    store.save("gmail", Tokens(access="old-access", refresh="old-refresh"))
    handler = _InFlightHandler("gmail", raise_exc=RuntimeError("HTTP 503"))
    task = asyncio.create_task(
        refresh_plugin_token("gmail", store, lambda _plugin_id: handler, force=True)
    )
    await handler.entered.wait()

    new_grant = Tokens(access="new-grant-access", refresh="new-grant-refresh")
    store.save("gmail", new_grant)
    handler.release.set()
    attempt = await task

    assert attempt.outcome == SKIPPED
    assert attempt.usable is True
    assert attempt.access_changed is True
    assert store.load("gmail") == new_grant


@pytest.mark.asyncio
async def test_transient_refresh_error_observes_concurrent_disconnect() -> None:
    store = _store()
    store.save("gmail", Tokens(access="old-access", refresh="old-refresh"))
    handler = _InFlightHandler("gmail", raise_exc=RuntimeError("HTTP 503"))
    task = asyncio.create_task(
        refresh_plugin_token("gmail", store, lambda _plugin_id: handler, force=True)
    )
    await handler.entered.wait()

    store.delete("gmail")
    handler.release.set()
    attempt = await task

    assert attempt.outcome == SKIPPED
    assert attempt.usable is False
    assert store.load("gmail") is None


@pytest.mark.asyncio
async def test_refresh_preserves_rotating_metadata_and_refresh_token() -> None:
    store = _store()
    store.save(
        "linear",
        Tokens(
            access="a0",
            refresh="r0",
            extra={"client_id": "client-1", "shared": "old"},
        ),
    )
    handler = _FakeHandler(
        "linear",
        new_tokens=Tokens(
            access="a1",
            refresh=None,
            extra={"scope": "read", "shared": "new"},
        ),
    )

    attempt = await refresh_plugin_token("linear", store, lambda _plugin_id: handler, force=True)

    saved = store.load("linear")
    assert attempt.outcome == REFRESHED
    assert saved.refresh == "r0"
    assert saved.extra["client_id"] == "client-1"
    assert saved.extra["scope"] == "read"
    assert saved.extra["shared"] == "new"
    assert "last_refreshed" in saved.extra


@pytest.mark.asyncio
async def test_transient_refresh_failure_keeps_entry() -> None:
    store = _store()
    store.save("gmail", _tokens(60))
    handler = _FakeHandler("gmail", raise_exc=RuntimeError("HTTP 503"))

    outcomes = await refresh_due_tokens(["gmail"], store, lambda pid: handler)

    assert outcomes == {"gmail": FAILED}
    # A transient failure must not delete or poison the stored token.
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


@pytest.mark.asyncio
async def test_scheduler_stop_drains_rotating_refresh_before_return() -> None:
    store = _store()
    store.save("gmail", _tokens(60))
    handler = _InFlightHandler(
        "gmail", new_tokens=Tokens(access="new-access", refresh="new-refresh")
    )
    scheduler = RefreshScheduler(
        plugin_ids_fn=lambda: ["gmail"],
        store=store,
        build_handler=lambda _plugin_id: handler,
        interval_seconds=3600,
        shutdown_drain_timeout_seconds=1.0,
    )
    scheduler.start()
    await handler.entered.wait()

    stopping = asyncio.create_task(scheduler.stop())
    await asyncio.sleep(0)
    assert stopping.done() is False

    handler.release.set()
    await asyncio.wait_for(stopping, timeout=1.0)

    saved = store.load("gmail")
    assert saved.access == "new-access"
    assert saved.refresh == "new-refresh"
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_scheduler_stop_cancels_cycle_only_after_drain_timeout(caplog) -> None:  # noqa: ANN001
    store = _store()
    original = _tokens(60)
    store.save("gmail", original)
    cancelled = asyncio.Event()

    class _CancellationAwareHandler(_InFlightHandler):
        async def refresh(self, current: Tokens) -> Tokens:
            try:
                return await super().refresh(current)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    handler = _CancellationAwareHandler(
        "gmail", new_tokens=Tokens(access="new-access", refresh="new-refresh")
    )
    scheduler = RefreshScheduler(
        plugin_ids_fn=lambda: ["gmail"],
        store=store,
        build_handler=lambda _plugin_id: handler,
        interval_seconds=3600,
        shutdown_drain_timeout_seconds=0.01,
    )
    caplog.set_level(logging.WARNING)
    scheduler.start()
    await handler.entered.wait()

    await asyncio.wait_for(scheduler.stop(), timeout=1.0)

    assert cancelled.is_set()
    assert store.load("gmail") == original
    assert scheduler._task is None
    assert "cancelling as a last resort" in caplog.text


@pytest.mark.asyncio
async def test_scheduler_start_is_idempotent_and_restart_safe() -> None:
    scheduler = RefreshScheduler(
        plugin_ids_fn=list,
        store=_store(),
        build_handler=lambda _plugin_id: None,
        interval_seconds=3600,
    )

    scheduler.start()
    first = scheduler._task
    scheduler.start()
    assert scheduler._task is first
    await scheduler.stop()

    scheduler.start()
    second = scheduler._task
    assert second is not None and second is not first
    await scheduler.stop()


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

    msg = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "gmail" in msg and "linear" in msg


def test_log_cycle_no_warning_when_all_healthy(caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.WARNING)
    RefreshScheduler._log_cycle({"notion": REFRESHED, "slack": SKIPPED})

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_on_refreshed_fires_after_successful_refresh() -> None:
    # The scheduler writes the fresh token to the store, but a live MCP client
    # session (e.g. an open Notion connection) never learns the token rotated
    # unless something tells it to. on_refreshed is that "something" — fired
    # once per plugin id right after its token save succeeds.
    store = _store()
    store.save("notion", _tokens(60))
    handler = _FakeHandler("notion", new_tokens=Tokens(access="a1", refresh="r1"))
    seen: list[str] = []
    sched = RefreshScheduler(
        plugin_ids_fn=lambda: ["notion"],
        store=store,
        build_handler=lambda pid: handler,
        on_refreshed=seen.append,
    )

    outcomes = await sched.run_once()

    assert outcomes == {"notion": REFRESHED}
    assert seen == ["notion"]


@pytest.mark.asyncio
async def test_on_refreshed_exception_does_not_break_the_loop(caplog) -> None:  # noqa: ANN001
    # A UI-refresh hiccup (e.g. the live registry lookup throwing) must never
    # take down the refresh cycle itself — the token is already safely saved.
    store = _store()
    store.save("notion", _tokens(60))
    handler = _FakeHandler("notion", new_tokens=Tokens(access="a1", refresh="r1"))

    def _boom(plugin_id: str) -> None:
        raise RuntimeError("ui refresh failed")

    sched = RefreshScheduler(
        plugin_ids_fn=lambda: ["notion"],
        store=store,
        build_handler=lambda pid: handler,
        on_refreshed=_boom,
    )

    caplog.set_level(logging.WARNING)
    outcomes = await sched.run_once()  # must not raise

    assert outcomes == {"notion": REFRESHED}
    assert store.load("notion").access == "a1"  # token save is unaffected


class _SaveFailsForPlugin:
    """Wraps a real TokenStore; ``save`` raises for one chosen plugin id --
    used to prove a store failure for one plugin stays isolated from the
    rest of the cycle."""

    def __init__(self, inner: TokenStore, fail_for: str) -> None:
        self._inner = inner
        self._fail_for = fail_for

    def load(self, plugin_id: str):
        return self._inner.load(plugin_id)

    def save(self, plugin_id: str, tokens: Tokens) -> None:
        if plugin_id == self._fail_for:
            raise RuntimeError("simulated store.save failure")
        self._inner.save(plugin_id, tokens)


@pytest.mark.asyncio
async def test_one_plugins_save_failure_does_not_block_the_next() -> None:
    inner = _store()
    inner.save("gmail", _tokens(60))
    inner.save("notion", _tokens(60))
    store = _SaveFailsForPlugin(inner, fail_for="gmail")
    handlers = {
        "gmail": _FakeHandler("gmail", new_tokens=Tokens(access="a1", refresh="r1")),
        "notion": _FakeHandler("notion", new_tokens=Tokens(access="a2", refresh="r2")),
    }

    outcomes = await refresh_due_tokens(["gmail", "notion"], store, lambda pid: handlers[pid])

    assert outcomes == {"gmail": FAILED, "notion": REFRESHED}
    assert inner.load("notion").access == "a2"


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
