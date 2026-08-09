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
from jarvis.marketplace.token_store import (
    REAUTH_CLIENT_MISSING,
    REAUTH_CLIENT_REJECTED,
    REAUTH_PROVIDER_REJECTED,
    REAUTH_ROTATION_LOST,
    Tokens,
    TokenStore,
)

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


# Ordered most-specific first: a DCR handler raises "no stored client_id —
# reconnect required", which matches two markers, and the FIRST match wins. The
# marker itself is our own literal, so classifying this way keeps every persisted
# reason free of provider text (which can quote a token back at us).
_REAUTH_ERROR_MARKERS: tuple[tuple[str, str], ...] = (
    ("no stored client_id", REAUTH_CLIENT_MISSING),
    ("client was not found", REAUTH_CLIENT_REJECTED),
    ("unauthorized_client", REAUTH_CLIENT_REJECTED),
    ("invalid_client", REAUTH_CLIENT_REJECTED),
    ("invalid_grant", REAUTH_PROVIDER_REJECTED),
    ("token has been expired", REAUTH_PROVIDER_REJECTED),
    ("reconnect required", REAUTH_PROVIDER_REJECTED),
    ("revoked", REAUTH_PROVIDER_REJECTED),
)


def reauth_reason_for(message: str) -> str | None:
    """Classify a refresh failure, or return ``None`` when it is transient.

    ``None`` means "retry on the next cycle"; any code means the grant cannot be
    healed without the user. Returning the CODE rather than a bool is what lets
    the UI say which of the two happened days later.
    """
    lowered = message.lower()
    for marker, reason in _REAUTH_ERROR_MARKERS:
        if marker in lowered:
            return reason
    return None


def _self_heal_due(tokens: Tokens, retry_seconds: int | None) -> bool:
    """Whether a reauth-flagged connection may be probed once more.

    The flag can be wrong. A provider outage answering ``invalid_client``, or a
    momentary DNS failure surfacing as a hard rejection, strands a grant whose
    refresh token is still perfectly good — and nothing ever retried it, so the
    connection stayed dead until the user noticed and reconnected by hand. The
    flag therefore decays: every ``retry_seconds`` the connection gets ONE more
    attempt, and a success clears it (``needs_reauth=False`` on the saved token).

    ``REAUTH_ROTATION_LOST`` is the one exception and never retries. There the
    provider has already retired the token we hold, so presenting it again is
    exactly the replay that some providers treat as theft and answer by revoking
    every token on the account. A stranded connection costs one sign-in; that
    mistake costs the whole account.

    A probe is cheap — one request per dead plugin per day — and the failure it
    repairs is otherwise permanent.
    """
    if retry_seconds is None or not tokens.refresh:
        return False
    if tokens.reauth_reason == REAUTH_ROTATION_LOST:
        return False
    last_try = tokens.reauth_retry_at or tokens.reauth_at
    if last_try is None:
        # Flagged before this field existed: probe on the next cycle, then the
        # written `reauth_retry_at` puts it on the normal cadence.
        return True
    return (datetime.now(UTC) - last_try).total_seconds() >= retry_seconds


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


def flag_for_reauth(tokens: Tokens, reason: str) -> Tokens:
    """Flag a grant for reconnect, recording WHY and WHEN it first failed.

    ``reauth_at`` is preserved across repeated failures so it keeps meaning "when
    this connection died" rather than "when we last confirmed it is still dead".
    """
    return dataclasses.replace(
        tokens,
        needs_reauth=True,
        reauth_reason=reason,
        reauth_at=tokens.reauth_at or datetime.now(UTC),
    )


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


# A rotated refresh token is a point of no return: the provider has already
# retired the previous one. Give the local store several attempts before
# accepting that it is lost — the write is local and its failures (a
# momentarily locked keyring, a contended credential store) are usually
# transient.
_ROTATED_SAVE_ATTEMPTS = 4
_ROTATED_SAVE_BACKOFF_SECONDS = 0.25


async def _save_with_retries(
    store: TokenStore, plugin_id: str, tokens: Tokens, *, rotated: bool
) -> None:
    """Persist a refreshed token, retrying harder when rotation made it unique.

    Async so the backoff yields the event loop instead of blocking it — this
    runs inside the voice-capable process, where a blocking sleep would stall
    unrelated work.
    """
    attempts = _ROTATED_SAVE_ATTEMPTS if rotated else 1
    delay = _ROTATED_SAVE_BACKOFF_SECONDS
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            store.save(plugin_id, tokens)
            if attempt:
                log.info(
                    "plugin %s rotated token stored on attempt %d",
                    plugin_id,
                    attempt + 1,
                )
            return
        except Exception as exc:  # noqa: BLE001 - retried below, re-raised at the end
            last = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(delay)
                delay *= 2
    assert last is not None
    raise last


def _handle_lost_rotation(
    plugin_id: str,
    store: TokenStore,
    current: Tokens,
    exc: Exception,
) -> RefreshAttempt:
    """React to a rotated refresh token we could not store.

    This is the one failure that can destroy a connection permanently. Several
    providers (Todoist documents it explicitly) treat a replay of an already
    rotated refresh token as theft and revoke EVERY token for that account,
    which costs the user a full re-consent. The stored token is now the retired
    one, so simply "retrying later" would present exactly that replay.

    So the connection is marked for re-authorization immediately: the scheduler
    skips a ``needs_reauth`` entry, the card shows Reconnect, and one manual
    sign-in costs far less than a provider-side mass revocation. Being wrong
    here is cheap; being silent is not.
    """
    log.error(
        "plugin %s: the provider rotated its refresh token but it could not be "
        "stored (%s). The stored token is retired -- reusing it risks a "
        "provider-side revocation of every token for this account, so the "
        "connection is flagged for reconnect instead.",
        plugin_id,
        exc,
    )
    try:
        store.save(plugin_id, flag_for_reauth(current, REAUTH_ROTATION_LOST))
    except Exception as mark_exc:  # noqa: BLE001 - the store is evidently broken
        # Nothing durable can be written at all. Say so loudly rather than
        # leave a retry loop that would keep replaying the retired token.
        log.error(
            "plugin %s: could not even flag the connection for reconnect (%s). "
            "Reconnect it manually.",
            plugin_id,
            mark_exc,
        )
    return RefreshAttempt(REVOKED)


