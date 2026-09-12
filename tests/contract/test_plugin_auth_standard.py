"""Contract tests for the browser-auth standard.

Every test here runs against MOCKED provider transports — no real accounts,
no browser, no network. That is stated explicitly (and mirrored in
``docs/marketplace/plugin-auth-audit.md`` §E): these tests prove wiring and
honesty of the flow, never a live provider verification.

What they prove per plugin (parametrized over the real seed catalog):
  * DCR discovery URLs carry a host (Apollo/Granola regression).
  * A placeholder catalog client is NEVER standard-ready and keeps its
    expert-override family mapping.
  * Publisher > catalog precedence; expert BYO > publisher.
  * Cancel drops the pending callback listener (no leaked socket / late
    completion).
  * Flow errors carry a machine-readable code; provider bodies are
    sanitized before they reach logs/UI/storage.
  * PAT prefix checks accept all documented provider formats (GitHub
    classic + fine-grained).
  * No client secret ever serializes into a served payload.
"""

from __future__ import annotations

import pytest

from jarvis.marketplace import auth as auth_pkg
from jarvis.marketplace.auth.base import (
    ERROR_DENIED,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    AuthSession,
    FlowResult,
    sanitize_provider_error,
)
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.connect_helpers import (
    is_placeholder_client_id,
    oauth_client_family,
)


def _pkce_plugins():
    from jarvis.marketplace.catalog import OAuthPkceLoopbackAuth

    return [p for p in load_catalog().plugins if isinstance(p.auth, OAuthPkceLoopbackAuth)]


def _dcr_plugins():
    from jarvis.marketplace.catalog import HostedMcpOAuthDcrAuth

    return [p for p in load_catalog().plugins if isinstance(p.auth, HostedMcpOAuthDcrAuth)]


# ----------------------------------------------------------------------
# 1. DCR discovery URLs must be usable (Apollo/Granola regression)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("plugin", _dcr_plugins(), ids=lambda p: p.id)
def test_dcr_discovery_url_has_host(plugin):
    from urllib.parse import urlsplit

    host = urlsplit(plugin.auth.discovery_url).hostname
    assert host, f"{plugin.id}: discovery_url has no host: {plugin.auth.discovery_url!r}"


@pytest.mark.parametrize("plugin", _dcr_plugins(), ids=lambda p: p.id)
def test_dcr_handler_builds_from_catalog(plugin):
    from jarvis.marketplace.connect_helpers import build_handler_from_catalog

    handler = build_handler_from_catalog(plugin.id)
    assert handler is not None, f"{plugin.id}: no refresh handler builds"
    assert isinstance(handler, auth_pkg.HostedMcpDcrHandler)


# ----------------------------------------------------------------------
# 2. Placeholder clients are never standard-ready, keep expert mapping
# ----------------------------------------------------------------------


@pytest.mark.parametrize("plugin", _pkce_plugins(), ids=lambda p: p.id)
def test_placeholder_pkce_plugin_declares_expert_family(plugin):
    if is_placeholder_client_id(plugin.auth.client_id):
        assert oauth_client_family(plugin.id), (
            f"{plugin.id}: placeholder client with no oauth_client_family — "
            "the expert override has nowhere to go"
        )


@pytest.mark.parametrize("plugin", _pkce_plugins(), ids=lambda p: p.id)
def test_placeholder_is_not_standard_ready_without_secrets(plugin, monkeypatch):
    from jarvis.marketplace.publisher_clients import (
        is_standard_ready,
        resolve_publisher_client,
    )

    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *a, **k: None)
    _cid, _sec, source = resolve_publisher_client(
        plugin.id, plugin.auth.client_id, plugin.auth.client_secret
    )
    if is_placeholder_client_id(plugin.auth.client_id):
        assert source == "missing", f"{plugin.id}: placeholder resolved as {source!r}"
        assert not is_standard_ready(plugin.id, plugin.auth.client_id)


# ----------------------------------------------------------------------
# 3. Precedence: expert BYO > publisher > catalog
# ----------------------------------------------------------------------


