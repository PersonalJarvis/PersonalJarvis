"""Hosted OAuth callback rendezvous for headless / VPS deployments.

The loopback :class:`~jarvis.marketplace.oauth_callback_server.OAuthCallbackServer`
only works when the user's browser can reach ``127.0.0.1`` — i.e. on the same
machine as the backend. On a headless VPS the OAuth provider must redirect to a
*public* HTTPS callback served by the main FastAPI app instead.

This module is that rendezvous. A :class:`HostedCallbackServer` parks a per-flow
:class:`asyncio.Future` keyed by the OAuth ``state``; the public route
``GET /api/marketplace/oauth/callback`` hands the captured ``(code, state)`` to
the waiting flow via :func:`deliver_callback`. The lookup-by-state *is* the CSRF
check — an unknown state is rejected.

``HostedCallbackServer`` mirrors the duck-typed interface of
``OAuthCallbackServer`` (``start`` / ``redirect_uri`` / ``await_callback`` /
``stop`` / ``_expected_state``) so a redirect handler picks one or the other
through :func:`make_callback_server` with no other code change.
"""

from __future__ import annotations

import asyncio
import logging

from jarvis.marketplace.oauth_callback_server import (
    _ERROR_HTML,
    _SUCCESS_HTML,
    CallbackResult,
    CallbackTimeoutError,
    OAuthCallbackServer,
)

log = logging.getLogger(__name__)

DEFAULT_CALLBACK_PATH = "/api/marketplace/oauth/callback"

# Public aliases for the route module — identical markup to the loopback server
# so both callback styles render the same "Connected." / error page.
SUCCESS_HTML = _SUCCESS_HTML
ERROR_HTML = _ERROR_HTML

# state -> waiting server. Process-local; a backend restart drops in-flight
# flows (acceptable — the user just clicks "Connect" again).
_PENDING: dict[str, HostedCallbackServer] = {}

# Runtime-applied public base URL, set at app startup from
# ``cfg.marketplace.public_callback_base_url``. Empty = loopback/desktop mode.
_PUBLIC_CALLBACK_BASE_URL = ""


def set_public_callback_base_url(url: str) -> None:
    """Apply the public callback base URL (called once at app startup)."""
    global _PUBLIC_CALLBACK_BASE_URL
    _PUBLIC_CALLBACK_BASE_URL = (url or "").strip()


def get_public_callback_base_url() -> str:
    return _PUBLIC_CALLBACK_BASE_URL


class HostedCallbackServer:
    """Loopback-server look-alike that resolves via a public hosted route.

    Unlike :class:`OAuthCallbackServer` it binds no socket; the captured code
    arrives out-of-band through :func:`deliver_callback` on the main app.
    """

    def __init__(
        self,
        expected_state: str,
        base_url: str,
        timeout_seconds: float = 300.0,
        callback_path: str = DEFAULT_CALLBACK_PATH,
    ) -> None:
        if not expected_state:
            raise ValueError("expected_state must be non-empty")
        if not base_url:
            raise ValueError("base_url must be non-empty")
        self._expected_state = expected_state
        self._base_url = base_url.rstrip("/")
        self._path = callback_path
        self._timeout = timeout_seconds
        self._future: asyncio.Future[CallbackResult] | None = None

    @property
    def redirect_uri(self) -> str:
        return f"{self._base_url}{self._path}"

    async def start(self) -> None:
        if self._future is not None:
            raise RuntimeError("already started")
        self._future = asyncio.get_running_loop().create_future()
        _PENDING[self._expected_state] = self

    def _resolve(
        self,
        result: CallbackResult | None = None,
        exc: BaseException | None = None,
    ) -> None:
        if self._future is None or self._future.done():
            return
        if exc is not None:
            self._future.set_exception(exc)
        else:
            assert result is not None
            self._future.set_result(result)

    async def await_callback(self) -> CallbackResult:
        if self._future is None:
            raise RuntimeError("server not started")
        try:
            return await asyncio.wait_for(self._future, timeout=self._timeout)
        except TimeoutError as exc:
            raise CallbackTimeoutError(
                f"no callback received within {self._timeout}s"
            ) from exc
        finally:
            _PENDING.pop(self._expected_state, None)

    async def stop(self) -> None:
        _PENDING.pop(self._expected_state, None)
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None


def deliver_callback(code: str, state: str, error: str | None = None) -> bool:
    """Hand a captured OAuth redirect to the waiting flow.

    Returns ``True`` if a flow was waiting on ``state`` (the CSRF check), else
    ``False`` (unknown or expired state — the route should show an error page).
    """
    srv = _PENDING.get(state)
    if srv is None:
        log.warning("hosted oauth callback for unknown/expired state")
        return False
    if error:
        srv._resolve(exc=RuntimeError(f"OAuth provider returned error: {error}"))
    elif not code:
        srv._resolve(exc=RuntimeError("missing 'code' parameter"))
    else:
        srv._resolve(result=CallbackResult(code=code, state=state))
    return True


def make_callback_server(
    expected_state: str,
    *,
    timeout_seconds: float = 300.0,
    fixed_port: int | None = None,
) -> HostedCallbackServer | OAuthCallbackServer:
    """Return a hosted callback server when a public base URL is configured,
    else the loopback server (the desktop power-user path).

    ``fixed_port`` only applies to the loopback server (e.g. Slack's registered
    redirect_uri); it is ignored in hosted mode.
    """
    base = get_public_callback_base_url()
    if base:
        return HostedCallbackServer(expected_state, base, timeout_seconds=timeout_seconds)
    return OAuthCallbackServer(
        expected_state=expected_state,
        timeout_seconds=timeout_seconds,
        port=fixed_port,
    )
