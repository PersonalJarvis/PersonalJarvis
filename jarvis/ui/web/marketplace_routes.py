"""REST API for the Plugin Marketplace.

Endpoints:
    GET    /api/marketplace/plugins                       — catalog + status
    POST   /api/marketplace/plugins/{id}/connect/pat       — paste-token (Vercel, Supabase fallback)
    POST   /api/marketplace/plugins/{id}/connect/start     — kick off OAuth redirect flow
    GET    /api/marketplace/plugins/{id}/connect/poll/{flow_id} — poll until completion
    DELETE /api/marketplace/plugins/{id}                   — disconnect
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

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
    HostedMcpOAuthDcrAuth,
    OAuthDeviceFlowAuth,
    OAuthPkceLoopbackAuth,
    PatPasteAuth,
)
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.telegram_connect import (
    on_telegram_connected,
    on_telegram_disconnected,
)
from jarvis.marketplace.token_store import Tokens, TokenStore

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
            asyncio.create_task(
                reg.refresh_plugin(plugin_id), name=f"plugin-refresh:{plugin_id}"
            )
    except Exception:  # noqa: BLE001
        # A failed re-expand after the user just connected a plugin is a
        # recoverable workflow failure, not a hot-path event — log at WARNING
        # so it surfaces without a debug flag.
        log.warning(
            "live plugin refresh failed for %s", plugin_id, exc_info=True
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _plugin_status(plugin_id: str, store: TokenStore) -> str:
    try:
        tokens = store.load(plugin_id)
    except RuntimeError:
        return "error"
    if tokens is None:
        return "not_connected"
    if tokens.needs_reauth:
        return "needs_reauth"
    return "connected"


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

    async def _validate(auth: PatPasteAuth, token: str) -> tuple[bool, int]:
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
        status = _plugin_status(spec.id, store)
        item["status"] = status
        mcp = spec.mcp_server or {}
        mcp_live = str(mcp.get("transport", "")).lower() in ("http", "stdio")
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
        "plugins": enriched,
        "total": len(enriched),
        "connected": connected,
    }


# ----------------------------------------------------------------------
# PAT-paste connect (Vercel, Supabase fallback)
# ----------------------------------------------------------------------


class PatConnectBody(BaseModel):
    token: str = Field(min_length=1, max_length=2048)


@router.post("/plugins/{plugin_id}/connect/pat")
async def connect_pat(plugin_id: str, body: PatConnectBody) -> dict[str, Any]:
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

    try:
        ok, status = await _validate_token(spec.auth, token)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"could not reach {spec.auth.validation_endpoint}: {type(exc).__name__}",
        ) from exc

    if not ok:
        raise HTTPException(
            status_code=401,
            detail=f"{spec.display_name} rejected the token (HTTP {status})",
        )

    store = TokenStore()
    store.save(plugin_id, Tokens(access=token))
    if plugin_id == "telegram":
        # Telegram "connect" enables the in-repo channel. Do not report a
        # successful Marketplace connect if the canonical channel secret/config
        # could not be written; otherwise the UI says "connected" while the bot
        # cannot start.
        try:
            on_telegram_connected(token)
        except Exception as exc:  # noqa: BLE001
            try:
                store.delete(plugin_id)
            except Exception as cleanup_exc:  # noqa: BLE001
                log.debug(
                    "telegram token cleanup after failed enable failed: %s",
                    cleanup_exc,
                )
            log.warning("telegram channel enable failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail=f"telegram-channel-enable-failed: {type(exc).__name__}",
            ) from exc
    _refresh_plugin_in_live_registry(plugin_id)
    return {"ok": True, "plugin_id": plugin_id, "status": "connected"}


# ----------------------------------------------------------------------
# OAuth redirect connect (Notion, Supabase main path)
# ----------------------------------------------------------------------


@router.post("/plugins/{plugin_id}/connect/start")
async def connect_start(
    plugin_id: str, background: BackgroundTasks
) -> dict[str, Any]:
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
        handler = PkceLoopbackHandler(
            PkceLoopbackConfig(
                plugin_id=plugin_id,
                authorization_url=spec.auth.authorization_url,
                token_url=spec.auth.token_url,
                client_id=spec.auth.client_id,
                client_secret=spec.auth.client_secret,
                callback_port=spec.auth.callback_port or 0,
                scopes=list(spec.auth.scopes),
                scope_separator=spec.auth.scope_separator,
                # Slack-specific: PKCE-enabled apps must use user_scope= per
                # docs.slack.dev/authentication/using-pkce. When the catalog
                # marks a plugin user-scopes-only, route the param.
                scope_param_name=(
                    "user_scope" if spec.auth.user_scopes_only else "scope"
                ),
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
                slot.result = await handler.await_completion(session)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "plugin %s connect/await failed: %s", plugin_id, exc
                )
                slot.result = FlowResult(tokens=None, error=str(exc))
            else:
                if slot.result.tokens is not None:
                    TokenStore().save(plugin_id, slot.result.tokens)
                    _refresh_plugin_in_live_registry(plugin_id)
                    log.info("plugin %s connected via DCR", plugin_id)

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
async def oauth_callback(
    code: str = "", state: str = "", error: str = ""
) -> HTMLResponse:
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
async def disconnect(plugin_id: str) -> dict[str, Any]:
    catalog = load_catalog()
    if catalog.by_id(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"plugin {plugin_id!r} not in catalog")
    TokenStore().delete(plugin_id)
    _refresh_plugin_in_live_registry(plugin_id)
    if plugin_id == "telegram":
        try:
            on_telegram_disconnected()
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram channel disable failed: %s", exc)
    return {"ok": True, "plugin_id": plugin_id, "status": "not_connected"}