def test_publisher_client_beats_catalog_placeholder(monkeypatch):
    from jarvis.marketplace.publisher_clients import resolve_publisher_client

    def fake_get(key, env_fallback=None):
        if key == "publisher_microsoft_oauth_client_id":
            return "publisher-provided-id"
        if key == "publisher_microsoft_oauth_client_secret":
            return "publisher-provided-secret"
        return None

    monkeypatch.setattr("jarvis.core.config.get_secret", fake_get)
    cid, sec, source = resolve_publisher_client("outlook", "REPLACE_WITH_YOUR_CLIENT_ID", None)
    assert (cid, sec, source) == (
        "publisher-provided-id",
        "publisher-provided-secret",
        "publisher",
    )


def test_expert_override_beats_publisher(monkeypatch):
    from jarvis.marketplace.publisher_clients import resolve_publisher_client

    def fake_get(key, env_fallback=None):
        if key == "microsoft_oauth_client_id":
            return "expert-id"
        if key == "publisher_microsoft_oauth_client_id":
            return "publisher-provided-id"
        return None

    monkeypatch.setattr("jarvis.core.config.get_secret", fake_get)
    cid, _sec, source = resolve_publisher_client("outlook", "REPLACE_WITH_YOUR_CLIENT_ID", None)
    assert (cid, source) == ("expert-id", "own")


def test_real_catalog_client_keeps_source_catalog(monkeypatch):
    from jarvis.marketplace.publisher_clients import resolve_publisher_client

    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *a, **k: None)
    cid, _sec, source = resolve_publisher_client("spotify", "real-id", None)
    assert (cid, source) == ("real-id", "catalog")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["publisher", "own", "missing"])
async def test_device_connect_uses_resolved_client(monkeypatch, source):
    import asyncio

    from fastapi import BackgroundTasks, HTTPException

    from jarvis.marketplace.auth.base import FlowRegistry
    from jarvis.ui.web import marketplace_routes as routes

    captured = []
    registry = FlowRegistry()
    completed = asyncio.Event()

    def fake_get(key, env_fallback=None):
        if source == "own" and key == "github_oauth_client_id":
            return "expert-client"
        if source != "missing" and key == "publisher_github_oauth_client_id":
            return "publisher-client"
        return None

    class DeviceHandler:
        def __init__(self, config):
            captured.append(config.client_id)

        async def start(self, spec):
            return AuthSession(flow_id="resolved-device", plugin_id=spec.id, kind="device_flow")

        async def await_completion(self, session):
            completed.set()
            return FlowResult(tokens=None, error="denied", error_code=ERROR_DENIED)

    monkeypatch.setattr("jarvis.core.config.get_secret", fake_get)
    monkeypatch.setattr(routes, "DeviceFlowHandler", DeviceHandler)
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    if source == "missing":
        with pytest.raises(HTTPException) as exc:
            await routes.connect_start("github", BackgroundTasks())
        assert exc.value.status_code == 409
        assert captured == []
    else:
        response = await routes.connect_start("github", BackgroundTasks())
        await asyncio.wait_for(completed.wait(), timeout=1)
        assert response["kind"] == "device_flow"
        assert captured == ["expert-client" if source == "own" else "publisher-client"]


# ----------------------------------------------------------------------
# 4. Cancel cleans the pending callback listener
# ----------------------------------------------------------------------


