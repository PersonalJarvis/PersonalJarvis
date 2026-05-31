"""PkceLoopbackHandler — covers Slack.

OAuth 2.1 + PKCE (RFC 7636) with a pre-registered public client_id
shipped in the binary. No client_secret, no DCR. The redirect_uri is
fixed at app-registration time (Slack: `http://localhost:3118/oauth/callback`),
so we open the loopback callback server on that exact port.

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
from urllib.parse import urlencode

import httpx

from jarvis.marketplace.auth.base import (
    AuthSession,
    FlowResult,
    pkce_pair,
    random_state,
    session_id,
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
    # Slack: scope is split into `scope=` (bot) and `user_scope=` (user).
    # PKCE-enabled Slack apps must use `user_scope=` — no bot tokens. So
    # the scope_param_name lets us swap between the two.
    scope_param_name: str = "scope"
    callback_path: str = "/oauth/callback"
    timeout_seconds: int = 10


@dataclass
class _PendingPkceFlow:
    config: PkceLoopbackConfig
    callback_server: OAuthCallbackServer
    code_verifier: str
    redirect_uri: str


class PkceLoopbackHandler:
    """One handler per PKCE-loopback plugin (Slack today)."""

    def __init__(self, config: PkceLoopbackConfig) -> None:
        self.plugin_id = config.plugin_id
        self._config = config
        self._pending: dict[str, _PendingPkceFlow] = {}

    async def start(self, plugin_spec: object) -> AuthSession:
        # Loopback callback server on the fixed port (Slack's 3118).
        state = random_state()
        callback_server = OAuthCallbackServer(
            expected_state=state,
            timeout_seconds=300,
            callback_path=self._config.callback_path,
            port=self._config.callback_port,
        )
        try:
            await callback_server.start()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"could not bind {self._config.plugin_id} callback "
                f"on port {self._config.callback_port}: {exc} "
                "(another app may be using it — close it and retry)"
            ) from exc

        redirect_uri = callback_server.redirect_uri
        verifier, challenge = pkce_pair()
        sid = session_id()

        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self._config.scopes:
            params[self._config.scope_param_name] = ",".join(self._config.scopes)
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
        return Tokens(
            access=access, refresh=refresh, expires_at=expires_at, extra=extra
        )

    async def refresh(self, current: Tokens) -> Tokens:
        if not current.refresh:
            raise RuntimeError("no refresh token stored")
        timeout = httpx.Timeout(self._config.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                self._config.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": current.refresh,
                    "client_id": self._config.client_id,
                },
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
        return Tokens(
            access=access,
            refresh=new_refresh,
            expires_at=expires_at,
            extra=current.extra,
        )

    @staticmethod
    def auth_header(tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access}"}
