"""Publisher-provisioned OAuth clients for the browser-auth standard.

Product standard: a normal user clicks "Connect", signs in at the real
provider in the browser, and the plugin works — without creating tokens,
client IDs, secrets, developer apps, redirect URIs, env vars, or config
edits. The app registration and public client configuration are provided
by the PROJECT (the plugin publisher), not by every end user.

This module is the single place that resolves which OAuth client a PKCE
plugin actually uses, and — honestly — WHERE that client came from:

Precedence for one ``<family>`` (e.g. ``microsoft``):
  1. Expert override: ``<family>_oauth_client_id`` / ``<family>_oauth_client_secret``
     secrets (the long-standing bring-your-own-client escape hatch). Wins so
     an expert's explicit choice is never silently displaced.
  2. Publisher secret: ``publisher_<family>_oauth_client_id`` /
     ``publisher_<family>_oauth_client_secret`` secrets (env fallback
     ``PUBLISHER_<FAMILY>_OAUTH_CLIENT_*``). Lets a distribution rotate the
     shared client without a code change.
  3. Shipped public client: ``SHIPPED_PUBLIC_CLIENT_IDS``. A public client id
     built into the app, used by every install with zero setup. Not a secret.
     When the family also has a ``SHIPPED_TOKEN_BROKERS`` entry, the provider
     insists on a client secret the app cannot carry, so the code exchange and
     every refresh go to that broker, which adds the secret server-side.
  4. Catalog value: the ``client_id`` shipped in ``seed_catalog.json`` (or a
     user's ``data/`` override). Only used when it is a real client id, never
     a ``REPLACE_WITH_*`` placeholder.

A publisher client SECRET is confidential: it lives only in the backend
secret store, never in the repo, the frontend payload, or the desktop
bundle. The connect dialog only ever learns ``source`` (``publisher``,
``own``, ``catalog``, ``missing``) plus a boolean — never the id/secret.

Until a publisher registers a real app for a family, the standard flow for
that family's plugins is EXTERNALLY BLOCKED (see
``docs/marketplace/plugin-auth-audit.md``). The code reports that honestly
instead of inventing a client id. A family listed in
``SHIPPED_PUBLIC_CLIENT_IDS`` is registered and standard-ready.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

ClientSource = Literal["publisher", "own", "catalog", "missing"]
"""Where the effective OAuth client came from.