def test_device_refresh_handler_resolves_publisher_client(monkeypatch):
    from jarvis.marketplace.connect_helpers import build_handler_from_catalog

    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, *args: (
            "publisher-client" if key == "publisher_github_oauth_client_id" else None
        ),
    )
    handler = build_handler_from_catalog("github")
    assert handler._config.client_id == "publisher-client"


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False])
async def test_device_refresh_preserves_issuing_client(monkeypatch, bound):
    import httpx

    from jarvis.marketplace.auth.oauth_device import DeviceFlowConfig, DeviceFlowHandler
    from jarvis.marketplace.token_store import Tokens

    requests = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *, data, headers):
            requests.append(data)
            return httpx.Response(
                200, json={"access_token": "renewed-access", "refresh_token": "rotated"}
            )

    monkeypatch.setattr("jarvis.marketplace.auth.oauth_device.httpx.AsyncClient", Client)
    handler = DeviceFlowHandler(
        DeviceFlowConfig(
            plugin_id="github",
            device_url="https://example.com/device",
            verify_url="https://example.com/verify",
            token_url="https://example.com/token",  # noqa: S106 — endpoint, not a credential
            client_id="new-client",
            scopes=[],
        )
    )
    current = Tokens(
        access="old-access",
        refresh="old-refresh",
        extra={"client_id": "issuing-client"} if bound else {},
    )
    renewed = await handler.refresh(current)
    expected = "issuing-client" if bound else "new-client"
    assert requests[0]["client_id"] == expected
    assert renewed.extra["client_id"] == expected
    assert renewed.refresh == "rotated"


@pytest.mark.asyncio
async def test_poll_cannot_consume_another_plugins_flow(monkeypatch):
    from fastapi import HTTPException

    from jarvis.marketplace.auth.base import FlowRegistry
    from jarvis.ui.web import marketplace_routes as routes

    registry = FlowRegistry()
    session = AuthSession(flow_id="owned-flow", plugin_id="github", kind="device_flow")
    registry.put(object(), session)
    registry.get(session.flow_id).result = FlowResult(tokens=None, error="denied")
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    with pytest.raises(HTTPException) as exc:
        await routes.connect_poll("notion", session.flow_id)
    assert exc.value.status_code == 404
    assert registry.get(session.flow_id) is not None
    assert (await routes.connect_poll("github", session.flow_id))["state"] == "error"


class _FakeCallbackServer:
    def __init__(self):
        self.stopped = False
        self.redirect_uri = "http://127.0.0.1:9/oauth/callback"

    async def start(self):
        return None

    async def await_callback(self):
        raise AssertionError("cancelled flow must never be awaited")

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_pkce_cancel_stops_listener_and_clears_pending(monkeypatch):
    from jarvis.marketplace.auth.oauth_pkce_loopback import (
        PkceLoopbackConfig,
        PkceLoopbackHandler,
    )

    servers: list[_FakeCallbackServer] = []

    def fake_factory(*a, **k):
        srv = _FakeCallbackServer()
        servers.append(srv)
        return srv

    monkeypatch.setattr(
        "jarvis.marketplace.auth.oauth_pkce_loopback.make_callback_server",
        fake_factory,
    )
    handler = PkceLoopbackHandler(
        PkceLoopbackConfig(
            plugin_id="probe",
            authorization_url="https://example.com/auth",
            token_url="https://example.com/token",  # noqa: S106 — test fixture URL, not a secret
            client_id="cid",
            callback_port=0,
            scopes=[],
        )
    )
    session = await handler.start(object())
    assert session.flow_id in handler._pending
    await handler.cancel(session)
    assert session.flow_id not in handler._pending
    assert servers and servers[0].stopped


@pytest.mark.asyncio
async def test_dcr_cancel_clears_pending():
    from jarvis.marketplace.auth.oauth_dcr import DcrConfig, HostedMcpDcrHandler

    handler = HostedMcpDcrHandler(
        DcrConfig(plugin_id="probe", discovery_url="https://example.com/x")
    )
    session = AuthSession(flow_id="dead", plugin_id="probe", kind="browser_redirect")
    # Cancelling an unknown flow is a no-op, never an error.
    await handler.cancel(session)


