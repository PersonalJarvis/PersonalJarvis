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


# Catalog client_id values that are unfilled placeholders, not real OAuth
# clients. The public catalog can't ship the maintainer's Google client, so
# gmail/google_drive carry a REPLACE_WITH_... marker until the operator supplies
# a real client via the `google_oauth_client_id` secret. Sending a placeholder
# to a token endpoint yields `invalid_client: "The OAuth client was not found."`.
_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "replace_with",
    "your_client_id",
    "your-client-id",
    "<",
    "changeme",
    "todo",
)

# Plugins that share one Google OAuth client (per the catalog gmail hint:
# "shares the Google client with Drive"). Their real client is resolved from the
# shared `google_oauth_client_id` / `google_oauth_client_secret` secrets.
_GOOGLE_FAMILY: frozenset[str] = frozenset({"gmail", "google_drive", "google_calendar"})


def is_placeholder_client_id(value: str | None) -> bool:
    """True when an OAuth client_id is empty or an unfilled catalog placeholder."""
    if value is None:
        return True
    v = value.strip()
    if not v:
        return True
    low = v.lower()
    return any(marker in low for marker in _PLACEHOLDER_MARKERS)


def resolve_pkce_client(
    plugin_id: str, catalog_client_id: str, catalog_client_secret: str | None
) -> tuple[str, str | None]:
    """Resolve the effective (client_id, client_secret) for a PKCE plugin.

    Precedence: a secret override wins over the catalog value, so the operator
    can supply a real Google OAuth client without editing the tracked catalog
    (which gets re-synced from the seed). For the Google family the shared
    `google_oauth_client_id` / `google_oauth_client_secret` secrets are used;
    other PKCE plugins (e.g. Slack) keep their real catalog client untouched.
    """
    if plugin_id not in _GOOGLE_FAMILY:
        return catalog_client_id, catalog_client_secret
    from jarvis.core.config import get_secret

    cid = get_secret("google_oauth_client_id", "GOOGLE_OAUTH_CLIENT_ID")
    csec = get_secret("google_oauth_client_secret", "GOOGLE_OAUTH_CLIENT_SECRET")
    return (cid or catalog_client_id, csec or catalog_client_secret)


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
        client_id, client_secret = resolve_pkce_client(
            plugin_id, auth.client_id, auth.client_secret
        )
        return PkceLoopbackHandler(
            PkceLoopbackConfig(
                plugin_id=plugin_id,
                authorization_url=auth.authorization_url,
                token_url=auth.token_url,
                client_id=client_id,
                client_secret=client_secret,
                callback_port=auth.callback_port or 0,
                scopes=list(auth.scopes),
                scope_separator=auth.scope_separator,
                scope_param_name="user_scope" if auth.user_scopes_only else "scope",
                callback_path=auth.callback_path,
                resource=auth.resource,
                offline_access=auth.offline_access,
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
