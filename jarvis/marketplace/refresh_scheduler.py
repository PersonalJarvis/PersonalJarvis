"""Token-refresh scheduler for connected marketplace plugins (Wave 2, #3).

OAuth access tokens expire — from ~30 min (HubSpot) to ~24 h (Linear). Without
proactive refresh a long-lived backend silently starts getting 401s mid-session.
This scheduler periodically refreshes tokens nearing expiry via each plugin's
:class:`~jarvis.marketplace.auth.base.AuthHandler` and writes the new tokens
back to the :class:`~jarvis.marketplace.token_store.TokenStore`. A ``revoked``
refresh (auth server returns ``invalid_grant``) drops the entry so the UI can
surface a "Reconnect" prompt rather than looping on a dead token.

The pure core :func:`refresh_due_tokens` takes its dependencies as arguments
(plugin ids, store, handler builder) so it is unit-testable without the real
catalog, keyring, or network. :class:`RefreshScheduler` is the thin loop around
it that the app starts at boot.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

from jarvis.marketplace.auth.base import AuthHandler
from jarvis.marketplace.token_store import TokenStore

log = logging.getLogger(__name__)

HandlerBuilder = Callable[[str], AuthHandler | None]
PluginIdsFn = Callable[[], list[str]]

# Per-plugin outcome labels (also the public vocabulary for telemetry/logs).
REFRESHED = "refreshed"
SKIPPED = "skipped"
REVOKED = "revoked"
FAILED = "failed"

# Substrings in a refresh RuntimeError message that mean the connection is
# un-healable without the user re-authenticating (or fixing the OAuth client) —
# as opposed to a transient server/network error that should be retried next
# cycle. Google reports OAuth errors via HTTP status + JSON body (not Slack's
# `ok:false` shape), so the handler raises e.g.
# ``RuntimeError("refresh HTTP 401: {...invalid_client...}")`` — the bare-string
# "revoked" check missed every Google failure, leaving needs_reauth False while
# the token rotted (live 2026-06-07 Gmail bug). Matching the canonical OAuth
# error codes + descriptions catches Google, Slack, and generic providers.
_REAUTH_ERROR_MARKERS: tuple[str, ...] = (
    "revoked",
    "invalid_grant",
    "invalid_client",
    "unauthorized_client",
    "client was not found",
    "token has been expired",
    # Legacy DCR token minted before client_id was persisted — un-refreshable,
    # one reconnect heals it (live 2026-06-08 audit: linear).
    "reconnect required",
    "no stored client_id",
)


def _refresh_needs_reauth(message: str) -> bool:
    """True iff a refresh-failure message indicates an un-healable auth error.

    Conservative on purpose: a generic transient failure (5xx, timeout, network)
    contains none of these markers and stays a retryable FAILED, so one flaky
    cycle never falsely flags a healthy plugin as needing reconnect."""
    m = message.lower()
    return any(marker in m for marker in _REAUTH_ERROR_MARKERS)


def _keep_alive_due(tokens: object, keep_alive_seconds: int | None) -> bool:
    """True when a token should be proactively refreshed to stay warm.

    Some providers expire a *refresh* token after a period of inactivity even
    while the access token still looks valid (or never carries an expiry at
    all). Exercising the refresh token on a fixed cadence keeps the connection
    alive indefinitely — the heart of "log in once, stay connected forever".

    Disabled (returns False) when keep_alive_seconds is None, so the plain
    near-expiry contract of refresh_due_tokens is unchanged by default."""
    if keep_alive_seconds is None or not getattr(tokens, "refresh", None):
        return False
    lr = tokens.extra.get("last_refreshed")  # type: ignore[attr-defined]
    if not lr:
        return True  # never stamped → refresh once to warm it + record the time
    try:
        last = datetime.fromisoformat(lr)
    except (ValueError, TypeError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (datetime.now(UTC) - last).total_seconds() >= keep_alive_seconds


async def refresh_due_tokens(
    plugin_ids: list[str],
    store: TokenStore,
    build_handler: HandlerBuilder,
    *,
    threshold_seconds: int = 600,
    keep_alive_seconds: int | None = None,
    on_refreshed: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Refresh every connected plugin whose token is due.

    A token is *due* when its access token is near expiry OR (when
    ``keep_alive_seconds`` is set) it has not been refreshed within that window —
    the keep-alive sweep that keeps refresh tokens warm so a connection survives
    forever, even for providers whose access token is long-lived / never expires.

    ``on_refreshed``, when given, fires once per plugin id right after its fresh
    token is saved — the hook the live MCP session uses to pick up the new token
    instead of continuing to 401 with the stale one. A callback failure is
    swallowed and logged; a UI-refresh hiccup must never break the token save
    that already succeeded, nor take down the rest of the cycle.

    Returns a ``{plugin_id: outcome}`` map (outcomes from the module constants).
    Never raises — a single plugin's failure is isolated so one dead connection
    cannot stall the whole cycle.
    """
    outcomes: dict[str, str] = {}
    for pid in plugin_ids:
        try:
            tokens = store.load(pid)
        except RuntimeError:
            # Corrupted blob — surface as FAILED, leave it for the UI to fix.
            outcomes[pid] = FAILED
            continue

        if tokens is None or not tokens.refresh:
            outcomes[pid] = SKIPPED
            continue
        due = tokens.is_near_expiry(threshold_seconds) or _keep_alive_due(
            tokens, keep_alive_seconds
        )
        if not due:
            outcomes[pid] = SKIPPED
            continue

        handler = build_handler(pid)
        if handler is None:
            outcomes[pid] = SKIPPED
            continue

        try:
            new_tokens = await handler.refresh(tokens)
        except RuntimeError as exc:
            if _refresh_needs_reauth(str(exc)):
                # Do NOT delete — keep the entry and flag it so the UI shows a
                # "Reconnect" prompt. A connected plugin must never silently
                # disappear; the only user-visible delete path is an explicit
                # DELETE. (Previously this called store.delete(pid).)
                store.save(pid, dataclasses.replace(tokens, needs_reauth=True))
                outcomes[pid] = REVOKED
                log.info("plugin %s refresh needs reauth — marked needs_reauth: %s", pid, exc)
            else:
                outcomes[pid] = FAILED
                log.warning("plugin %s refresh failed (transient, will retry): %s", pid, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            outcomes[pid] = FAILED
            log.warning("plugin %s refresh errored: %s", pid, exc)
            continue

        # A healthy refresh clears any stale needs_reauth flag and stamps the
        # refresh time so the keep-alive sweep can skip it until the next window.
        merged_extra = {**new_tokens.extra, "last_refreshed": datetime.now(UTC).isoformat()}
        store.save(
            pid,
            dataclasses.replace(new_tokens, extra=merged_extra, needs_reauth=False),
        )
        outcomes[pid] = REFRESHED

        if on_refreshed is not None:
            try:
                on_refreshed(pid)
            except Exception as exc:  # noqa: BLE001 — a UI-refresh hiccup must not kill the loop
                log.warning("refresh: on_refreshed callback failed for %s: %s", pid, exc)
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
        keep_alive_seconds: int | None = 43_200,  # 12h — keep refresh tokens warm
        on_refreshed: Callable[[str], None] | None = None,
    ) -> None:
        self._plugin_ids_fn = plugin_ids_fn
        self._store = store
        self._build_handler = build_handler
        self._interval = interval_seconds
        self._threshold = threshold_seconds
        self._keep_alive_seconds = keep_alive_seconds
        self._on_refreshed = on_refreshed
        self._task: asyncio.Task[None] | None = None

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
        while True:
            try:
                outcomes = await self.run_once()
                self._log_cycle(outcomes)
            except Exception as exc:  # noqa: BLE001
                log.warning("refresh cycle failed: %s", exc)
            await asyncio.sleep(self._interval)

    @staticmethod
    def _log_cycle(outcomes: dict[str, str]) -> None:
        """Per-cycle observability: a connection silently rotting forever was the
        whole failure mode (live 2026-06-08 audit). Log a one-line summary so the
        refresh loop is visibly alive — at INFO when anything changed, else DEBUG."""
        counts: dict[str, int] = {}
        for outcome in outcomes.values():
            counts[outcome] = counts.get(outcome, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no plugins"
        changed = any(counts.get(k) for k in (REFRESHED, REVOKED, FAILED))
        (log.info if changed else log.debug)("token refresh cycle: %s", summary)

        # Name dead connections explicitly at WARNING. An anonymous "revoked=1"
        # count let a revoked plugin rot unseen (live: linear sat dead ~22 days,
        # gmail ~16 days, unnoticed until next use). Only a user reconnect heals
        # a revoked refresh token, so surface exactly which plugin needs it.
        dead = sorted(pid for pid, outcome in outcomes.items() if outcome == REVOKED)
        if dead:
            log.warning(
                "marketplace plugin(s) need reconnect — refresh token revoked: %s",
                ", ".join(dead),
            )

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="marketplace-refresh")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
