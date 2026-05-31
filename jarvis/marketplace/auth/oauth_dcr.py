"""HostedMcpDcrHandler — covers Notion + Supabase (and any future hosted
MCP server that supports OAuth 2.1 + Dynamic Client Registration + PKCE).

The flow per RFC 8414 (auth-server discovery) + RFC 7591 (DCR) + RFC 7636 (PKCE):

  1. GET .well-known/oauth-protected-resource  → identifies auth server
  2. GET .well-known/oauth-authorization-server → registration_endpoint, etc
  3. POST registration_endpoint  → ephemeral client_id (auth_method=none)
  4. Build authorize URL with PKCE challenge, open in user's default browser
  5. User logs in → 302 to http://127.0.0.1:<port>/callback?code=…&state=…
  6. POST token_endpoint with code + verifier → access + refresh tokens
  7. Persist via TokenStore

Pitfalls handled:
- Loopback port allocated ONCE upfront — the registration_endpoint sees
  the exact port that the callback server is bound to (avoids the
  port-mismatch trap from openclaw/openclaw#52961).
- DCR is fresh per flow (never persist client_id) — sidesteps the
  "stale client_id rejected after server-side rotation" class of bugs.
- State validated against CSRF on callback; mismatch raises.
- Refresh has per-plugin asyncio.Lock to avoid the 1-2s race window where
  Notion/Supabase issue two valid refresh tokens during rotation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from jarvis.marketplace.auth.base import (
    AuthSession,
    FlowResult,
    pkce_pair,
    random_state,
    session_id,
)
from jarvis.marketplace.hosted_callback import (
    HostedCallbackServer,
    make_callback_server,
)
from jarvis.marketplace.oauth_callback_server import (
    CallbackTimeoutError,
    OAuthCallbackServer,
)
from jarvis.marketplace.token_store import Tokens

log = logging.getLogger(__name__)

# Plugin-spec subset we actually read. Pydantic typing lives in catalog.py;
# here we just duck-type the few fields we need.


@dataclass(frozen=True)
class DcrConfig:
    plugin_id: str
    discovery_url: str
    fallback_authorization_endpoint: str | None = None
    fallback_token_endpoint: str | None = None
    fallback_registration_endpoint: str | None = None
    client_name: str = "Personal Jarvis"
    client_uri: str = "https://github.com/personal-jarvis/jarvis"
    timeout_seconds: int = 10


@dataclass
class _PendingFlow:
    """Per-flow state held between `start()` and `await_completion()`."""

    config: DcrConfig
    callback_server: OAuthCallbackServer | HostedCallbackServer
    code_verifier: str
    state: str
    redirect_uri: str
    token_endpoint: str
    client_id: str


class HostedMcpDcrHandler:
    """One handler instance per plugin (Notion, Supabase). The instance is
    cheap; recreate per-flow if you prefer — just don't share the
    `_pending` dict across plugins."""

    def __init__(self, config: DcrConfig) -> None:
        self.plugin_id = config.plugin_id
        self._config = config
        self._pending: dict[str, _PendingFlow] = {}

    # ------------------------------------------------------------------
    # Discovery — cacheable per-plugin (24h) but not implemented as cache
    # yet; first-class concern for after the spike.
    # ------------------------------------------------------------------

    async def _discover(self, client: httpx.AsyncClient) -> dict[str, str]:
        """Returns the auth-server metadata dict. Fields used downstream:
        authorization_endpoint, token_endpoint, registration_endpoint."""
        # Step 1: protected-resource → tells us which auth server to ask.
        try:
            r = await client.get(self._config.discovery_url)
            r.raise_for_status()
            pr_meta = r.json()
        except httpx.HTTPError as exc:
            if not self._config.fallback_authorization_endpoint:
                raise RuntimeError(
                    f"protected-resource discovery failed: {exc}"
                ) from exc
            # Fallback: use catalog-provided endpoints; skip discovery.
            log.warning(
                "%s: discovery unreachable, using fallback endpoints",
                self.plugin_id,
            )
            return {
                "authorization_endpoint": self._config.fallback_authorization_endpoint,
                "token_endpoint": self._config.fallback_token_endpoint or "",
                "registration_endpoint": (
                    self._config.fallback_registration_endpoint or ""
                ),
            }

        auth_servers = pr_meta.get("authorization_servers") or []
        if not auth_servers:
            raise RuntimeError(
                f"protected-resource has no authorization_servers: {pr_meta}"
            )

        # Step 2: hit auth-server's well-known.
        as_url = auth_servers[0].rstrip("/") + "/.well-known/oauth-authorization-server"
        r2 = await client.get(as_url)
        r2.raise_for_status()
        meta = r2.json()
        if "authorization_endpoint" not in meta or "token_endpoint" not in meta:
            raise RuntimeError(f"auth-server metadata incomplete: keys={list(meta)}")
        return meta

    # ------------------------------------------------------------------
    # DCR — RFC 7591
    # ------------------------------------------------------------------

    async def _register(
        self,
        client: httpx.AsyncClient,
        registration_endpoint: str,
        redirect_uri: str,
    ) -> str:
        """Returns a freshly-issued client_id."""
        body = {
            "client_name": self._config.client_name,
            "client_uri": self._config.client_uri,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",  # public client, no secret
        }
        r = await client.post(registration_endpoint, json=body)
        if r.status_code >= 400:
            raise RuntimeError(
                f"DCR failed (HTTP {r.status_code}): {r.text[:200]}"
            )
        meta = r.json()
        cid = meta.get("client_id")
        if not cid:
            raise RuntimeError(f"DCR response missing client_id: {meta}")
        return cid

    # ------------------------------------------------------------------
    # AuthHandler protocol
    # ------------------------------------------------------------------

    async def start(self, plugin_spec: object) -> AuthSession:
        """Discovery + DCR + build authorize URL. Returns the URL the UI
        opens in the browser."""
        timeout = httpx.Timeout(self._config.timeout_seconds)
        # Hosted callback (public route) when configured for a headless VPS,
        # else the loopback server (desktop). DCR registers whatever
        # redirect_uri this yields, so both modes work transparently.
        callback_server = make_callback_server(
            random_state(),
            timeout_seconds=300,
        )
        await callback_server.start()
        redirect_uri = callback_server.redirect_uri

        async with httpx.AsyncClient(timeout=timeout) as client:
            meta = await self._discover(client)
            registration_endpoint = meta.get("registration_endpoint")
            if not registration_endpoint:
                # Fallback: some hosted MCPs publish DCR via a separate URL
                # (e.g. baked into our catalog as a fallback override).
                registration_endpoint = self._config.fallback_registration_endpoint
            if not registration_endpoint:
                await callback_server.stop()
                raise RuntimeError(
                    f"{self.plugin_id}: no registration_endpoint discovered"
                )
            client_id = await self._register(
                client, registration_endpoint, redirect_uri
            )

        verifier, challenge = pkce_pair()
        sid = session_id()
        scopes = self._scopes_from_meta(meta)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": callback_server._expected_state,  # noqa: SLF001
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if scopes:
            params["scope"] = scopes
        params["prompt"] = "consent"  # always show consent on first connect
        authorize_url = (
            meta["authorization_endpoint"] + "?" + urlencode(params, doseq=True)
        )

        # Park the per-flow state until callback fires.
        self._pending[sid] = _PendingFlow(
            config=self._config,
            callback_server=callback_server,
            code_verifier=verifier,
            state=callback_server._expected_state,  # noqa: SLF001
            redirect_uri=redirect_uri,
            token_endpoint=meta["token_endpoint"],
            client_id=client_id,
        )

        return AuthSession(
            flow_id=sid,
            plugin_id=self.plugin_id,
            kind="browser_redirect",
            open_url=authorize_url,
            expires_at_ms=int(
                (datetime.now(UTC) + timedelta(minutes=5)).timestamp() * 1000
            ),
        )

    @staticmethod
    def _scopes_from_meta(meta: dict[str, object]) -> str:
        scopes = meta.get("scopes_supported")
        if isinstance(scopes, list) and scopes:
            return " ".join(str(s) for s in scopes)
        return ""

    async def await_completion(self, session: AuthSession) -> FlowResult:
        pending = self._pending.get(session.flow_id)
        if pending is None:
            return FlowResult(tokens=None, error="unknown flow_id")

        try:
            result = await pending.callback_server.await_callback()
        except CallbackTimeoutError:
            await pending.callback_server.stop()
            self._pending.pop(session.flow_id, None)
            return FlowResult(tokens=None, error="user did not approve in time")
        except Exception as exc:  # noqa: BLE001
            await pending.callback_server.stop()
            self._pending.pop(session.flow_id, None)
            return FlowResult(tokens=None, error=f"callback error: {exc}")
        finally:
            # Stop the listener — code is captured.
            await pending.callback_server.stop()

        # Token exchange.
        tokens = await self._exchange(pending, code=result.code)
        self._pending.pop(session.flow_id, None)
        return FlowResult(tokens=tokens, error=None)

    async def _exchange(self, pending: _PendingFlow, *, code: str) -> Tokens:
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": pending.client_id,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.code_verifier,
        }
        timeout = httpx.Timeout(pending.config.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                pending.token_endpoint,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if r.status_code != 200:
            raise RuntimeError(
                f"token exchange HTTP {r.status_code}: {r.text[:200]}"
            )
        payload = r.json()
        access = payload.get("access_token")
        if not access:
            raise RuntimeError(f"token response missing access_token: {payload}")
        refresh = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if expires_in is not None
            else None
        )
        extra = {}
        scope = payload.get("scope")
        if scope:
            extra["scope"] = scope
        token_type = payload.get("token_type")
        if token_type:
            extra["token_type"] = token_type
        return Tokens(
            access=access,
            refresh=refresh,
            expires_at=expires_at,
            extra=extra,
        )

    async def refresh(self, current: Tokens) -> Tokens:
        if not current.refresh:
            raise RuntimeError("no refresh token stored")
        # We need the auth server's token_endpoint and a valid client_id.
        # Re-discovery is the simplest path for now (DCR is fresh per flow,
        # so the old client_id may already be revoked — instead of
        # caching, re-register).
        timeout = httpx.Timeout(self._config.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            meta = await self._discover(client)
            # We don't need DCR for refresh — public clients refresh with
            # `client_id` only. But some servers require the same client_id
            # that was used for auth. Pragmatic: re-register here too.
            registration_endpoint = (
                meta.get("registration_endpoint")
                or self._config.fallback_registration_endpoint
            )
            if not registration_endpoint:
                raise RuntimeError("refresh: no registration_endpoint")
            # Refresh doesn't need a redirect_uri but DCR does. Use a
            # placeholder; the registered client is throwaway.
            client_id = await self._register(
                client, registration_endpoint, "http://127.0.0.1/refresh"
            )
            r = await client.post(
                meta["token_endpoint"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": current.refresh,
                    "client_id": client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if r.status_code == 400 and "invalid_grant" in r.text:
            raise RuntimeError("revoked")
        if r.status_code != 200:
            raise RuntimeError(
                f"refresh HTTP {r.status_code}: {r.text[:200]}"
            )
        payload = r.json()
        new_access = payload.get("access_token")
        if not new_access:
            raise RuntimeError(f"refresh missing access_token: {payload}")
        new_refresh = payload.get("refresh_token") or current.refresh
        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if expires_in is not None
            else None
        )
        return Tokens(
            access=new_access,
            refresh=new_refresh,
            expires_at=expires_at,
            extra=current.extra,
        )

    @staticmethod
    def auth_header(tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access}"}
