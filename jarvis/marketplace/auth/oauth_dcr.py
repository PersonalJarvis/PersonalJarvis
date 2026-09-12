"""HostedMcpDcrHandler — covers Notion + Supabase (and any future hosted
MCP server that supports OAuth 2.1 + Dynamic Client Registration + PKCE).

The flow per RFC 8414 (auth-server discovery) + RFC 7591 (DCR) + RFC 7636 (PKCE):

  1. GET .well-known/oauth-protected-resource  → identifies auth server
  2. GET .well-known/oauth-authorization-server → registration_endpoint, etc
  3. POST registration_endpoint → ephemeral client with negotiated authentication
  4. Build authorize URL with PKCE challenge, open in user's default browser
  5. User logs in → 302 to http://127.0.0.1:<port>/callback?code=…&state=…
  6. POST token_endpoint with code + verifier → access + refresh tokens
  7. Persist via TokenStore

Pitfalls handled:
- Loopback port allocated ONCE upfront — the registration_endpoint sees
  the exact port that the callback server is bound to (avoids the
  port-mismatch trap from openclaw/openclaw#52961).
- DCR registers a fresh client per CONNECT flow, but the issued client_id is
  persisted with the tokens and reused on REFRESH — a refresh_token is bound to
  its issuing client (OAuth 2.0 §6), so refreshing under a re-registered client
  fails with invalid_grant.
- State validated against CSRF on callback; mismatch raises.
- Refresh has per-plugin asyncio.Lock to avoid the 1-2s race window where
  Notion/Supabase issue two valid refresh tokens during rotation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus, urlencode, urlsplit, urlunsplit

import httpx

from jarvis.core.branding import OFFICIAL_REPO_URL, PRODUCT_NAME
from jarvis.marketplace.auth.base import (
    ERROR_DENIED,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    AuthSession,
    FlowResult,
    pkce_pair,
    random_state,
    sanitize_provider_error,
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
    client_name: str = PRODUCT_NAME
    client_uri: str = OFFICIAL_REPO_URL
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
    client_secret: str | None = field(default=None, repr=False)
    token_endpoint_auth_method: str = "none"  # noqa: S105 - OAuth method, not a credential
    resource: str | None = None
    # RFC 7009 endpoint from discovery, persisted with the tokens so a later
    # disconnect can actually end the grant at the provider instead of only
    # deleting our local copy.
    revocation_endpoint: str | None = None


@dataclass(frozen=True)
class _RegisteredClient:
    client_id: str
    client_secret: str | None = field(default=None, repr=False)
    auth_method: str = "none"


def apply_client_auth(
    body: dict[str, str], client_id: str, client_secret: str | None, method: str
) -> httpx.BasicAuth | None:
    """Apply OAuth client authentication without sending credentials twice."""
    if not isinstance(method, str) or method not in {
        "none",
        "client_secret_basic",
        "client_secret_post",
    }:
        raise RuntimeError("Unsupported OAuth client authentication; reconnect required")
    if method != "none" and not client_secret:
        raise RuntimeError("OAuth client secret is missing; reconnect required")
    if method == "client_secret_basic":
        body.pop("client_id", None)
        # RFC 6749 section 2.3.1 requires form-encoding before HTTP Basic.
        return httpx.BasicAuth(quote_plus(client_id), quote_plus(client_secret or ""))
    body["client_id"] = client_id
    if method == "client_secret_post":
        body["client_secret"] = client_secret or ""
    return None


def _well_known_candidates(issuer: str) -> list[str]:
    """Auth-server metadata URLs for an issuer, most-correct first.

    RFC 8414 §3 inserts ``/.well-known/oauth-authorization-server`` BETWEEN the
    host and the issuer's path component. So an issuer WITH a path (Stripe's
    ``https://access.stripe.com/mcp``, Asana's ``mcp.asana.com/v2``) resolves to
    ``https://access.stripe.com/.well-known/oauth-authorization-server/mcp`` —
    NOT ``.../mcp/.well-known/...`` (which 404s and broke Stripe connect).
    Path-less issuers (Notion, Linear) are identical either way. We also try the
    OIDC variant and the legacy append form as fallbacks for servers that only
    serve there.
    """
    parts = urlsplit(issuer.rstrip("/"))
    path = parts.path  # "/mcp" or ""
    out: list[str] = []

    def _add(url: str) -> None:
        if url not in out:
            out.append(url)

    # 1. RFC 8414 — well-known inserted between host and the issuer path.
    _add(
        urlunsplit(
            (parts.scheme, parts.netloc, "/.well-known/oauth-authorization-server" + path, "", "")
        )
    )
    # 2. OpenID-Connect discovery insert variant.
    _add(
        urlunsplit((parts.scheme, parts.netloc, "/.well-known/openid-configuration" + path, "", ""))
    )
    # 3. Legacy append form (some non-compliant servers serve only here).
    _add(issuer.rstrip("/") + "/.well-known/oauth-authorization-server")
    return out


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

    async def _discover(self, client: httpx.AsyncClient) -> dict:
        """Returns the auth-server metadata dict. Fields used downstream:
        authorization_endpoint, token_endpoint, registration_endpoint.

        Side effect: stashes ``self._discovered_resource`` (the protected-resource
        canonical URI, RFC 9728) so ``start()``/``_exchange`` can send the RFC 8707
        ``resource`` parameter — Stripe's MCP authorize silently drops you on the
        dashboard (no consent) when it is missing."""
        self._discovered_resource: str | None = None
        self._resource_scopes: list[str] | None = None
        # Step 1: protected-resource → tells us which auth server to ask.
        try:
            r = await client.get(self._config.discovery_url)
            r.raise_for_status()
            pr_meta = r.json()
        except httpx.HTTPError as exc:
            if not self._config.fallback_authorization_endpoint:
                raise RuntimeError(f"protected-resource discovery failed: {exc}") from exc
            # Fallback: use catalog-provided endpoints; skip discovery.
            log.warning(
                "%s: discovery unreachable, using fallback endpoints",
                self.plugin_id,
            )
            return {
                "authorization_endpoint": self._config.fallback_authorization_endpoint,
                "token_endpoint": self._config.fallback_token_endpoint or "",
                "registration_endpoint": (self._config.fallback_registration_endpoint or ""),
            }

        auth_servers = pr_meta.get("authorization_servers") or []
        if not auth_servers:
            raise RuntimeError(
                "protected-resource has no authorization_servers "
                f"(discovery: {sanitize_provider_error(str(pr_meta))})"
            )
        # RFC 9728 resource indicator — passed as the RFC 8707 `resource` param.
        self._discovered_resource = pr_meta.get("resource") or None
        resource_scopes = pr_meta.get("scopes_supported")
        if isinstance(resource_scopes, list) and all(isinstance(s, str) for s in resource_scopes):
            self._resource_scopes = resource_scopes

        # Step 2: fetch the auth-server metadata. RFC 8414 puts the well-known
        # path BETWEEN host and the issuer's path, so an issuer with a path
        # (Stripe's .../mcp, Asana's .../v2) is NOT at issuer + "/.well-known/...".
        # Try the candidates in order until one returns usable metadata.
        last_status: int | None = None
        for as_url in _well_known_candidates(auth_servers[0]):
            r2 = await client.get(as_url)
            if r2.status_code == 200:
                try:
                    cand = r2.json()
                except ValueError:
                    last_status = r2.status_code
                    continue
                if "authorization_endpoint" in cand and "token_endpoint" in cand:
                    return cand
            last_status = r2.status_code
        raise RuntimeError(
            f"auth-server metadata discovery failed for {auth_servers[0]!r} "
            f"(last HTTP {last_status})"
        )

    # ------------------------------------------------------------------
    # DCR — RFC 7591
    # ------------------------------------------------------------------

    async def _register(
        self,
        client: httpx.AsyncClient,
        registration_endpoint: str,
        redirect_uri: str,
        auth_method: str = "none",
    ) -> _RegisteredClient:
        """Return credentials only to the private per-flow state."""
        body = {
            "client_name": self._config.client_name,
            "client_uri": self._config.client_uri,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": auth_method,
        }
        r = await client.post(registration_endpoint, json=body)
        if r.status_code >= 400:
            raise RuntimeError(
                f"DCR failed (HTTP {r.status_code}): {sanitize_provider_error(r.text)}"
            )
        meta = r.json()
        cid = meta.get("client_id")
        if not cid:
            raise RuntimeError("DCR response missing client_id")
        assigned_method = meta.get("token_endpoint_auth_method", auth_method)
        secret = meta.get("client_secret") or None
        if not isinstance(cid, str) or (secret is not None and not isinstance(secret, str)):
            raise RuntimeError("DCR returned invalid client credentials")
        # Validate before launching browser consent, without exposing provider metadata.
        apply_client_auth({}, cid, secret, assigned_method)
        return _RegisteredClient(
            cid, secret if assigned_method != "none" else None, assigned_method
        )

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
        # 15-min window: a real user logging into the provider can need to do a
        # CAPTCHA + 2FA + account selection + consent — 5 min was too tight and
        # the loopback callback server died before they finished (the redirect
        # then hit a dead port and never came back).
        callback_server = make_callback_server(
            random_state(),
            timeout_seconds=900,
        )
        await callback_server.start()
        redirect_uri = callback_server.redirect_uri

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                meta = await self._discover(client)
                registration_endpoint = meta.get("registration_endpoint")
                if not registration_endpoint:
                    # Fallback: some hosted MCPs publish DCR via a separate URL
                    # (e.g. baked into our catalog as a fallback override).
                    registration_endpoint = self._config.fallback_registration_endpoint
                if not registration_endpoint:
                    raise RuntimeError(f"{self.plugin_id}: no registration_endpoint discovered")
                supported = meta.get("token_endpoint_auth_methods_supported", ["none"])
                auth_method = next(
                    (
                        method
                        for method in ("none", "client_secret_basic", "client_secret_post")
                        if isinstance(supported, list) and method in supported
                    ),
                    None,
                )
                if auth_method is None:
                    raise RuntimeError(
                        "Provider has no supported OAuth client authentication method"
                    )
                registered = await self._register(
                    client, registration_endpoint, redirect_uri, auth_method
                )
                client_id = registered.client_id
        except BaseException:
            await callback_server.stop()
            raise

        verifier, challenge = pkce_pair()
        sid = session_id()
        configured_scopes = getattr(getattr(plugin_spec, "auth", None), "scopes", None)
        scopes = (
            " ".join(configured_scopes)
            if configured_scopes is not None
            else self._scopes_from_meta(meta)
        )
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
        resource = getattr(self, "_discovered_resource", None)
        if resource:
            # RFC 8707 resource indicator — MANDATORY for MCP auth servers.
            # Without it Stripe's authorize skips consent and bounces the user
            # to their dashboard instead of redirecting back to the loopback.
            params["resource"] = resource
        params["prompt"] = "consent"  # always show consent on first connect
        authorize_url = meta["authorization_endpoint"] + "?" + urlencode(params, doseq=True)

        # Park the per-flow state until callback fires.
        self._pending[sid] = _PendingFlow(
            config=self._config,
            callback_server=callback_server,
            code_verifier=verifier,
            state=callback_server._expected_state,  # noqa: SLF001
            redirect_uri=redirect_uri,
            token_endpoint=meta["token_endpoint"],
            client_id=client_id,
            client_secret=registered.client_secret,
            token_endpoint_auth_method=registered.auth_method,
            resource=resource,
            revocation_endpoint=meta.get("revocation_endpoint") or None,
        )

        return AuthSession(
            flow_id=sid,
            plugin_id=self.plugin_id,
            kind="browser_redirect",
            open_url=authorize_url,
            expires_at_ms=int((datetime.now(UTC) + timedelta(minutes=15)).timestamp() * 1000),
        )

    def _scopes_from_meta(self, meta: Mapping[str, object]) -> str:
        # An issuer can serve many applications. Its scopes include unrelated
        # billing/admin rights; the MCP resource advertises its own scope set.
        scopes = getattr(self, "_resource_scopes", None)
        if scopes is None:
            scopes = meta.get("scopes_supported")
        if isinstance(scopes, list) and scopes:
            return " ".join(str(s) for s in scopes)
        return ""

    async def await_completion(self, session: AuthSession) -> FlowResult:
        pending = self._pending.get(session.flow_id)
        if pending is None:
            return FlowResult(tokens=None, error="unknown flow_id", error_code=ERROR_UNKNOWN)

        try:
            result = await pending.callback_server.await_callback()
        except CallbackTimeoutError:
            await pending.callback_server.stop()
            self._pending.pop(session.flow_id, None)
            return FlowResult(
                tokens=None,
                error="user did not approve in time",
                error_code=ERROR_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            await pending.callback_server.stop()
            self._pending.pop(session.flow_id, None)
            message = str(exc).lower()
            code = (
                ERROR_DENIED
                if ("access_denied" in message or "denied" in message)
                else ERROR_UNKNOWN
            )
            return FlowResult(
                tokens=None,
                error=f"callback error: {sanitize_provider_error(str(exc))}",
                error_code=code,
            )
        finally:
            # Stop the listener — code is captured.
            await pending.callback_server.stop()

        # Token exchange.
        try:
            tokens = await self._exchange(pending, code=result.code)
        except RuntimeError as exc:
            self._pending.pop(session.flow_id, None)
            message = str(exc).lower()
            code = (
                ERROR_DENIED
                if ("access_denied" in message or "denied" in message)
                else ERROR_UNKNOWN
            )
            return FlowResult(tokens=None, error=str(exc), error_code=code)
        self._pending.pop(session.flow_id, None)
        return FlowResult(tokens=tokens, error=None)

    async def cancel(self, session: AuthSession) -> None:
        """Stop the pending callback listener so a cancelled dialog leaks no
        socket and a late provider callback cannot complete the flow."""
        pending = self._pending.pop(session.flow_id, None)
        if pending is not None:
            try:
                await pending.callback_server.stop()
            except Exception:  # noqa: BLE001 — cancel stays best-effort
                log.debug("dcr cancel stop failed for %s", self.plugin_id)

    async def _exchange(self, pending: _PendingFlow, *, code: str) -> Tokens:
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": pending.client_id,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.code_verifier,
        }
        if pending.resource:
            body["resource"] = pending.resource
        auth = apply_client_auth(
            body, pending.client_id, pending.client_secret, pending.token_endpoint_auth_method
        )
        timeout = httpx.Timeout(pending.config.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                pending.token_endpoint,
                data=body,
                auth=auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if r.status_code != 200:
            raise RuntimeError(
                f"token exchange HTTP {r.status_code}: {sanitize_provider_error(r.text)}"
            )
        payload = r.json()
        access = payload.get("access_token")
        if not access:
            raise RuntimeError("token response missing access_token")
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
        # Persist the client this grant was issued to + the token endpoint.
        # A refresh_token is bound to its issuing client_id (OAuth 2.0 §6), so
        # the refresh path MUST present the SAME client_id — re-registering a
        # fresh DCR client here would get rejected with invalid_grant and the
        # scheduler would delete the (still-valid) token. See refresh().
        extra["client_id"] = pending.client_id
        extra["token_endpoint_auth_method"] = pending.token_endpoint_auth_method
        if pending.client_secret:
            extra["client_secret"] = pending.client_secret
        extra["token_endpoint"] = pending.token_endpoint
        if pending.resource:
            extra["resource"] = pending.resource
        if pending.revocation_endpoint:
            # A DCR client is ephemeral, so its revocation endpoint is only
            # knowable from the discovery document used at connect time.
            extra["revocation_endpoint"] = pending.revocation_endpoint
        return Tokens(
            access=access,
            refresh=refresh,
            expires_at=expires_at,
            extra=extra,
        )

    async def refresh(self, current: Tokens) -> Tokens:
        if not current.refresh:
            raise RuntimeError("no refresh token stored")
        # A refresh_token is bound to the client_id that obtained it (OAuth 2.0
        # §6). We persisted that client_id (+ token endpoint) at connect time —
        # reuse it verbatim. Re-registering a fresh DCR client here was the old
        # bug: the auth server rejects the mismatched client with invalid_grant,
        # the scheduler reads that as "revoked", and the keyring entry is deleted
        # — which is why browser-OAuth plugins (Linear, Notion) silently
        # disconnected after a restart while static PAT plugins survived.
        client_id = current.extra.get("client_id")
        token_endpoint = current.extra.get("token_endpoint")
        if not client_id:
            # Token minted before client_id was persisted (pre-fix connect).
            # We can't refresh it without the original client, but we must NOT
            # signal "revoked" — that would make the scheduler delete a token
            # that may still be valid. Fail soft so the entry is kept; the user
            # reconnects once and the new token carries its client_id forever.
            raise RuntimeError(
                "refresh: no stored client_id — reconnect required to heal this connection"
            )
        timeout = httpx.Timeout(self._config.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            if not token_endpoint:
                # Legacy token without a stored endpoint — rediscover it.
                meta = await self._discover(client)
                token_endpoint = meta["token_endpoint"]
            refresh_body = {
                "grant_type": "refresh_token",
                "refresh_token": current.refresh,
                "client_id": client_id,
            }
            if current.extra.get("resource"):
                refresh_body["resource"] = current.extra["resource"]
            auth = apply_client_auth(
                refresh_body,
                client_id,
                current.extra.get("client_secret"),
                current.extra.get("token_endpoint_auth_method", "none"),
            )
            r = await client.post(
                token_endpoint,
                data=refresh_body,
                auth=auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if r.status_code == 400 and "invalid_grant" in r.text:
            raise RuntimeError("revoked")
        if r.status_code != 200:
            raise RuntimeError(f"refresh HTTP {r.status_code}: {sanitize_provider_error(r.text)}")
        payload = r.json()
        new_access = payload.get("access_token")
        if not new_access:
            raise RuntimeError("refresh missing access_token")
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