async def refresh_plugin_token(
    plugin_id: str,
    store: TokenStore,
    build_handler: HandlerBuilder,
    *,
    force: bool = False,
    observed_access_token: str | None = None,
    threshold_seconds: int = 600,
    keep_alive_seconds: int | None = None,
    reauth_retry_seconds: int | None = None,
) -> RefreshAttempt:
    """Refresh one plugin under a process-local single-flight lock.

    The token is reloaded after acquiring the lock. If another caller already
    replaced the access token that triggered a 401, the waiting caller reuses
    that token instead of rotating the refresh token a second time.

    ``reauth_retry_seconds`` opts into self-healing: a connection already
    flagged for reconnect is probed once per that interval instead of being
    skipped forever. Left ``None`` (the default) a flagged connection is never
    touched, so a direct caller keeps the old behaviour verbatim.
    """
    async with _refresh_lock(plugin_id):
        try:
            tokens = store.load(plugin_id)
        except Exception as exc:  # noqa: BLE001 - isolate one plugin's storage
            log.warning("plugin %s token load failed: %s", plugin_id, exc)
            return RefreshAttempt(FAILED)

        if tokens is None:
            return RefreshAttempt(SKIPPED)

        self_heal = False
        if tokens.needs_reauth:
            if not _self_heal_due(tokens, reauth_retry_seconds):
                return RefreshAttempt(SKIPPED)
            self_heal = True
            # Stamp the attempt BEFORE calling the provider. If that call hangs,
            # or the process dies mid-flight, this stamp is the only thing
            # stopping the next cycle five minutes later from probing again --
            # a dead plugin would otherwise hammer the provider forever.
            stamped = dataclasses.replace(tokens, reauth_retry_at=datetime.now(UTC))
            try:
                store.save(plugin_id, stamped)
            except Exception as exc:  # noqa: BLE001 - isolate one plugin's storage
                log.warning(
                    "plugin %s self-heal stamp failed, skipping this cycle: %s",
                    plugin_id,
                    exc,
                )
                return RefreshAttempt(FAILED)
            tokens = stamped
            log.info("plugin %s: retrying a connection flagged for reconnect", plugin_id)

        if observed_access_token is not None and tokens.access != observed_access_token:
            return RefreshAttempt(SKIPPED, usable=True, access_changed=True)
        if not tokens.refresh:
            return RefreshAttempt(SKIPPED, usable=not force)

        # A self-heal probe is due by definition -- its access token expired long
        # ago, and the whole point is to test the refresh token behind it.
        if not force and not self_heal:
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
            reason = reauth_reason_for(str(exc))
            if reason is None:
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
                store.save(plugin_id, flag_for_reauth(current, reason))
            except Exception as save_exc:  # noqa: BLE001 - isolate storage failure
                log.warning(
                    "plugin %s needs_reauth save failed, will retry: %s",
                    plugin_id,
                    save_exc,
                )
                return RefreshAttempt(FAILED)
            log.info(
                "plugin %s refresh needs reauth (%s): %s",
                plugin_id,
                reason,
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
            # A working refresh clears the whole reconnect story, not just the
            # flag: a stale reason left behind would keep explaining a failure
            # that no longer exists. Set explicitly rather than relying on the
            # handler having returned a pristine Tokens.
            needs_reauth=False,
            reauth_reason=None,
            reauth_at=None,
            reauth_retry_at=None,
        )
        if current.needs_reauth:
            log.info(
                "plugin %s: self-heal succeeded, connection is live again (was flagged %s)",
                plugin_id,
                current.reauth_reason or "for an unrecorded reason",
            )
        rotated = bool(refreshed.refresh) and refreshed.refresh != current.refresh
        try:
            await _save_with_retries(store, plugin_id, saved, rotated=rotated)
        except Exception as exc:  # noqa: BLE001 - isolate storage failure
            if not rotated:
                # The provider did not rotate, so the stored refresh token is
                # still the live one. Losing this write costs nothing but a
                # short-lived access token; the next cycle retries safely.
                log.warning(
                    "plugin %s refreshed token save failed, will retry: %s",
                    plugin_id,
                    exc,
                )
                return RefreshAttempt(FAILED)
            return _handle_lost_rotation(plugin_id, store, current, exc)
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
    reauth_retry_seconds: int | None = None,
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
            reauth_retry_seconds=reauth_retry_seconds,
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
        # Once a day. Frequent enough that a connection stranded by a provider
        # outage repairs itself well before the user next looks, rare enough
        # that a genuinely revoked grant costs one request a day.
        reauth_retry_seconds: int | None = 86_400,
        on_refreshed: Callable[[str], None] | None = None,
        shutdown_drain_timeout_seconds: float = 30.0,
    ) -> None:
        self._plugin_ids_fn = plugin_ids_fn
        self._store = store
        self._build_handler = build_handler
        self._interval = interval_seconds
        self._threshold = threshold_seconds
        self._keep_alive_seconds = keep_alive_seconds
        self._reauth_retry_seconds = reauth_retry_seconds
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
            reauth_retry_seconds=self._reauth_retry_seconds,
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
