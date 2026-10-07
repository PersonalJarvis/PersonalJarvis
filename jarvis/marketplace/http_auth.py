"""Keep remote MCP requests on the current Marketplace credential generation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import httpx

from jarvis.marketplace.refresh_scheduler import (
    HandlerBuilder,
    RefreshAttempt,
    refresh_plugin_token,
)
from jarvis.marketplace.token_store import Tokens, TokenStore

log = logging.getLogger(__name__)


class PluginHttpAuth(httpx.Auth):
    """Refresh before expiry and retry only an explicit HTTP authentication refusal.

    The SDK owns the HTTP session, but the Marketplace owns its credentials.
    Reading the store per request also picks up rotations by another app instance.
    Tool errors, transport failures and timeouts are never replayed here.
    """

    def __init__(
        self,
        plugin_id: str,
        url: str,
        store: TokenStore,
        build_handler: HandlerBuilder,
    ) -> None:
        self._plugin_id = plugin_id
        self._url = httpx.URL(url)
        self._store = store
        self._build_handler = build_handler

    def _tokens(self) -> Tokens:
        tokens = self._store.load(self._plugin_id)
        if tokens is None or tokens.needs_reauth:
            raise RuntimeError("Plugin disconnected or authorization expired; reconnect in Plugins")
        return tokens

    async def _refresh(self, *, observed: str | None = None) -> RefreshAttempt:
        async def refresh_bounded() -> RefreshAttempt:
            return await asyncio.wait_for(
                refresh_plugin_token(
                    self._plugin_id,
                    self._store,
                    self._build_handler,
                    force=observed is not None,
                    observed_access_token=observed,
                ),
                timeout=65,
            )

        task = asyncio.create_task(
            refresh_bounded(),
            name=f"plugin-http-refresh:{self._plugin_id}",
        )
        cancelled: asyncio.CancelledError | None = None
        # Closing an MCP stream must not discard an already-rotated grant.
        # Even repeated cancellation waits for this bounded save to finish.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                # Re-raise after the bounded credential save finishes below.
                cancelled = exc
            except Exception as exc:  # noqa: BLE001 - re-raised via task.result below
                log.debug(
                    "plugin %s HTTP refresh ended with %s",
                    self._plugin_id,
                    type(exc).__name__,
                )
                break
        try:
            result = task.result()
        except TimeoutError:
            log.warning("plugin %s HTTP refresh did not drain", self._plugin_id)
            if cancelled is not None:
                raise cancelled from None
            raise
        if cancelled is not None:
            raise cancelled
        return result

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        if (request.url.scheme, request.url.host, request.url.port) != (
            self._url.scheme,
            self._url.host,
            self._url.port,
        ):
            raise RuntimeError("Plugin credential destination does not match its MCP server")

        # An async auth-flow override must buffer explicitly; HTTPX only honors
        # requires_request_body in its default sync-to-async implementation.
        await request.aread()
        tokens = self._tokens()
        if tokens.is_near_expiry():
            await self._refresh()
            tokens = self._tokens()
            if tokens.expires_at is not None and tokens.expires_at <= datetime.now(UTC):
                raise RuntimeError(
                    "Plugin token refresh temporarily unavailable; try again shortly"
                )

        request.headers["Authorization"] = f"Bearer {tokens.access}"
        response = yield request
        if response.status_code != 401:
            return

        attempt = await self._refresh(observed=tokens.access)
        if not attempt.usable:
            return
        tokens = self._tokens()
        request.headers["Authorization"] = f"Bearer {tokens.access}"
        # Release the refused response before retrying a streaming request.
        await response.aclose()
        yield request
