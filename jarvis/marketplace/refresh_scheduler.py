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
import logging
from collections.abc import Callable
from contextlib import suppress

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


async def refresh_due_tokens(
    plugin_ids: list[str],
    store: TokenStore,
    build_handler: HandlerBuilder,
    *,
    threshold_seconds: int = 600,
) -> dict[str, str]:
    """Refresh every connected plugin whose access token is near expiry.

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
        if not tokens.is_near_expiry(threshold_seconds):
            outcomes[pid] = SKIPPED
            continue

        handler = build_handler(pid)
        if handler is None:
            outcomes[pid] = SKIPPED
            continue

        try:
            new_tokens = await handler.refresh(tokens)
        except RuntimeError as exc:
            if "revoked" in str(exc):
                store.delete(pid)
                outcomes[pid] = REVOKED
                log.info("plugin %s refresh token revoked — dropped", pid)
            else:
                outcomes[pid] = FAILED
                log.warning("plugin %s refresh failed: %s", pid, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            outcomes[pid] = FAILED
            log.warning("plugin %s refresh errored: %s", pid, exc)
            continue

        store.save(pid, new_tokens)
        outcomes[pid] = REFRESHED
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
    ) -> None:
        self._plugin_ids_fn = plugin_ids_fn
        self._store = store
        self._build_handler = build_handler
        self._interval = interval_seconds
        self._threshold = threshold_seconds
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> dict[str, str]:
        return await refresh_due_tokens(
            self._plugin_ids_fn(),
            self._store,
            self._build_handler,
            threshold_seconds=self._threshold,
        )

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("refresh cycle failed: %s", exc)
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="marketplace-refresh")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