``publisher`` — shared client provisioned by the project (standard path).
``own`` — the user's own (expert) client override.
``catalog`` — a real client id shipped in the catalog entry itself.
``missing`` — only a placeholder is available; standard flow is blocked.
"""

# Public OAuth client ids built into the app. They are not confidential:
# every sign-in address contains them. Client secrets never belong here.
# Microsoft is one Entra public client named "Personal Jarvis": personal and
# work/school accounts, loopback PKCE on http://127.0.0.1:43891/oauth/callback,
# no client secret. One id serves every plugin in the family.
# GitHub is the "Personal Jarvis" OAuth App owned by the PersonalJarvis
# organisation: Device Flow enabled, non-expiring user tokens, no client secret
# (the device flow never sends one).
# Google is the "Personal Jarvis" Desktop client: loopback PKCE on the plugins'
# fixed 127.0.0.1 ports. Its token endpoint still refuses a code exchange or
# refresh without the client secret, so the family uses a token broker below.
# Slack is the publicly distributed "Personal Jarvis" Slack app. Slack only
# allows https redirect URLs for it, and web-redirect tokens need the secret,
# so the family uses a token broker with an https redirect below.
# Figma is the "Personal Jarvis" Figma OAuth app: loopback PKCE on
# http://127.0.0.1:3127/oauth/callback. Its token and refresh endpoints take
# the client secret in a Basic header, so the family uses a token broker.
SHIPPED_PUBLIC_CLIENT_IDS: dict[str, str] = {
    "figma": "QnSTQR5QK5hAOjxBmp6sIY",
    "github": "Ov23liaA2KfHbfVOelcx",
    "google": "940985062784-4t8h10q3ggt6b1317ajjb9u0eriop2m7.apps.googleusercontent.com",
    "microsoft": "1986efcb-f871-4ffa-8df7-64c04dd3ab56",
    "slack": "11871821947191.12101850377302",
}


@dataclass(frozen=True)
class TokenBroker:
    """A project-run token endpoint that adds a shipped client's secret.

    ``token_url`` replaces the provider's token endpoint for the code exchange
    and every refresh. ``redirect_uri``, when set, is the https address the
    provider redirects the browser to instead of the app's loopback listener;
    the broker bounces that request on to the loopback listener, which still
    listens on the catalog's port and path. The code exchange then sends the
    same ``redirect_uri``, as OAuth requires.
    """

    token_url: str
    redirect_uri: str | None = None


_BROKER_BASE = "https://token.personaljarvis.ai/oauth"

# Token brokers for shipped clients whose provider requires a client secret.
# The broker is a small project-run Cloudflare Worker (source: the website
# repository, ``workers/token-broker``) that accepts only the
# authorization_code grant (PKCE verifier required, redirect_uri restricted to
# loopback or to the broker's own callback) and the refresh_token grant, adds
# the shipped client's id and secret, and returns the provider's answer
# unchanged. It stores and logs nothing. The app sends no client secret there
# and never holds one, so every install type — desktop bundle, pip, CLI —
# works with zero setup.
# A broker is used ONLY for grants issued to the shipped client: an expert
# override or a ``publisher_<family>_oauth_*`` secret talks to the provider's
# token endpoint directly with its own secret. Revocation never needs a secret
# and always goes straight to the provider.
SHIPPED_TOKEN_BROKERS: dict[str, TokenBroker] = {
    "figma": TokenBroker(token_url=f"{_BROKER_BASE}/figma/token"),
    "google": TokenBroker(token_url=f"{_BROKER_BASE}/google/token"),
    "slack": TokenBroker(
        token_url=f"{_BROKER_BASE}/slack/token",
        redirect_uri=f"{_BROKER_BASE}/slack/callback",
    ),
}


def token_broker(family: str | None) -> TokenBroker | None:
    """The shipped-client token broker of ``family``, if it has one."""
    if not family:
        return None
    return SHIPPED_TOKEN_BROKERS.get(family)


def broker_family_for_client(client_id: str | None) -> str | None:
    """The broker family whose shipped client is ``client_id``, else ``None``.

    Lets a refresh find the broker for a grant stored without the broker
    marker: a grant bound to the shipped client can only be refreshed there.
    """
    if not client_id:
        return None
    for family, shipped in SHIPPED_PUBLIC_CLIENT_IDS.items():
        if shipped == client_id and family in SHIPPED_TOKEN_BROKERS:
            return family
    return None


def publisher_secret_names(family: str) -> tuple[str, str]:
    """Secret-store key + env fallback for a family's publisher client id."""
    return f"publisher_{family}_oauth_client_id", f"PUBLISHER_{family.upper()}_OAUTH_CLIENT_ID"


def publisher_secret_names_secret(family: str) -> tuple[str, str]:
    """Secret-store key + env fallback for a family's publisher client secret."""
    return (
        f"publisher_{family}_oauth_client_secret",
        f"PUBLISHER_{family.upper()}_OAUTH_CLIENT_SECRET",
    )


@dataclass(frozen=True)
class ResolvedClient:
    """The effective OAuth client for one plugin and where its tokens go.

    ``token_broker`` names the ``SHIPPED_TOKEN_BROKERS`` family whose broker
    performs the code exchange and refresh, or is ``None`` for the provider's
    own token endpoint. It is set only for the shipped public client.
    """

    client_id: str
    client_secret: str | None
    source: ClientSource
    token_broker: str | None = None