@pytest.mark.asyncio
async def test_device_cancel_clears_pending():
    from datetime import UTC, datetime, timedelta

    from jarvis.marketplace.auth.oauth_device import (
        DeviceFlowConfig,
        DeviceFlowHandler,
        _PendingDeviceFlow,
    )

    handler = DeviceFlowHandler(
        DeviceFlowConfig(
            plugin_id="probe",
            device_url="https://example.com/device",
            verify_url="https://example.com/verify",
            token_url="https://example.com/token",  # noqa: S106 — test fixture URL, not a secret
            client_id="cid",
            scopes=[],
        )
    )
    handler._pending["f1"] = _PendingDeviceFlow(
        config=handler._config,
        device_code="dc",
        interval=5,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    await handler.cancel(AuthSession(flow_id="f1", plugin_id="probe", kind="device_flow"))
    assert "f1" not in handler._pending


@pytest.mark.asyncio
async def test_device_cancel_flags_poll_to_stop():
    from datetime import UTC, datetime, timedelta

    from jarvis.marketplace.auth.oauth_device import (
        DeviceFlowConfig,
        DeviceFlowHandler,
        _PendingDeviceFlow,
    )

    handler = DeviceFlowHandler(
        DeviceFlowConfig(
            plugin_id="probe",
            device_url="https://example.com/device",
            verify_url="https://example.com/verify",
            token_url="https://example.com/token",  # noqa: S106 — test fixture URL, not a secret
            client_id="cid",
            scopes=[],
        )
    )
    pending = _PendingDeviceFlow(
        config=handler._config,
        device_code="dc",
        interval=0,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    handler._pending["f1"] = pending
    session = AuthSession(flow_id="f1", plugin_id="probe", kind="device_flow")
    await handler.cancel(session)
    assert pending.cancelled
    # The orphaned poll loop observes the flag on its next tick and aborts
    # instead of polling until expiry.
    with pytest.raises(RuntimeError, match="cancelled"):
        await handler._poll(pending)


# ----------------------------------------------------------------------
# 5. Error taxonomy + sanitizing
# ----------------------------------------------------------------------


def test_sanitize_masks_token_like_material():
    dirty = (
        'token exchange HTTP 400: {"error": "bad", "access_token": "sekretABCDEF1234567890abcdef"}'
    )
    clean = sanitize_provider_error(dirty)
    assert "sekretABCDEF1234567890abcdef" not in clean
    assert clean == "provider request failed"


@pytest.mark.parametrize(
    "body", ["email=private@example.invalid", '{"password":"short"}', "secret: a b c"]
)
def test_provider_error_never_echoes_unrecognized_sensitive_values(body):
    assert sanitize_provider_error(body) == "provider request failed"


def test_sanitize_truncates_and_never_empty():
    assert sanitize_provider_error("") == "provider request failed"
    long = "x" * 500
    assert len(sanitize_provider_error(long)) <= 161


def test_flow_result_carries_error_code():
    res = FlowResult(tokens=None, error="user denied authorization", error_code=ERROR_DENIED)
    assert res.error_code == ERROR_DENIED
    # Backward compatible: code defaults to None.
    assert FlowResult(tokens=None, error="x").error_code is None


def test_device_error_classification():
    from jarvis.marketplace.auth.oauth_device import _classify_device_error

    assert _classify_device_error(RuntimeError("user denied authorization")) == ERROR_DENIED
    assert _classify_device_error(RuntimeError("device code expired")) == ERROR_TIMEOUT
    assert _classify_device_error(RuntimeError("boom")) == ERROR_UNKNOWN


def test_pkce_error_classification():
    from jarvis.marketplace.auth.oauth_pkce_loopback import _classify_callback_error

    assert (
        _classify_callback_error(RuntimeError("OAuth provider returned error: access_denied"))
        == ERROR_DENIED
    )
    assert _classify_callback_error(RuntimeError("weird")) == ERROR_UNKNOWN


# ----------------------------------------------------------------------
# 6. PAT prefixes incl. GitHub fine-grained
# ----------------------------------------------------------------------


def test_pat_prefix_helper_accepts_documented_formats():
    from jarvis.marketplace.catalog import PatPasteAuth
    from jarvis.ui.web.marketplace_routes import pat_prefix_ok

    github = PatPasteAuth(
        mode="pat_paste",
        token_creation_url="https://github.com/settings/tokens",  # noqa: S106 — field name, not a secret
        token_prefix="ghp",  # noqa: S106 — field name, not a secret
        token_prefixes=["github_pat_"],
        validation_endpoint="https://api.github.com/user",
        instruction_md="x",
    )
    assert pat_prefix_ok(github, "ghp_abc123")
    assert pat_prefix_ok(github, "github_pat_abc123")
    assert not pat_prefix_ok(github, "junk-token")

    prefixless = PatPasteAuth(
        mode="pat_paste",
        token_creation_url="https://example.com",  # noqa: S106 — field name, not a secret
        token_prefix="",
        validation_endpoint="https://example.com/me",
        instruction_md="x",
    )
    assert pat_prefix_ok(prefixless, "anything-goes-to-provider-validation")


def test_seed_github_accepts_fine_grained_prefix():
    spec = load_catalog().by_id("github")
    assert spec is not None
    # GitHub is browser-primary now; the accepted paste formats live on the
    # token fallback.
    assert spec.fallback_auth is not None
    assert "github_pat_" in (spec.fallback_auth.token_prefixes or [])


# ----------------------------------------------------------------------
# 7. No secret material in served payloads
# ----------------------------------------------------------------------


@pytest.mark.parametrize("plugin", _pkce_plugins(), ids=lambda p: p.id)
def test_client_secret_never_serializes(plugin):
    dump = plugin.model_dump(mode="json")
    auth = dump.get("auth", {})
    assert "client_secret" not in auth, f"{plugin.id}: client_secret leaks to payload"


@pytest.mark.asyncio
async def test_poll_error_reports_machine_code():
    from jarvis.marketplace.auth import get_registry
    from jarvis.ui.web.marketplace_routes import connect_poll

    registry = get_registry()

    class _Handler:
        plugin_id = "probe"

    session = AuthSession(flow_id="poll-code-probe", plugin_id="probe", kind="device_flow")
    registry.put(_Handler(), session)  # type: ignore[arg-type]
    slot = registry.get(session.flow_id)
    assert slot is not None
    slot.result = FlowResult(tokens=None, error="user denied authorization", error_code="denied")
    try:
        body = await connect_poll("probe", session.flow_id)
    finally:
        registry.drop(session.flow_id)
    assert body["state"] == "error"
    assert body["error_code"] == "denied"


# ----------------------------------------------------------------------
# 8. Dual-mode: browser primary + token fallback
# ----------------------------------------------------------------------


def _migrated_dual_mode_ids():
    return ["github", "gitlab", "figma", "hubspot"]


@pytest.mark.parametrize("plugin_id", _migrated_dual_mode_ids())
def test_migrated_plugin_has_browser_primary_and_pat_fallback(plugin_id):
    from jarvis.marketplace.catalog import PatPasteAuth

    spec = load_catalog().by_id(plugin_id)
    assert spec is not None
    assert spec.auth.mode != "pat_paste", f"{plugin_id}: primary must be a browser flow"
    assert isinstance(spec.fallback_auth, PatPasteAuth), (
        f"{plugin_id}: previously working token path must survive as fallback_auth"
    )
    assert spec.fallback_auth.validation_endpoint.startswith("https://")


def test_pat_block_prefers_primary_then_fallback():
    from jarvis.marketplace.catalog import OAuthPkceLoopbackAuth, PatPasteAuth, PluginSpec
    from jarvis.ui.web.marketplace_routes import _pat_block

    pat = PatPasteAuth(
        mode="pat_paste",
        token_creation_url="https://example.com",  # noqa: S106 — field name, not a secret
        token_prefix="",
        validation_endpoint="https://example.com/me",
        instruction_md="x",
    )
    pat_primary = PluginSpec(
        id="p",
        display_name="P",
        description="d",
        category="c",
        logo_slug="p",
        auth=pat,
    )
    assert _pat_block(pat_primary) is pat

    pkce = OAuthPkceLoopbackAuth(
        mode="oauth_pkce_loopback",
        authorization_url="https://example.com/auth",
        token_url="https://example.com/token",  # noqa: S106 — test fixture URL, not a secret
        client_id="REPLACE_WITH_X",
        scopes=[],
    )
    dual = PluginSpec(
        id="q",
        display_name="Q",
        description="d",
        category="c",
        logo_slug="q",
        auth=pkce,
        fallback_auth=pat,
    )
    assert _pat_block(dual) is pat

    browser_only = PluginSpec(
        id="r",
        display_name="R",
        description="d",
        category="c",
        logo_slug="r",
        auth=pkce,
    )
    assert _pat_block(browser_only) is None


@pytest.mark.asyncio
async def test_connect_pat_accepts_fallback_plugin(monkeypatch):
    """The paste endpoint serves a dual-mode plugin through its fallback."""
    from jarvis.marketplace.token_store import InMemoryBackend, TokenStore
    from jarvis.ui.web import marketplace_routes as mr

    spec = load_catalog().by_id("gitlab")
    assert spec is not None and spec.fallback_auth is not None

    class _Catalog:
        def by_id(self, pid):
            return spec if pid == "gitlab" else None

    async def _ok(auth, token, instance_url=None):
        return True, 200

    store = TokenStore(InMemoryBackend())
    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog())
    monkeypatch.setattr(mr, "_validate_token", _ok)
    monkeypatch.setattr(mr, "TokenStore", lambda: store)

    body = mr.PatConnectBody(token="glpat-abc123")  # noqa: S106 — throwaway fixture token
    result = await mr.connect_pat("gitlab", body, None)  # type: ignore[arg-type]
    assert result["status"] == "connected"
    assert store.load("gitlab") is not None


@pytest.mark.asyncio
async def test_connect_pat_still_rejects_browser_only_plugin(monkeypatch):
    from jarvis.ui.web import marketplace_routes as mr

    spec = load_catalog().by_id("outlook")
    assert spec is not None and spec.fallback_auth is None

    class _Catalog:
        def by_id(self, pid):
            return spec if pid == "outlook" else None

    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog())
    body = mr.PatConnectBody(token="whatever")  # noqa: S106 — throwaway fixture token
    with pytest.raises(Exception) as exc_info:
        await mr.connect_pat("outlook", body, None)  # type: ignore[arg-type]
    assert getattr(exc_info.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_list_plugins_exposes_fallback_and_device_standard(monkeypatch):
    from fastapi import Response

    from jarvis.marketplace.token_store import InMemoryBackend, TokenStore
    from jarvis.ui.web import marketplace_routes as mr

    store = TokenStore(InMemoryBackend())
    monkeypatch.setattr(mr, "TokenStore", lambda: store)
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *a, **k: None)

    payload = await mr.list_plugins(Response())
    by_id = {p["id"]: p for p in payload["plugins"]}

    github = by_id["github"]
    assert github["fallback_auth"]["mode"] == "pat_paste"
    assert github["auth_standard"]["fallback"] is True
    assert github["auth_standard"]["ready"] is False
    assert github["oauth_client_configured"] is False

    gitlab = by_id["gitlab"]
    assert gitlab["fallback_auth"]["mode"] == "pat_paste"
    assert gitlab["auth_standard"]["fallback"] is True

    outlook = by_id["outlook"]
    assert outlook["fallback_auth"] is None
    assert outlook["auth_standard"]["fallback"] is False


