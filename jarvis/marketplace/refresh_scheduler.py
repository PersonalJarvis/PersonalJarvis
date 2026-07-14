"""Coordinated refresh lifecycle for connected Marketplace plugins.

OAuth access tokens are short-lived, while refresh tokens are longer-lived but
can still expire or be revoked. This module keeps access tokens fresh and
serializes all refresh paths for a plugin so scheduler, REST-tool, and registry
retries cannot rotate the same refresh token concurrently.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
import weakref
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

from jarvis.marketplace.auth.base import AuthHandler
from jarvis.marketplace.token_store import Tokens, TokenStore

log = logging.getLogger(__name__)

HandlerBuilder = Callable[[str], AuthHandler | None]
PluginIdsFn = Callable[[], list[str]]

REFRESHED = "refreshed"
SKIPPED = "skipped"
REVOKED = "revoked"
FAILED = "failed"


@dataclasses.dataclass(frozen=True, slots=True)
class RefreshAttempt:
    """Secret-free result from one coordinated refresh attempt."""

    outcome: str
    usable: bool = False
    access_changed: bool = False


_REFRESH_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)
_REFRESH_LOCKS_GUARD = threading.Lock()


def _refresh_lock(plugin_id: str) -> asyncio.Lock:
    """Return the per-plugin lock bound to the current event loop."""
    loop = asyncio.get_running_loop()
    with _REFRESH_LOCKS_GUARD:
        loop_locks = _REFRESH_LOCKS.setdefault(loop, {})
        return loop_locks.setdefault(plugin_id, asyncio.Lock())


_REAUTH_ERROR_MARKERS: tuple[str, ...] = (
    "revoked",
    "invalid_grant",
    "invalid_client",
    "unauthorized_client",
    "client was not found",
    "token has been expired",
    "reconnect required",
    "no stored client_id",
)


def _refresh_needs_reauth(message: str) -> bool:
    """Return whether a refresh failure requires user authentication."""
    lowered = message.lower()
    return any(marker in lowered for marker in _REAUTH_ERROR_MARKERS)


def _keep_alive_due(tokens: object, keep_alive_seconds: int | None) -> bool:
    """Return whether a refresh token should be exercised to stay warm."""
    if keep_alive_seconds is None or not getattr(tokens, "refresh", None):
        return False
    last_refreshed = tokens.extra.get("last_refreshed")  # type: ignore[attr-defined]
    if not last_refreshed:
        return True
    try:
        last = datetime.fromisoformat(last_refreshed)
    except (TypeError, ValueError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (datetime.now(UTC) - last).total_seconds() >= keep_alive_seconds


def _reload_before_refresh_commit(
    plugin_id: str,
    store: TokenStore,
    original_state: str,
) -> tuple[Tokens | None, RefreshAttempt | None]:
    """Reload the token and reject a stale provider result.

    Disconnect and reconnect do not take the refresh single-flight lock. They
    may therefore replace or delete the stored grant while the provider call
    is awaiting I/O. A refresh result may only be committed when the complete
    stored token state is still the one used to start that provider call.
    """
    try:
        current = store.load(plugin_id)
    except Exception as exc:  # noqa: BLE001 - isolate one plugin's storage
        log.warning("plugin %s token reload failed after refresh: %s", plugin_id, exc)
        return None, RefreshAttempt(FAILED)

    if current is not None and current.to_json() == original_state:
        return current, None

    log.info(
        "plugin %s token changed while refresh was in flight; discarded stale result",
        plugin_id,
    )
    if current is None or current.needs_reauth:
        return current, RefreshAttempt(SKIPPED)
    return current, RefreshAttempt(
        SKIPPED,
        usable=True,
        access_changed=current.access != Tokens.from_json(original_state).access,
    )


async def refresh_plugin_token(
    plugin_id: str,
    store: TokenStore,
    build_handler: HandlerBuilder,
    *,
    force: bool = False,
    observed_access_token: str | None = None,
    threshold_seconds: int = 600,
    keep_alive_seconds: int | None = None,
) -> RefreshAttempt:
    """Refresh one plugin under a process-local single-flight lock.

    The token is reloaded after acquiring the lock. If another caller already
    replaced the access token that triggered a 401, the waiting caller reuses
    that token instead of rotating the refresh token a second time.
    """
    async with _refresh_lock(plugin_id):
        try:
            tokens = store.load(plugin_id)
        except Exception as exc:  # noqa: BLE001 - isolate one plugin's storage
            log.warning("plugin %s token load failed: %s", plugin_id, exc)
            return RefreshAttempt(FAILED)

        if tokens is None or tokens.needs_reauth:
            return RefreshAttempt(SKIPPED)
        if observed_access_token is not None and tokens.access != observed_access_token:
            return RefreshAttempt(SKIPPED, usable=True, access_changed=True)
        if not tokens.refresh:
            return RefreshAttempt(SKIPPED, usable=not force)

        if not force:
            due = tokens.is_near_expiry(threshold_seconds) or _keep_alive_due(
                tokens, keep_alive_seconds
            )
            if not due:
                return RefreshAttempt(SKIPPED, usable=True)

        original_state = tokens.to_json()

        try:
            handler = build_handler(plugin_id)
        except Exception as exc:  # noqa: BLE001 - configuration must not break the loop
            log.warning("plugin %s refresh handler failed to build: %s", plugin_id, exc)
            return RefreshAttempt(FAILED)
        if handler is None:
            return RefreshAttempt(SKIPPED)

        try:
            refreshed = await handler.refresh(tokens)
            if not refreshed.access:
                raise RuntimeError("refresh returned an empty access token")
        except Exception as exc:  # noqa: BLE001 - provider failures are isolated
            if not _refresh_needs_reauth(str(exc)):
                _current, superseded = _reload_before_refresh_commit(
                    plugin_id, store, original_state
                )
                if superseded is not None:
                    return superseded
                log.warning(
                    "plugin %s refresh failed (transient, will retry): %s",
                    plugin_id,
                    exc,
                )
                return RefreshAttempt(FAILED)

            current, superseded = _reload_before_refresh_commit(plugin_id, store, original_state)
            if superseded is not None:
                return superseded
            assert current is not None

            try:
                store.save(plugin_id, dataclasses.replace(current, needs_reauth=True))
            except Exception as save_exc:  # noqa: BLE001 - isolate storage failure
                log.warning(
                    "plugin %s needs_reauth save failed, will retry: %s",
                    plugin_id,
                    save_exc,
                )
                return RefreshAttempt(FAILED)
            log.info(
                "plugin %s refresh needs reauth; marked needs_reauth: %s",
                plugin_id,
                exc,
            )
            return RefreshAttempt(REVOKED)

        current, superseded = _reload_before_refresh_commit(plugin_id, store, original_state)
        if superseded is not None:
            return superseded
        assert current is not None

        merged_extra = {
            **current.extra,
            **refreshed.extra,
            "last_refreshed": datetime.now(UTC).isoformat(),
        }
        saved = dataclasses.replace(
            refreshed,
            refresh=refreshed.refresh or current.refresh,
            extra=merged_extra,
            needs_reauth=False,
        )
        try:
            store.save(plugin_id, saved)
        except Exception as exc:  # noqa: BLE001 - isolate storage failure
            log.warning(
                "plugin %s refreshed token save failed, will retry: %s",
                plugin_id,
                exc,
            )
            return RefreshAttempt(FAILED)
        return RefreshAttempt(
            REFRESHED,
            usable=True,
            access_changed=saved.access != current.access,
        )


async def refresh_due_tokens(
    plugin_ids: list[str],
    store: TokenStore,
    build_handler: HandlerBuilder,
    *,
    threshold_seconds: int = 600,
    keep_alive_seconds: int | None = None,
    on_refreshed: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Refresh every due plugin without allowing one failure to stop the cycle."""
    outcomes: dict[str, str] = {}
    for plugin_id in plugin_ids:
        attempt = await refresh_plugin_token(
            plugin_id,
            store,
            build_handler,
            threshold_seconds=threshold_seconds,
            keep_alive_seconds=keep_alive_seconds,
        )
        outcomes[plugin_id] = attempt.outcome
        if attempt.outcome == REFRESHED and on_refreshed is not None:
            try:
                on_refreshed(plugin_id)
            except Exception as exc:  # noqa: BLE001 - callback is best-effort
                log.warning(
                    "refresh: on_refreshed callback failed for %s: %s",
                    plugin_id,
                    exc,
                )
    return outcomes