def resolve_client(
    plugin_id: str,
    catalog_client_id: str,
    catalog_client_secret: str | None,
) -> ResolvedClient:
    """Resolve the effective client, its source, and its token broker.

    Precedence: expert BYO secret > publisher secret > shipped public client
    (plus its broker, when the family has one) > catalog value. An unset/empty
    secret never displaces a better value. A placeholder catalog id with
    nothing provisioned resolves to source ``missing`` with the catalog
    placeholder preserved so callers can keep reporting the honest 409.
    """
    from jarvis.marketplace.connect_helpers import (
        is_placeholder_client_id,
        oauth_client_family,
    )

    family = oauth_client_family(plugin_id)
    if family is None:
        # No family mapping: the catalog client is authoritative (DCR plugins
        # never reach here; static non-family clients stay untouched).
        if is_placeholder_client_id(catalog_client_id):
            return ResolvedClient(catalog_client_id, catalog_client_secret, "missing")
        return ResolvedClient(catalog_client_id, catalog_client_secret, "catalog")

    from jarvis.core.config import get_secret

    # 1. Expert override (bring-your-own-client). Kept first so an explicit
    # user choice always wins over anything provisioned. Talks to the
    # provider directly with the expert's own secret.
    own_id = get_secret(f"{family}_oauth_client_id", f"{family.upper()}_OAUTH_CLIENT_ID")
    own_secret = get_secret(
        f"{family}_oauth_client_secret", f"{family.upper()}_OAUTH_CLIENT_SECRET"
    )
    if own_id and not is_placeholder_client_id(own_id):
        return ResolvedClient(own_id, own_secret or catalog_client_secret, "own")

    # 2. Publisher-provisioned shared client from the secret store. The
    # distribution holds this secret itself, so no broker is involved.
    pub_id_key, pub_id_env = publisher_secret_names(family)
    pub_sec_key, pub_sec_env = publisher_secret_names_secret(family)
    pub_id = get_secret(pub_id_key, pub_id_env)
    if pub_id and not is_placeholder_client_id(pub_id):
        pub_secret = get_secret(pub_sec_key, pub_sec_env)
        return ResolvedClient(pub_id, pub_secret or catalog_client_secret, "publisher")

    # 3. Public client id built into the app. No secret is paired with it; a
    # family whose provider demands one exchanges tokens through its broker.
    shipped = SHIPPED_PUBLIC_CLIENT_IDS.get(family, "")
    if shipped and not is_placeholder_client_id(shipped):
        broker = family if family in SHIPPED_TOKEN_BROKERS else None
        return ResolvedClient(shipped, None, "publisher", broker)

    # 4. Catalog value, if real.
    if not is_placeholder_client_id(catalog_client_id):
        return ResolvedClient(catalog_client_id, catalog_client_secret, "catalog")

    return ResolvedClient(catalog_client_id, catalog_client_secret, "missing")


def resolve_publisher_client(
    plugin_id: str,
    catalog_client_id: str,
    catalog_client_secret: str | None,
) -> tuple[str, str | None, ClientSource]:
    """Resolve the effective ``(client_id, client_secret, source)``.

    Tuple form of :func:`resolve_client` for callers that never need the
    token broker (status payloads, device flow).
    """
    resolved = resolve_client(plugin_id, catalog_client_id, catalog_client_secret)
    return resolved.client_id, resolved.client_secret, resolved.source


def is_standard_ready(
    plugin_id: str,
    catalog_client_id: str,
    catalog_client_secret: str | None = None,
) -> bool:
    """True when the standard browser flow can start with no user BYO setup."""
    _, _, source = resolve_publisher_client(plugin_id, catalog_client_id, catalog_client_secret)
    return source in ("publisher", "catalog", "own")


__all__ = [
    "SHIPPED_PUBLIC_CLIENT_IDS",
    "SHIPPED_TOKEN_BROKERS",
    "ClientSource",
    "ResolvedClient",
    "TokenBroker",
    "broker_family_for_client",
    "is_standard_ready",
    "publisher_secret_names",
    "publisher_secret_names_secret",
    "resolve_client",
    "resolve_publisher_client",
    "token_broker",
]
