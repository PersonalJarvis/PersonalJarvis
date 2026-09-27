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
SHIPPED_PUBLIC_CLIENT_IDS: dict[str, str] = {
    "microsoft": "1986efcb-f871-4ffa-8df7-64c04dd3ab56",
}


def publisher_secret_names(family: str) -> tuple[str, str]:
    """Secret-store key + env fallback for a family's publisher client id."""
    return f"publisher_{family}_oauth_client_id", f"PUBLISHER_{family.upper()}_OAUTH_CLIENT_ID"


def publisher_secret_names_secret(family: str) -> tuple[str, str]:
    """Secret-store key + env fallback for a family's publisher client secret."""
    return (
        f"publisher_{family}_oauth_client_secret",
        f"PUBLISHER_{family.upper()}_OAUTH_CLIENT_SECRET",
    )


def resolve_publisher_client(
    plugin_id: str,
    catalog_client_id: str,
    catalog_client_secret: str | None,
) -> tuple[str, str | None, ClientSource]:
    """Resolve the effective ``(client_id, client_secret, source)``.

    Precedence: expert BYO secret > publisher secret > shipped public client
    > catalog value. An unset/empty secret never displaces a better value.
    A placeholder catalog id with nothing provisioned resolves to
    ``("...", None, "missing")`` with the catalog placeholder preserved so
    callers can keep reporting the honest 409.
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
            return catalog_client_id, catalog_client_secret, "missing"
        return catalog_client_id, catalog_client_secret, "catalog"

    from jarvis.core.config import get_secret

    # 1. Expert override (bring-your-own-client). Kept first so an explicit
    # user choice always wins over anything provisioned.
    own_id = get_secret(f"{family}_oauth_client_id", f"{family.upper()}_OAUTH_CLIENT_ID")
    own_secret = get_secret(
        f"{family}_oauth_client_secret", f"{family.upper()}_OAUTH_CLIENT_SECRET"
    )
    if own_id and not is_placeholder_client_id(own_id):
        return own_id, own_secret or catalog_client_secret, "own"

    # 2. Publisher-provisioned shared client (the standard path).
    pub_id_key, pub_id_env = publisher_secret_names(family)
    pub_sec_key, pub_sec_env = publisher_secret_names_secret(family)
    pub_id = get_secret(pub_id_key, pub_id_env)
    if pub_id and not is_placeholder_client_id(pub_id):
        pub_secret = get_secret(pub_sec_key, pub_sec_env)
        return pub_id, pub_secret or catalog_client_secret, "publisher"

    # 3. Public client id built into the app. No secret is paired with it.
    shipped = SHIPPED_PUBLIC_CLIENT_IDS.get(family, "")
    if shipped and not is_placeholder_client_id(shipped):
        return shipped, None, "publisher"

    # 4. Catalog value, if real.
    if not is_placeholder_client_id(catalog_client_id):
        return catalog_client_id, catalog_client_secret, "catalog"

    return catalog_client_id, catalog_client_secret, "missing"


def is_standard_ready(
    plugin_id: str,
    catalog_client_id: str,
    catalog_client_secret: str | None = None,
) -> bool:
    """True when the standard browser flow can start with no user BYO setup."""
    _, _, source = resolve_publisher_client(plugin_id, catalog_client_id, catalog_client_secret)
    return source in ("publisher", "catalog", "own")


__all__ = [
    "ClientSource",
    "SHIPPED_PUBLIC_CLIENT_IDS",
    "is_standard_ready",
    "publisher_secret_names",
    "publisher_secret_names_secret",
    "resolve_publisher_client",
]
