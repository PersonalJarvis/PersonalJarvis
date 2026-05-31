"""Catalog-backed helpers shared by the connect flow and the refresh scheduler.

Maps a catalog plugin id to its :class:`AuthHandler` (mirrors the dispatch in
``marketplace_routes.connect_start``) and lists currently-connected plugins.
Kept separate from the FastAPI route module so the refresh scheduler can build
handlers without importing the web layer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.marketplace.auth import AuthHandler
    from jarvis.marketplace.token_store import TokenStore

log = logging.getLogger(__name__)


def build_handler_from_catalog(plugin_id: str) -> AuthHandler | None:
    """Return the AuthHandler for a catalog plugin, or ``None`` if the plugin
    is unknown or uses a non-refreshable auth mode (e.g. ``pat_paste``)."""
    from jarvis.marketplace.auth import (
        DcrConfig,
        DeviceFlowConfig,
        DeviceFlowHandler,
        HostedMcpDcrHandler,
        PkceLoopbackConfig,
        PkceLoopbackHandler,
    )
    from jarvis.marketplace.catalog import (
        HostedMcpOAuthDcrAuth,
        OAuthDeviceFlowAuth,
        OAuthPkceLoopbackAuth,
    )
    from jarvis.marketplace.catalog_data import load_catalog

    spec = load_catalog().by_id(plugin_id)
    if spec is None:
        return None
    auth = spec.auth
    if isinstance(auth, HostedMcpOAuthDcrAuth):
        return HostedMcpDcrHandler(
            DcrConfig(plugin_id=plugin_id, discovery_url=auth.discovery_url)
        )
    if isinstance(auth, OAuthDeviceFlowAuth):
        return DeviceFlowHandler(
            DeviceFlowConfig(
                plugin_id=plugin_id,
                device_url=auth.device_url,
                verify_url=auth.verify_url,
                token_url=auth.token_url,
                client_id=auth.client_id,
                scopes=list(auth.scopes),
            )
        )
    if isinstance(auth, OAuthPkceLoopbackAuth):
        return PkceLoopbackHandler(
            PkceLoopbackConfig(
                plugin_id=plugin_id,
                authorization_url=auth.authorization_url,
                token_url=auth.token_url,
                client_id=auth.client_id,
                callback_port=auth.callback_port or 0,
                scopes=list(auth.scopes),
                scope_param_name="user_scope" if auth.user_scopes_only else "scope",
            )
        )
    return None  # pat_paste / allowlist — no refreshable OAuth handler


def connected_plugin_ids(store: TokenStore) -> list[str]:
    """Catalog plugin ids that currently have tokens stored."""
    from jarvis.marketplace.catalog_data import load_catalog

    ids: list[str] = []
    for spec in load_catalog().plugins:
        try:
            if store.load(spec.id) is not None:
                ids.append(spec.id)
        except RuntimeError:
            # Corrupted blob — treat as not-connected for scheduling purposes.
            continue
    return ids