def test_new_families_resolve_publisher_precedence(monkeypatch):
    from jarvis.marketplace.publisher_clients import resolve_publisher_client

    def fake_get(key, env_fallback=None):
        if key == "publisher_github_oauth_client_id":
            return "pub-github-id"
        return None

    monkeypatch.setattr("jarvis.core.config.get_secret", fake_get)
    cid, _, source = resolve_publisher_client(
        "github", "REPLACE_WITH_JARVIS_GITHUB_CLIENT_ID", None
    )
    assert (cid, source) == ("pub-github-id", "publisher")


def test_loader_accepts_and_guards_fallback_auth():
    from jarvis.marketplace.agent_plugins_loader import (
        AgentPluginError,
        convert_manifest,
    )

    base = {
        "name": "probe",
        "description": "d",
        "extensions": {
            "io.github.personaljarvis": {
                "auth": {
                    "mode": "oauth_pkce_loopback",
                    "authorization_url": "https://example.com/auth",
                    "token_url": "https://example.com/token",
                    "client_id": "REPLACE_WITH_X",
                    "scopes": [],
                },
                "fallback_auth": {
                    "mode": "pat_paste",
                    "token_creation_url": "https://example.com/tokens",
                    "token_prefix": "",
                    "validation_endpoint": "https://example.com/me",
                    "instruction_md": "x",
                },
            }
        },
    }
    spec = convert_manifest(base, None)
    assert spec.fallback_auth is not None
    assert spec.fallback_auth.mode == "pat_paste"

    bad_mode = {
        "name": "probe",
        "description": "d",
        "extensions": {
            "io.github.personaljarvis": {
                "auth": {
                    "mode": "oauth_pkce_loopback",
                    "authorization_url": "https://example.com/auth",
                    "token_url": "https://example.com/token",
                    "client_id": "REPLACE_WITH_X",
                    "scopes": [],
                },
                "fallback_auth": {
                    "mode": "hosted_mcp_allowlist",
                    "mcp_url": "https://example.com/mcp",
                },
            }
        },
    }
    with pytest.raises(AgentPluginError):
        convert_manifest(bad_mode, None)

    redundant = {
        "name": "probe",
        "description": "d",
        "extensions": {
            "io.github.personaljarvis": {
                "auth": {
                    "mode": "pat_paste",
                    "token_creation_url": "https://example.com/tokens",
                    "token_prefix": "",
                    "validation_endpoint": "https://example.com/me",
                    "instruction_md": "x",
                },
                "fallback_auth": {
                    "mode": "pat_paste",
                    "token_creation_url": "https://example.com/tokens",
                    "token_prefix": "",
                    "validation_endpoint": "https://example.com/me",
                    "instruction_md": "x",
                },
            }
        },
    }
    with pytest.raises(AgentPluginError):
        convert_manifest(redundant, None)


