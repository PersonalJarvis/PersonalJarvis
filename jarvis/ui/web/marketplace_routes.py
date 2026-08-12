"""REST API for the Plugin Marketplace.

Endpoints:
    GET    /api/marketplace/plugins                       — catalog + status
    POST   /api/marketplace/plugins/{id}/connect/pat       — paste-token (Vercel, Supabase fallback)
    POST   /api/marketplace/plugins/{id}/connect/start     — kick off OAuth redirect flow
    GET    /api/marketplace/plugins/{id}/connect/poll/{flow_id} — poll until completion
    DELETE /api/marketplace/plugins/{id}                   — disconnect
    GET    /api/marketplace/community                      — community index browse
    POST   /api/marketplace/community/refresh              — force index re-fetch
    POST   /api/marketplace/community/plugins/{id}/install — one-click install
    DELETE /api/marketplace/community/plugins/{id}         — uninstall + revoke
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from jarvis.core.process_utils import resolve_executable
from jarvis.marketplace.auth import (
    DcrConfig,
    DeviceFlowConfig,
    DeviceFlowHandler,
    FlowResult,
    HostedMcpDcrHandler,
    PkceLoopbackConfig,
    PkceLoopbackHandler,
    get_registry,
)
from jarvis.marketplace.catalog import (
    CATEGORY_ORDER,
    HostedMcpOAuthDcrAuth,
    OAuthDeviceFlowAuth,
    OAuthPkceLoopbackAuth,
    PatPasteAuth,
)
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.channel_runtime import apply_channel_live
from jarvis.marketplace.discord_connect import (
    on_discord_connected,
    on_discord_disconnected,
)
from jarvis.marketplace.instance_url import InstanceUrlError, normalize_instance_url
from jarvis.marketplace.revoke import revoke_tokens
from jarvis.marketplace.telegram_connect import (
    on_telegram_connected,
    on_telegram_disconnected,
)
from jarvis.marketplace.token_store import Tokens, TokenStore

# Marketplace plugin ids whose "connect" enables an in-repo bidirectional chat
# channel (token + config), not just a stored token. Kept in sync with the
# channel adapters under jarvis/channels/.
_CHANNEL_PLUGIN_IDS = ("telegram", "discord")

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


def _refresh_plugin_in_live_registry(plugin_id: str) -> None:
    """Best-effort: re-expand the live brain after a connect/disconnect.

    No-op when no shared registry is published (headless without web boot).
    """
    try:
        from jarvis.marketplace.plugin_shared import get_active_plugin_registry

        reg = get_active_plugin_registry()
        if reg is not None:
            asyncio.create_task(reg.refresh_plugin(plugin_id), name=f"plugin-refresh:{plugin_id}")
    except Exception:  # noqa: BLE001
        # A failed re-expand after the user just connected a plugin is a
        # recoverable workflow failure, not a hot-path event — log at WARNING
        # so it surfaces without a debug flag.
        log.warning("live plugin refresh failed for %s", plugin_id, exc_info=True)


def _live_plugin_registry() -> Any:
    """The active ``PluginToolRegistry``, or ``None`` if unreachable.

    Same lookup as ``_refresh_plugin_in_live_registry`` — returns ``None`` on
    any failure (early boot, headless) so callers can fail open.
    """
    try:
        from jarvis.marketplace.plugin_shared import get_active_plugin_registry

        return get_active_plugin_registry()
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PluginStatusMeta:
    """Everything the plugins view needs about one grant, from ONE token load."""

    status: str
    expires_at: str | None = None
    last_refreshed: str | None = None
    # Why the connection is flagged, and since when. Both stay ``None`` on a
    # healthy grant and on one flagged before these fields existed — the view
    # then says "unknown" rather than inventing a cause.
    reauth_reason: str | None = None
    reauth_at: str | None = None


def _plugin_status_meta(plugin_id: str, store: TokenStore) -> _PluginStatusMeta:
    """Return the plugin's stored auth state from ONE token load.

    ``expires_at`` / ``last_refreshed`` are surfaced so the UI can show an honest
    "auto-refreshing / expiring soon" hint without re-deriving it. Both are
    ``None`` for a token that carries no expiry (e.g. a PAT) or was never
    refreshed — never a fabricated timestamp.

    ``reauth_reason`` is one of the ``REAUTH_*`` codes and is the ONLY failure
    detail that leaves this layer: a provider's raw error body can echo a token
    back, so it is never stored and never served.
    """
    try:
        tokens = store.load(plugin_id)
    except RuntimeError:
        return _PluginStatusMeta("error")
    if tokens is None:
        return _PluginStatusMeta("not_connected")
    return _PluginStatusMeta(
        status="needs_reauth" if tokens.needs_reauth else "connected",
        expires_at=tokens.expires_at.isoformat() if tokens.expires_at else None,
        last_refreshed=tokens.extra.get("last_refreshed") or None,
        reauth_reason=tokens.reauth_reason if tokens.needs_reauth else None,
        reauth_at=(
            tokens.reauth_at.isoformat() if tokens.needs_reauth and tokens.reauth_at else None
        ),
    )


def _plugin_status(plugin_id: str, store: TokenStore) -> str:
    return _plugin_status_meta(plugin_id, store).status


def _build_dcr_handler(plugin_id: str, auth: HostedMcpOAuthDcrAuth) -> HostedMcpDcrHandler:
    return HostedMcpDcrHandler(
        DcrConfig(
            plugin_id=plugin_id,
            discovery_url=auth.discovery_url,
        )
    )


def _make_validator(transport: httpx.AsyncBaseTransport | None = None):
    """Build a token validator that branches on the catalog's ``auth_scheme``.

    Returns an async callable ``(auth, token) -> (ok: bool, status: int)``.
    ``transport`` is injectable so unit tests can stub the HTTP layer.
    Raises ``httpx.HTTPError`` to the caller when the endpoint is unreachable.
    """

    async def _validate(
        auth: PatPasteAuth, token: str, instance_url: str | None = None
    ) -> tuple[bool, int]:
        scheme = getattr(auth, "auth_scheme", "bearer")
        headers = {"User-Agent": "Personal-Jarvis/1.0"}
        if scheme == "telegram_path":
            # Telegram puts the token in the URL path, no auth header.
            url = auth.validation_endpoint.replace("{token}", token)
        elif scheme == "bot":
            url = auth.validation_endpoint
            headers["Authorization"] = f"Bot {token}"
        else:  # bearer
            url = auth.validation_endpoint
            headers["Authorization"] = f"Bearer {token}"
        if auth.instance_url is not None and instance_url:
            # Self-hosted: the catalog cannot know the address, so validation
            # goes to the user's own server instead of a fixed endpoint.
            url = normalize_instance_url(instance_url) + auth.instance_url.validation_path
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return False, resp.status_code
        if scheme == "telegram_path":
            # Telegram returns 200 with {"ok": false} for soft errors.
            try:
                return bool(resp.json().get("ok")), 200
            except ValueError:
                return False, 200
        return True, 200

    return _validate


_validate_token = _make_validator()


# ----------------------------------------------------------------------
# Read endpoints
# ----------------------------------------------------------------------


def _mcp_live(
    mcp: dict[str, Any], *, plugin_id: str = "", status: str = ""
) -> tuple[bool, str | None]:
    """``(is_live, runtime_missing)`` for an MCP plugin's transport.

    M2 (honest status): a stdio plugin (GitHub=docker, Supabase=npx) is only LIVE if
    its launcher binary is on PATH — else connecting saves the token but the tools
    never appear, so a green "Connected · Live" badge is a lie. ``runtime_missing``
    is the absent launcher name for an honest UI hint.

    Bug 14: an http plugin's connect-time ``list_tools()`` can 401 and be
    swallowed, leaving status "connected" with ZERO live tools — a green
    "Connected · Live" badge over a dead session. When a BOOTSTRAPPED live
    registry is reachable AND the plugin is "connected" AND it reports zero
    tools, the session is dead (expired token, 401 at list_tools) and the badge
    goes honest. No registry reachable (headless), or a registry that is
    published but still bootstrapping in the background (the boot window where
    every count reads 0 although nothing is dead) -> fail open, exactly
    today's behaviour.
    """
    transport = str(mcp.get("transport", "")).lower()
    if transport == "http":
        if status == "connected":
            reg = _live_plugin_registry()  # the same accessor refresh uses
            if reg is not None and reg.is_bootstrapped() and reg.live_tool_count(plugin_id) == 0:
                hint = reg.last_connect_error(plugin_id) or "no tools loaded — reconnect"
                return False, hint
        return True, None
    if transport == "stdio":
        install = mcp.get("install") or []
        launcher = str(install[0]) if install else ""
        if launcher:
            resolved = resolve_executable(launcher)
            if resolved != launcher or Path(resolved).is_file():
                return True, None
        return False, (launcher or None)
    return False, None


@router.get("/plugins")
async def list_plugins(response: Response) -> dict[str, Any]:
    # Never let an embedded webview (pywebview/WebView2) serve a stale cached
    # plugin list: WebView2 heuristically caches this GET, so after a catalog
    # change the desktop window kept showing the old/empty list while a fresh
    # browser tab showed the new one. no-store forces every fetch to hit the
    # server. (Bug: "plugins disappear / don't show in the desktop app".)
    response.headers["Cache-Control"] = "no-store"
    catalog = load_catalog()
    store = TokenStore()
    enriched: list[dict[str, Any]] = []
    connected = 0
    for spec in catalog.plugins:
        item = spec.model_dump(mode="json")
        meta = _plugin_status_meta(spec.id, store)
        status = meta.status
        item["status"] = status
        item["expires_at"] = meta.expires_at
        item["last_refreshed"] = meta.last_refreshed
        item["reauth_reason"] = meta.reauth_reason
        item["reauth_at"] = meta.reauth_at
        if isinstance(spec.auth, OAuthPkceLoopbackAuth):
            from jarvis.marketplace.connect_helpers import (
                is_placeholder_client_id,
                resolve_pkce_client,
            )

            effective_client_id, _ = resolve_pkce_client(
                spec.id, spec.auth.client_id, spec.auth.client_secret
            )
            # Configuration state is safe to expose; the client id and secret
            # themselves never leave the backend. The dialog uses this to stop
            # placeholder-only installs before they launch a doomed OAuth tab.
            item["oauth_client_configured"] = not is_placeholder_client_id(
                effective_client_id
            )
        mcp = spec.mcp_server or {}
        mcp_live, runtime_missing = _mcp_live(mcp, plugin_id=spec.id, status=status)
        if runtime_missing:
            item["runtime_missing"] = runtime_missing
        native_live = False
        if spec.native_tool:
            try:
                from jarvis.brain.factory import ROUTER_TOOLS

                native_live = spec.native_tool in ROUTER_TOOLS
            except Exception:  # noqa: BLE001
                native_live = False
        item["live_callable"] = mcp_live or native_live
        if status == "connected":
            connected += 1
        enriched.append(item)
    return {
        "version": catalog.version,
        "schema_version": catalog.schema_version,
        # Section order for the store. Served rather than hardcoded in the UI so
        # a new category is a backend-only change; the frontend appends any
        # category it receives that is not listed here instead of dropping (or
        # crashing on) it.
        "category_order": list(CATEGORY_ORDER),
        "plugins": enriched,
        "total": len(enriched),
        "connected": connected,
    }


# ----------------------------------------------------------------------
# PAT-paste connect (Vercel, Supabase fallback)
# ----------------------------------------------------------------------


class PatConnectBody(BaseModel):
    token: str = Field(min_length=1, max_length=2048)
    # Base address of the user's own server, for self-hosted plugins whose
    # catalog entry declares an `instance_url`. Not a secret.
    instance_url: str | None = Field(default=None, max_length=512)
    # Owner lock for channel plugins (telegram/discord): the numeric user id the
    # bot will obey. When given, it is added to the allowlist and
    # trust-on-first-contact is turned off. Not a secret — lives in jarvis.toml.
    allowed_user_id: int | None = Field(default=None, ge=0)


@router.post("/plugins/{plugin_id}/connect/pat")
async def connect_pat(plugin_id: str, body: PatConnectBody, request: Request) -> dict[str, Any]:
    catalog = load_catalog()
    spec = catalog.by_id(plugin_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"plugin {plugin_id!r} not in catalog")

    if not isinstance(spec.auth, PatPasteAuth):
        raise HTTPException(
            status_code=400,
            detail=(
                f"plugin {plugin_id!r} uses auth mode {spec.auth.mode!r}, "
                "not 'pat_paste' — use the matching connect endpoint instead"
            ),
        )

    token = body.token.strip()
    if spec.auth.token_prefix and not token.startswith(spec.auth.token_prefix):
        raise HTTPException(
            status_code=400,
            detail=f"token must start with '{spec.auth.token_prefix}_' "
            f"(got first 4 chars: {token[:4]!r})",
        )

    # Self-hosted services (Home Assistant, Jellyfin, ...) live at the user's
    # own address, which the catalog cannot know.
    instance_url: str | None = None
    if spec.auth.instance_url is not None:
        if not (body.instance_url or "").strip():
            raise HTTPException(
                status_code=400,
                detail=f"{spec.display_name} needs the address of your own server",
            )
        try:
            instance_url = normalize_instance_url(body.instance_url or "")
        except InstanceUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    target = (
        instance_url + spec.auth.instance_url.validation_path
        if instance_url and spec.auth.instance_url
        else spec.auth.validation_endpoint
    )
    try:
        # Only widen the call for self-hosted plugins. Every other caller (and
        # every injected test double) keeps the two-argument shape it has had
        # since the flow was written.
        ok, status = (
            await _validate_token(spec.auth, token, instance_url)
            if instance_url
            else await _validate_token(spec.auth, token)
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"could not reach {target}: {type(exc).__name__}",
        ) from exc

    if not ok:
        raise HTTPException(
            status_code=401,
            detail=f"{spec.display_name} rejected the token (HTTP {status})",
        )

    store = TokenStore()
    # The address rides in `extra` beside the token: it is per-connection state,
    # it must survive a restart exactly like the credential, and a tool that has
    # the token but not the address can do nothing with it.
    extra = {"instance_url": instance_url} if instance_url else {}
    store.save(plugin_id, Tokens(access=token, extra=extra))
    if plugin_id in _CHANNEL_PLUGIN_IDS:
        # A channel "connect" enables the in-repo bidirectional channel. Do not
        # report a successful Marketplace connect if the canonical channel
        # secret/config could not be written; otherwise the UI says "connected"
        # while the bot cannot start.
        try:
            if plugin_id == "telegram":
                on_telegram_connected(token, body.allowed_user_id)
            else:
                on_discord_connected(token, body.allowed_user_id)
        except Exception as exc:  # noqa: BLE001
            try:
                store.delete(plugin_id)
            except Exception as cleanup_exc:  # noqa: BLE001
                log.debug(
                    "%s token cleanup after failed enable failed: %s",
                    plugin_id,
                    cleanup_exc,
                )
            log.warning("%s channel enable failed: %s", plugin_id, exc)
            raise HTTPException(
                status_code=500,
                detail=f"{plugin_id}-channel-enable-failed: {type(exc).__name__}",
            ) from exc
    _refresh_plugin_in_live_registry(plugin_id)
    live_applied = False
    if plugin_id in _CHANNEL_PLUGIN_IDS:
        live_applied = await apply_channel_live(request.app.state, plugin_id)
    result: dict[str, Any] = {
        "ok": True,
        "plugin_id": plugin_id,
        "status": "connected",
        "live_applied": live_applied,
    }
    if plugin_id in _CHANNEL_PLUGIN_IDS and not live_applied:
        # M8 (honest status): the token saved but the channel did NOT start live —
        # commonly the optional extra is missing (e.g. discord.py for Discord) or an
        # app restart is needed. Surface it so the UI never implies a working message
        # path with a green "Live" badge.
        result["live_note"] = (
            "Saved, but the channel did not start live. It may need its optional "
            "extra (pip install '.[channels]' for Discord) or an app restart."
        )
    return result


# ----------------------------------------------------------------------
# OAuth redirect connect (Notion, Supabase main path)
# ----------------------------------------------------------------------


@router.post("/plugins/{plugin_id}/connect/start")
async def connect_start(plugin_id: str, background: BackgroundTasks) -> dict[str, Any]:
    """Kick off an OAuth-redirect flow. Returns a session the UI renders.

    The handler runs `await_completion()` in a background task — the UI
    long-polls `/connect/poll/{flow_id}` until tokens are ready.
    """
    catalog = load_catalog()
    spec = catalog.by_id(plugin_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"plugin {plugin_id!r} not in catalog")

    if isinstance(spec.auth, HostedMcpOAuthDcrAuth):
        handler = _build_dcr_handler(plugin_id, spec.auth)
    elif isinstance(spec.auth, OAuthDeviceFlowAuth):
        from jarvis.marketplace.connect_helpers import is_placeholder_client_id

        if is_placeholder_client_id(spec.auth.client_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"oauth client not configured for plugin {plugin_id!r}: "
                    "placeholder client_id in the catalog. Supply your own "
                    "OAuth client first."
                ),
            )
        handler = DeviceFlowHandler(
            DeviceFlowConfig(
                plugin_id=plugin_id,
                device_url=spec.auth.device_url,
                verify_url=spec.auth.verify_url,
                token_url=spec.auth.token_url,
                client_id=spec.auth.client_id,
                scopes=list(spec.auth.scopes),
            )
        )
    elif isinstance(spec.auth, OAuthPkceLoopbackAuth):
        # Resolve the effective client from secrets so a reconnect uses the
        # operator's real Google client, not the catalog placeholder (the same
        # resolution the refresh scheduler uses — connect/refresh stay in sync).
        from jarvis.marketplace.connect_helpers import (
            is_placeholder_client_id,
            resolve_pkce_client,
        )

        _pkce_client_id, _pkce_client_secret = resolve_pkce_client(
            plugin_id, spec.auth.client_id, spec.auth.client_secret
        )
        if is_placeholder_client_id(_pkce_client_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"oauth client not configured for plugin {plugin_id!r}: the "
                    "shipped catalog carries a placeholder client_id and no "
                    "<family>_oauth_client_id secret is set. Open the connect "
                    "dialog's 'Use your own OAuth client' section (or follow "
                    "the plugin's setup hint) and paste your own client id — "
                    "then retry."
                ),
            )
        handler = PkceLoopbackHandler(
            PkceLoopbackConfig(
                plugin_id=plugin_id,
                authorization_url=spec.auth.authorization_url,
                token_url=spec.auth.token_url,
                client_id=_pkce_client_id,
                client_secret=_pkce_client_secret,
                callback_port=spec.auth.callback_port or 0,
                scopes=list(spec.auth.scopes),
                scope_separator=spec.auth.scope_separator,
                # Slack-specific: PKCE-enabled apps must use user_scope= per
                # docs.slack.dev/authentication/using-pkce. When the catalog
                # marks a plugin user-scopes-only, route the param.
                scope_param_name=("user_scope" if spec.auth.user_scopes_only else "scope"),
                callback_path=spec.auth.callback_path,
                resource=spec.auth.resource,
                offline_access=spec.auth.offline_access,
            )
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"plugin {plugin_id!r} uses auth mode {spec.auth.mode!r} "
                "which is not yet wired to /connect/start. Supported: "
                "hosted_mcp_oauth_dcr, oauth_device_flow, oauth_pkce_loopback."
            ),
        )

    try:
        session = await handler.start(spec)
    except Exception as exc:  # noqa: BLE001
        log.warning("plugin %s connect/start failed: %s", plugin_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"connect-start failed: {exc}",
        ) from exc

    registry = get_registry()
    registry.put(handler, session)

    # Drive the await-completion in a background task; the result is
    # parked on the registry slot for the poll endpoint to read.
    async def _drive() -> None:
        slot = registry.get(session.flow_id)
        if slot is None:
            return
        async with slot.completion_lock:
            try:
                result = await handler.await_completion(session)
            except Exception as exc:  # noqa: BLE001
                log.warning("plugin %s connect/await failed: %s", plugin_id, exc)
                if registry.get(session.flow_id) is slot:
                    slot.result = FlowResult(tokens=None, error=str(exc))
                return
            # Closing the browser-login dialog is a real cancellation, not
            # merely a UI hide. Never let a late provider callback replace the
            # existing grant (especially a needs_reauth grant) after the user
            # cancelled the reconnect.
            if registry.get(session.flow_id) is not slot:
                log.info("plugin %s connect flow cancelled", plugin_id)
                return
            if result.tokens is not None:
                # Persist BEFORE publishing the result: connect_poll reads
                # `slot.result` to decide "connected" vs "pending", so setting
                # it before the save actually lands let a poll (or a crash
                # right after) report success for a token that was never
                # written to the store.
                try:
                    TokenStore().save(plugin_id, result.tokens)
                except Exception as exc:  # noqa: BLE001
                    log.warning("plugin %s token save failed: %s", plugin_id, exc)
                    slot.result = FlowResult(tokens=None, error=f"token save failed: {exc}")
                    return
                _refresh_plugin_in_live_registry(plugin_id)
                log.info("plugin %s connected via DCR", plugin_id)
            slot.result = result

    asyncio.create_task(_drive(), name=f"oauth-drive:{plugin_id}:{session.flow_id}")

    return {
        "ok": True,
        "flow_id": session.flow_id,
        "plugin_id": session.plugin_id,
        "kind": session.kind,
        "open_url": session.open_url,
        "user_code": session.user_code,
        "verification_uri": session.verification_uri,
        "verification_uri_complete": session.verification_uri_complete,
        "expires_at_ms": session.expires_at_ms,
        "interval": session.interval,
    }


@router.delete("/plugins/{plugin_id}/connect/{flow_id}")
async def cancel_connect(plugin_id: str, flow_id: str) -> dict[str, str]:
    """Cancel a pending OAuth flow without touching the stored grant."""
    registry = get_registry()
    slot = registry.get(flow_id)
    if slot is not None and slot.session.plugin_id != plugin_id:
        raise HTTPException(status_code=404, detail="unknown flow_id for plugin")
    registry.drop(flow_id)
    return {"state": "cancelled", "flow_id": flow_id, "plugin_id": plugin_id}


@router.get("/plugins/{plugin_id}/connect/poll/{flow_id}")
async def connect_poll(plugin_id: str, flow_id: str) -> dict[str, Any]:
    """Returns `{state: "pending"|"connected"|"error", ...}`."""
    registry = get_registry()
    slot = registry.get(flow_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="unknown flow_id (or expired)")

    if slot.result is None:
        return {"state": "pending", "flow_id": flow_id}

    if slot.result.error or slot.result.tokens is None:
        registry.drop(flow_id)
        return {
            "state": "error",
            "flow_id": flow_id,
            "error": slot.result.error or "unknown",
        }

    registry.drop(flow_id)
    return {"state": "connected", "flow_id": flow_id, "plugin_id": plugin_id}


# ----------------------------------------------------------------------
# Hosted OAuth callback (headless / VPS — public redirect target)
# ----------------------------------------------------------------------


@router.get("/oauth/callback", response_model=None)
async def oauth_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    """Public redirect target for hosted-mode OAuth flows.

    The provider redirects the user's browser here with ``?code=&state=``. We
    hand the captured pair to the waiting flow — matched by ``state``, which is
    the CSRF check — and render a close-this-tab page. Active only when
    ``[marketplace].public_callback_base_url`` is set; desktop installs use the
    loopback callback server instead.
    """
    from jarvis.marketplace.hosted_callback import (
        ERROR_HTML,
        SUCCESS_HTML,
        deliver_callback,
    )

    delivered = deliver_callback(code=code, state=state, error=error or None)
    if not delivered:
        return HTMLResponse(
            ERROR_HTML.format(reason="Unknown or expired authorization state."),
            status_code=400,
        )
    if error:
        return HTMLResponse(ERROR_HTML.format(reason=error), status_code=400)
    if not code:
        return HTMLResponse(
            ERROR_HTML.format(reason="Missing authorization code."),
            status_code=400,
        )
    return HTMLResponse(SUCCESS_HTML)


# ----------------------------------------------------------------------
# Disconnect
# ----------------------------------------------------------------------


@router.delete("/plugins/{plugin_id}")
async def disconnect(plugin_id: str, request: Request) -> dict[str, Any]:
    catalog = load_catalog()
    spec = catalog.by_id(plugin_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"plugin {plugin_id!r} not in catalog")

    # Tell the provider to drop the grant BEFORE the local token is gone —
    # afterwards there is nothing left to revoke with. Deleting the local
    # credential is what the user asked for and must always happen, so a
    # provider that is unreachable or does not implement revocation only
    # changes what we report, never whether the disconnect succeeds.
    store = TokenStore()
    revocation = "unsupported"
    try:
        tokens = store.load(plugin_id)
        if tokens is not None:
            revocation = await revoke_tokens(spec, tokens)
    except Exception as exc:  # noqa: BLE001 - never block the disconnect
        log.info("plugin %s revocation skipped: %s", plugin_id, exc)
        revocation = "failed"

    store.delete(plugin_id)
    _refresh_plugin_in_live_registry(plugin_id)
    if plugin_id == "telegram":
        try:
            on_telegram_disconnected()
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram channel disable failed: %s", exc)
    elif plugin_id == "discord":
        try:
            on_discord_disconnected()
        except Exception as exc:  # noqa: BLE001
            log.warning("discord channel disable failed: %s", exc)
    live_applied = False
    if plugin_id in _CHANNEL_PLUGIN_IDS:
        live_applied = await apply_channel_live(request.app.state, plugin_id)
    return {
        "ok": True,
        "plugin_id": plugin_id,
        "status": "not_connected",
        "live_applied": live_applied,
        # "revoked" | "unsupported" | "failed" — so the UI can say honestly
        # whether the user still has to remove the app at the provider.
        "revocation": revocation,
    }


# ----------------------------------------------------------------------
# Community marketplace (registry index browse / install / uninstall)
# ----------------------------------------------------------------------


def _community_payload(
    index: Any, status: str
) -> dict[str, Any]:
    """Convert a fetched index into the wire shape the Plugins view renders.

    Every plugin entry is run through the SAME converter the install path
    uses, so "shown as installable" and "actually installs" cannot drift: an
    entry the loader rejects renders as an explicit incompatible card instead
    of failing later at install time. Skills carry their install state from
    the user skills directory.
    """
    from jarvis.core.paths import user_skills_dir
    from jarvis.marketplace.agent_plugins_loader import (
        AgentPluginError,
        convert_manifest,
    )

    catalog = load_catalog()
    installed_specs = {spec.id: spec for spec in catalog.plugins}

    plugins: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    if index is not None:
        for entry in index.plugins:
            base = {
                "name": entry.name,
                "publisher": entry.publisher,
                "version": entry.version,
                "published_at": entry.published_at,
                "source_url": entry.source_url,
            }
            try:
                spec = convert_manifest(
                    entry.plugin_json,
                    entry.mcp_json,
                    publisher=entry.publisher,
                    version=entry.version,
                    source_url=entry.source_url,
                )
                if spec.id != entry.name:
                    raise AgentPluginError(
                        f"index name {entry.name!r} does not match manifest "
                        f"name {spec.id!r}"
                    )
            except AgentPluginError as exc:
                plugins.append({**base, "valid": False, "error": str(exc)})
                continue
            item = spec.model_dump(mode="json")
            item.update(base)
            item["valid"] = True
            existing = installed_specs.get(spec.id)
            item["installed"] = existing is not None and existing.source == "community"
            item["installed_version"] = (
                existing.version if existing is not None else None
            )
            # A community name colliding with a shipped plugin is never
            # installable — surfaced so the UI explains WHY the button is off.
            item["seed_conflict"] = (
                existing is not None and existing.source != "community"
            )
            item["has_usage_card"] = bool(entry.usage_card)
            plugins.append(item)

        skills_root = user_skills_dir()
        for skill in index.skills:
            skills.append(
                {
                    "name": skill.name,
                    "title": skill.title or skill.name,
                    "description": skill.description,
                    "publisher": skill.publisher,
                    "version": skill.version,
                    "published_at": skill.published_at,
                    "categories": list(skill.categories),
                    "source_url": skill.source_url,
                    "raw_url": skill.raw_url,
                    "installed": (skills_root / skill.name / "SKILL.md").exists(),
                }
            )

    return {
        "status": status,
        "revision": getattr(index, "revision", None),
        "generated_at": getattr(index, "generated_at", None),
        "plugins": plugins,
        "skills": skills,
    }


@router.get("/community", openapi_extra={"x-jarvis-readonly": True})
async def community_browse(response: Response) -> dict[str, Any]:
    """The community index (TTL-cached fetch), enriched with install state."""
    from jarvis.marketplace import community_source

    response.headers["Cache-Control"] = "no-store"
    index, status = await community_source.get_index()
    return _community_payload(index, status)


@router.post("/community/refresh")
async def community_refresh(response: Response) -> dict[str, Any]:
    """Force a re-fetch of the community index, bypassing the TTL."""
    from jarvis.marketplace import community_source

    response.headers["Cache-Control"] = "no-store"
    index, status = await community_source.get_index(force=True)
    return _community_payload(index, status)


@router.post("/community/plugins/{plugin_id}/install")
async def community_install(plugin_id: str) -> dict[str, Any]:
    """One-click install: convert the manifest, persist the catalog entry and
    usage card, refresh the live registry. The plugin then behaves exactly
    like a seed plugin (connect flows, relevance gate, worker bridge)."""
    from jarvis.marketplace import community_source
    from jarvis.marketplace.agent_plugins_loader import (
        AgentPluginError,
        convert_manifest,
    )
    from jarvis.marketplace.community_install import (
        install_plugin_spec,
        seed_plugin_ids,
    )
    from jarvis.marketplace.usage_cards.loader import save_usage_card

    index, _ = await community_source.get_index()
    entry = None
    if index is not None:
        entry = next((e for e in index.plugins if e.name == plugin_id), None)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"plugin {plugin_id!r} is not in the community index",
        )
    if plugin_id in seed_plugin_ids():
        raise HTTPException(
            status_code=409,
            detail=f"{plugin_id!r} is a built-in plugin id and cannot be "
            "installed from the community index",
        )
    existing = load_catalog().by_id(plugin_id)
    if existing is not None and existing.source != "community":
        raise HTTPException(
            status_code=409,
            detail=f"{plugin_id!r} already exists in the local catalog",
        )
    try:
        spec = convert_manifest(
            entry.plugin_json,
            entry.mcp_json,
            publisher=entry.publisher,
            version=entry.version,
            source_url=entry.source_url,
        )
        if spec.id != plugin_id:
            raise AgentPluginError(
                f"index name {plugin_id!r} does not match manifest name {spec.id!r}"
            )
    except AgentPluginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        install_plugin_spec(spec)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if entry.usage_card:
        try:
            save_usage_card(spec.id, entry.usage_card)
        except (ValueError, OSError) as exc:
            # Keywords are a quality upgrade, not a prerequisite — the
            # relevance gate still matches on the plugin's own name/tools.
            log.warning("usage card for %s not saved: %s", spec.id, exc)
    _refresh_plugin_in_live_registry(spec.id)
    item = spec.model_dump(mode="json")
    item["status"] = "not_connected"
    return {"ok": True, "plugin": item}


@router.delete("/community/plugins/{plugin_id}")
async def community_uninstall(plugin_id: str) -> dict[str, Any]:
    """Remove an installed community plugin: revoke + drop stored tokens,
    remove the catalog entry and its usage card, refresh the live registry."""
    from jarvis.marketplace.community_install import remove_community_plugin
    from jarvis.marketplace.usage_cards.loader import delete_usage_card

    spec = load_catalog().by_id(plugin_id)
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"plugin {plugin_id!r} not in catalog"
        )
    if spec.source != "community":
        raise HTTPException(
            status_code=409,
            detail=f"{plugin_id!r} is a built-in plugin — disconnect it "
            "instead of uninstalling",
        )

    # ALL local state changes happen before the first await: the provider
    # revocation below can take seconds, and an install of the same id that
    # completes inside that window must not be wiped when this handler
    # resumes. The loaded tokens object stays in memory, so revoking after
    # the local delete loses nothing.
    store = TokenStore()
    tokens: Tokens | None = None
    try:
        tokens = store.load(plugin_id)
    except Exception as exc:  # noqa: BLE001 - never block the uninstall
        log.info("plugin %s token load for revocation skipped: %s", plugin_id, exc)
    store.delete(plugin_id)
    try:
        removed = remove_community_plugin(plugin_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    delete_usage_card(plugin_id)
    _refresh_plugin_in_live_registry(plugin_id)

    revocation = "unsupported"
    if tokens is not None:
        try:
            revocation = await revoke_tokens(spec, tokens)
        except Exception as exc:  # noqa: BLE001 - never block the uninstall
            log.info("plugin %s revocation skipped: %s", plugin_id, exc)
            revocation = "failed"
    return {"ok": True, "removed": removed, "revocation": revocation}