class RefreshScheduler:
    """Periodic background task wrapping :func:`refresh_due_tokens`."""

    def __init__(
        self,
        plugin_ids_fn: PluginIdsFn,
        store: TokenStore,
        build_handler: HandlerBuilder,
        *,
        interval_seconds: float = 300.0,
        threshold_seconds: int = 600,
        keep_alive_seconds: int | None = 43_200,
        on_refreshed: Callable[[str], None] | None = None,
        shutdown_drain_timeout_seconds: float = 30.0,
    ) -> None:
        self._plugin_ids_fn = plugin_ids_fn
        self._store = store
        self._build_handler = build_handler
        self._interval = interval_seconds
        self._threshold = threshold_seconds
        self._keep_alive_seconds = keep_alive_seconds
        self._on_refreshed = on_refreshed
        self._shutdown_drain_timeout = shutdown_drain_timeout_seconds
        self._task: asyncio.Task[None] | None = None
        self._cycle_task: asyncio.Task[dict[str, str]] | None = None
        self._stopping = False

    async def run_once(self) -> dict[str, str]:
        return await refresh_due_tokens(
            self._plugin_ids_fn(),
            self._store,
            self._build_handler,
            threshold_seconds=self._threshold,
            keep_alive_seconds=self._keep_alive_seconds,
            on_refreshed=self._on_refreshed,
        )

    async def _loop(self) -> None:
        while not self._stopping:
            cycle = asyncio.create_task(self.run_once(), name="marketplace-refresh-cycle")
            self._cycle_task = cycle
            try:
                # A loop cancellation must not propagate into a provider call:
                # rotating providers may already have consumed the old refresh
                # token, and cancelling before the save would lose the new one.
                outcomes = await asyncio.shield(cycle)
                self._log_cycle(outcomes)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the background loop alive
                log.warning("refresh cycle failed: %s", exc)
            finally:
                if cycle.done() and self._cycle_task is cycle:
                    self._cycle_task = None

            if self._stopping:
                break
            await asyncio.sleep(self._interval)

    @staticmethod
    def _log_cycle(outcomes: dict[str, str]) -> None:
        counts: dict[str, int] = {}
        for outcome in outcomes.values():
            counts[outcome] = counts.get(outcome, 0) + 1
        summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        summary = summary or "no plugins"
        changed = any(counts.get(key) for key in (REFRESHED, REVOKED, FAILED))
        (log.info if changed else log.debug)("token refresh cycle: %s", summary)

        revoked = sorted(plugin_id for plugin_id, outcome in outcomes.items() if outcome == REVOKED)
        if revoked:
            log.warning(
                "marketplace plugin(s) need reconnect; refresh token revoked: %s",
                ", ".join(revoked),
            )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._cycle_task is not None and not self._cycle_task.done():
            log.warning("marketplace refresh cannot restart while shutdown is draining")
            return
        self._cycle_task = None
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="marketplace-refresh")

    async def stop(self) -> None:
        self._stopping = True
        cycle = self._cycle_task
        if cycle is not None and not cycle.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(cycle),
                    timeout=self._shutdown_drain_timeout,
                )
            except TimeoutError:
                log.warning(
                    "marketplace refresh did not drain within %.1fs; cancelling as a last resort",
                    self._shutdown_drain_timeout,
                )
                cycle.cancel()
                # Give a cooperative provider one event-loop turn to observe
                # cancellation without making shutdown unbounded again.
                await asyncio.sleep(0)
                if cycle.done():
                    with suppress(asyncio.CancelledError, Exception):
                        cycle.result()
            except asyncio.CancelledError:
                if not cycle.cancelled():
                    raise
            except Exception as exc:  # noqa: BLE001 - the loop reports cycle failures
                log.warning("marketplace refresh ended while draining: %s", exc)

        loop_task = self._task
        if loop_task is not None and not loop_task.done():
            # At this point the provider cycle is complete (or hit the bounded
            # last-resort timeout), so cancellation only interrupts idle sleep.
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task
        self._task = None
        if cycle is not None and cycle.done() and self._cycle_task is cycle:
            self._cycle_task = None
