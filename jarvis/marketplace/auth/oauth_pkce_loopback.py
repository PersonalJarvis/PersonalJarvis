"""PkceLoopbackHandler — covers Slack and Google-style loopback OAuth.

OAuth 2.1 + PKCE (RFC 7636) with a pre-registered client_id. Some providers
use a public client with no secret; Google's desktop-client JSON still carries
a client_secret and its token endpoint may require it.

The pattern works for any service that:
  - lets the developer mark an OAuth app as "PKCE-enabled / public client"
  - allows a localhost redirect URI
  - accepts code+verifier without secret at the token endpoint

Slack today, future candidates: anything else that adopts public-client
PKCE without DCR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
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


@dataclass(frozen=True)
class PkceLoopbackConfig:
    plugin_id: str
    authorization_url: str
    token_url: str
    client_id: str
    callback_port: int
    scopes: list[str]
    client_secret: str | None = None
    scope_separator: Literal["comma", "space"] = "comma"
    # Slack: scope is split into `scope=` (bot) and `user_scope=` (user).
    # PKCE-enabled Slack apps must use `user_scope=` — no bot tokens. So
    # the scope_param_name lets us swap between the two.
    scope_param_name: str = "scope"
    callback_path: str = "/oauth/callback"
    timeout_seconds: int = 10
    # OAuth `resource` indicator (RFC 8707) — Asana's V2 MCP server requires
    # `resource=https://mcp.asana.com/v2` on authorize + token requests. Inert
    # when unset.
    resource: str | None = None
    # Google desktop/loopback clients only return a refresh token when the
    # authorize request carries `access_type=offline` + `prompt=consent`.
    offline_access: bool = False


@dataclass
class _PendingPkceFlow:
    config: PkceLoopbackConfig
    callback_server: OAuthCallbackServer | HostedCallbackServer
    code_verifier: str
    redirect_uri: str


class PkceLoopbackHandler:
    """One handler per PKCE-loopback plugin (Slack today)."""

    def __init__(self, config: PkceLoopbackConfig) -> None:
        self.plugin_id = config.plugin_id
        self._config = config
        self._pending: dict[str, _PendingPkceFlow] = {}

    async def start(self, plugin_spec: object) -> AuthSession:
        # H3: route through make_callback_server so a configured
        # public_callback_base_url gives a publicly-reachable hosted redirect on a
        # VPS; the desktop loopback still binds the plugin's registered fixed port +
        # callback path (e.g. Slack's http://127.0.0.1:3118/<path>).
        state = random_state()
        callback_server = make_callback_server(
            state,
            timeout_seconds=300,
            fixed_port=self._config.callback_port,
            callback_path=self._config.callback_path,
        )
        try:
            await callback_server.start()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"could not start the {self._config.plugin_id} OAuth callback "
                f"server: {exc} (loopback port {self._config.callback_port} may be "
                "in use, or the hosted callback base URL is unreachable)"
            ) from exc

        redirect_uri = callback_server.redirect_uri
        verifier, challenge = pkce_pair()
        sid = session_id()

        params = self._authorize_params(
            redirect_uri=redirect_uri, state=state, challenge=challenge
        )
        url = self._config.authorization_url + "?" + urlencode(params, doseq=True)

        self._pending[sid] = _PendingPkceFlow(
            config=self._config,
            callback_server=callback_server,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
        )
        return AuthSession(
            flow_id=sid,
            plugin_id=self.plugin_id,
            kind="browser_redirect",
            open_url=url,
            expires_at_ms=int(
                (datetime.now(UTC) + timedelta(minutes=5)).timestamp() * 1000
            ),
        )

    def _authorize_params(
        self, *, redirect_uri: str, state: str, challenge: str
    ) -> dict[str, str]:
        """Build the authorize query params. Extracted so the optional
        `resource` / `offline_access` extensions are unit-testable without
        binding a socket."""
        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self._config.scopes:
            separator = " " if self._config.scope_separator == "space" else ","
            params[self._config.scope_param_name] = separator.join(self._config.scopes)
        if self._config.resource:
            params["resource"] = self._config.resource
        if self._config.offline_access:
            params["access_type"] = "offline"
            params["prompt"] = "consent"
        return params

    async def await_completion(self, session: AuthSession) -> FlowResult:
        pending = self._pending.get(session.flow_id)
        if pending is None:
            return FlowResult(tokens=None, error="unknown flow_id")
        try:
            cb = await pending.callback_server.await_callback()
        except CallbackTimeoutError:
            await pending.callback_server.stop()
            self._pending.pop(session.flow_id, None)
            return FlowResult(tokens=None, error="user did not approve in time")
        except Exception as exc:  # noqa: BLE001
            await pending.callback_server.stop()
            self._pending.pop(session.flow_id, None)
            return FlowResult(tokens=None, error=f"callback error: {exc}")
        finally:
            await pending.callback_server.stop()

        try:
            tokens = await self._exchange(pending, code=cb.code)
            return FlowResult(tokens=tokens, error=None)
        except RuntimeError as exc:
            return FlowResult(tokens=None, error=str(exc))
        finally:
            self._pending.pop(session.flow_id, None)

    async def _exchange(self, pending: _PendingPkceFlow, *, code: str) -> Tokens:
        body = {
            "client_id": pending.config.client_id,
            "code": code,
            "code_verifier": pending.code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": pending.redirect_uri,
        }
        if pending.config.client_secret:
            body["client_secret"] = pending.config.client_secret
        if pending.config.resource:
            body["resource"] = pending.config.resource
        timeout = httpx.Timeout(pending.config.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                pending.config.token_url,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Personal-Jarvis/1.0",
                },
            )
        if r.status_code != 200:
            raise RuntimeError(
                f"token exchange HTTP {r.status_code}: {r.text[:200]}"
            )
        payload = r.json()
        # Slack wraps success/failure in "ok" plus error codes; many other
        # providers return error inline. Handle both shapes.
        if payload.get("ok") is False:
            raise RuntimeError(
                f"token exchange failed: {payload.get('error', 'unknown')}"
            )
        # Slack's response nests the user token under `authed_user`.
        access = (
            payload.get("authed_user", {}).get("access_token")
            or payload.get("access_token")
        )
        if not access:
            raise RuntimeError(f"token response missing access_token: {payload}")
        refresh = (
            payload.get("authed_user", {}).get("refresh_token")
            or payload.get("refresh_token")
        )
        expires_in = payload.get("authed_user", {}).get("expires_in") or payload.get(
            "expires_in"
        )
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if expires_in is not None
            else None
        )
        extra: dict[str, str] = {}
        team = payload.get("team", {})
        if isinstance(team, dict) and team.get("id"):
            extra["team_id"] = team["id"]
        if isinstance(team, dict) and team.get("name"):
            extra["team_name"] = team["name"]
        scope = payload.get("authed_user", {}).get("scope") or payload.get("scope")
        if scope:
            extra["scope"] = scope
        # Bind the refresh token to the exact OAuth client that obtained it.
        # A user can later replace/delete the family-level client secret (or a
        # recovered keyring can expose an older value), but OAuth refresh tokens
        # remain bound to their issuing client. Keeping the pair inside the same
        # protected token blob makes refresh independent of that drift.
        extra["client_id"] = pending.config.client_id
        if pending.config.client_secret:
            extra["client_secret"] = pending.config.client_secret
        return Tokens(
            access=access, refresh=refresh, expires_at=expires_at, extra=extra
        )

    async def refresh(self, current: Tokens) -> Tokens:
        if not current.refresh:
            raise RuntimeError("no refresh token stored")
        # New grants persist their issuing client in ``extra``.  Once that
        # marker exists, the whole pair is authoritative: a missing bound
        # secret means the grant was issued to a public client and a newly
        # configured secret must not be injected.  Legacy token blobs have no
        # client_id marker and retain the old config-backed behavior.
        bound_client_id = current.extra.get("client_id")
        if bound_client_id:
            client_id = bound_client_id
            client_secret = current.extra.get("client_secret")
        else:
            client_id = self._config.client_id
            client_secret = self._config.client_secret
        refresh_body = {
            "grant_type": "refresh_token",
            "refresh_token": current.refresh,
            "client_id": client_id,
        }
        if client_secret:
            refresh_body["client_secret"] = client_secret
        if self._config.resource:
            refresh_body["resource"] = self._config.resource
        timeout = httpx.Timeout(self._config.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                self._config.token_url,
                data=refresh_body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        if r.status_code != 200:
            raise RuntimeError(f"refresh HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        if payload.get("ok") is False:
            err = payload.get("error", "unknown")
            if err in ("invalid_grant", "token_revoked", "invalid_refresh_token"):
                raise RuntimeError("revoked")
            raise RuntimeError(f"refresh failed: {err}")
        access = (
            payload.get("authed_user", {}).get("access_token")
            or payload.get("access_token")
        )
        if not access:
            raise RuntimeError("refresh missing access_token")
        new_refresh = (
            payload.get("authed_user", {}).get("refresh_token")
            or payload.get("refresh_token")
            or current.refresh
        )
        expires_in = payload.get("authed_user", {}).get("expires_in") or payload.get(
            "expires_in"
        )
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if expires_in is not None
            else None
        )
        extra = dict(current.extra)
        if not bound_client_id:
            # A successful legacy refresh proves which currently configured
            # client owns the grant. Persist that pair now so later config or
            # keyring drift cannot break the next refresh. A client_id without
            # a secret deliberately marks a public client.
            extra["client_id"] = client_id
            if client_secret:
                extra["client_secret"] = client_secret
            else:
                extra.pop("client_secret", None)
        return Tokens(
            access=access,
            refresh=new_refresh,
            expires_at=expires_at,
            extra=extra,
        )

    @staticmethod
    def auth_header(tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access}"}
