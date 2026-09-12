"""Auth handler protocol + shared session shape.

Three concrete handlers implement this:

  * `HostedMcpDcrHandler`  — Notion, Supabase (DCR + PKCE on hosted MCP)
  * `PkceLoopbackHandler`  — Slack (pre-registered client_id + PKCE)
  * `DeviceFlowHandler`    — GitHub (no browser-redirect, user-code dance)

`pat_paste` plugins (Vercel today) bypass this protocol entirely and use
the dedicated `/connect/pat` endpoint.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from jarvis.marketplace.token_store import Tokens

# ---------------------------------------------------------------------------
# Shared session shape — the frontend reads this to render the right dialog
# ---------------------------------------------------------------------------


SessionKind = Literal["browser_redirect", "device_flow"]


@dataclass(frozen=True, slots=True)
class AuthSession:
    """Immutable handle returned by `start()`. The frontend uses `kind` to
    pick the dialog component."""

    flow_id: str
    plugin_id: str
    kind: SessionKind
    # Browser-redirect kinds: open this URL in the user's default browser.
    open_url: str | None = None
    # Device-flow only: code the user types on `verification_uri`.
    user_code: str | None = None
    verification_uri: str | None = None
    verification_uri_complete: str | None = None
    # Both kinds: when does this session expire (epoch ms).
    expires_at_ms: int | None = None
    # Device-flow only: poll interval the auth server requested (seconds).
    interval: int | None = None


@dataclass(frozen=True, slots=True)
class FlowResult:
    """The outcome of a completed flow. Either `tokens` is set OR `error`
    is set — never both."""

    tokens: Tokens | None
    error: str | None
    extra: dict[str, Any] = field(default_factory=dict)
    # Machine-readable outcome for the connect dialog. One of the ERROR_*
    # codes below, or None on success. Lets the UI render "denied",
    # "admin approval needed", "timed out", "try again" instead of raw
    # provider text. Additive: older callers leave it None.
    error_code: str | None = None


# Machine-readable connect-flow outcomes (backend → dialog). Persisted or
# shown strings must use these codes; provider raw bodies must never reach
# storage or the UI (they can echo a token back).
ERROR_DENIED = "denied"
"""The user (or their admin) refused the authorization at the provider."""
ERROR_TIMEOUT = "timeout"
"""The user did not approve in time."""
ERROR_PROVIDER_UNREACHABLE = "provider_unreachable"
"""The provider endpoint could not be reached or answered unusably."""
ERROR_PORT_IN_USE = "port_in_use"
"""The loopback callback port is occupied by another process."""
ERROR_MISCONFIGURED = "misconfigured"
"""Publisher client missing/placeholder — admin provisioning required."""
ERROR_UNKNOWN = "unknown"
"""Anything else; retry may help."""


def sanitize_provider_error(detail: str, *, limit: int = 160) -> str:
    """Normalize untrusted bodies without retaining any provider-supplied text.

    Redaction cannot recognize arbitrary credentials or personal information.
    Only fixed product messages may cross the provider error boundary.
    """
    lowered = detail.lower()
    if "access_denied" in lowered:
        message = "Authorization was declined. Try again when access is approved."
    elif "invalid_client" in lowered or "unauthorized_client" in lowered:
        message = "invalid_client: The provider does not accept this app registration."
    elif "invalid_grant" in lowered:
        message = "Authorization expired or was revoked. Connect again."
    else:
        message = "provider request failed"
    return message[:max(0, limit)]


class AuthHandler(Protocol):
    """The 4 lifecycle methods every handler implements."""

    plugin_id: str

    async def start(self, plugin_spec: Any) -> AuthSession:
        """Kick off the flow. Returns a session the UI renders."""

    async def await_completion(self, session: AuthSession) -> FlowResult:
        """Block until the user finishes (success or denial). The handler
        decides the strategy: device flow polls, browser-redirect waits on
        a Future fed by the loopback callback server."""

    async def refresh(self, current: Tokens) -> Tokens:
        """Use the refresh token to mint a new access token. Raises
        `RuntimeError("revoked")` if the auth server returns
        `invalid_grant` — the caller should drop the entry from the store
        and surface a "Reconnect" prompt."""

    def auth_header(self, tokens: Tokens) -> dict[str, str]:
        """The header dict to send with downstream MCP / REST requests."""


# ---------------------------------------------------------------------------
# PKCE — RFC 7636
# ---------------------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge)."""
    raw = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def random_state(n_bytes: int = 32) -> str:
    return secrets.token_urlsafe(n_bytes)


def session_id() -> str:
    return secrets.token_urlsafe(16)


# ---------------------------------------------------------------------------
# In-process flow registry — bridges `start()` and `await_completion()`
# across REST endpoints. Backend restart drops everything (acceptable;
# user just clicks "+" again).
# ---------------------------------------------------------------------------


@dataclass
class _FlowSlot:
    handler: AuthHandler
    session: AuthSession
    completion_lock: asyncio.Lock
    result: FlowResult | None = None


class FlowRegistry:
    """Lookup by `flow_id`. Threadsafe via per-slot lock."""

    def __init__(self) -> None:
        self._slots: dict[str, _FlowSlot] = {}

    def put(self, handler: AuthHandler, session: AuthSession) -> None:
        self._slots[session.flow_id] = _FlowSlot(
            handler=handler,
            session=session,
            completion_lock=asyncio.Lock(),
        )

    def get(self, flow_id: str) -> _FlowSlot | None:
        return self._slots.get(flow_id)

    def drop(self, flow_id: str) -> None:
        self._slots.pop(flow_id, None)

    def gc_expired(self, now_ms: int) -> int:
        """Drop slots whose session expired more than 60s ago."""
        dead = [
            fid
            for fid, s in self._slots.items()
            if s.session.expires_at_ms is not None
            and s.session.expires_at_ms + 60_000 < now_ms
        ]
        for fid in dead:
            self._slots.pop(fid, None)
        return len(dead)


_REGISTRY = FlowRegistry()


def get_registry() -> FlowRegistry:
    return _REGISTRY


def now_ms() -> int:
    return int(datetime.utcnow().timestamp() * 1000)