@pytest.mark.asyncio
async def test_prefix_error_names_formats_without_double_underscore(monkeypatch):
    """The 400 prefix hint shows 'github_pat_', never 'github_pat__'."""
    from jarvis.ui.web import marketplace_routes as mr

    spec = load_catalog().by_id("github")
    assert spec is not None and spec.fallback_auth is not None

    class _Catalog:
        def by_id(self, pid):
            return spec if pid == "github" else None

    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog())
    body = mr.PatConnectBody(token="junk-token")  # noqa: S106 — throwaway fixture token
    with pytest.raises(Exception) as exc_info:
        await mr.connect_pat("github", body, None)  # type: ignore[arg-type]
    assert getattr(exc_info.value, "status_code", None) == 400
    detail = str(getattr(exc_info.value, "detail", ""))
    assert "github_pat__" not in detail
    assert "github_pat_" in detail


def test_new_pkce_plugins_use_staggered_ports():
    """New PKCE plugins stay off the crowded shared 43891 loopback port."""
    ports = {}
    for pid in ("gitlab", "figma", "hubspot"):
        spec = load_catalog().by_id(pid)
        assert spec is not None
        port = spec.auth.callback_port
        assert port != 43891, f"{pid}: still on the shared port"
        ports[pid] = port
    assert len(set(ports.values())) == len(ports), f"ports collide: {ports}"


def test_github_mirror_matches_seed():
    import json
    from pathlib import Path

    manifest = Path("jarvis/marketplace/plugins/github/plugin.json")
    assert manifest.is_file(), "github agent-plugin mirror is missing"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    ext = data["extensions"]["io.github.personaljarvis"]
    seed = load_catalog().by_id("github")
    assert seed is not None
    assert ext["auth"]["mode"] == seed.auth.mode
    assert (ext.get("fallback_auth") is None) == (seed.fallback_auth is None)
    assert ext["fallback_auth"]["mode"] == "pat_paste"
