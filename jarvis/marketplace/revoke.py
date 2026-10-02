"""Best-effort token revocation at the provider on disconnect.

Removing a plugin used to delete only the LOCAL token: the authorization stayed
alive in the user's account at the provider, showing "Personal Jarvis" as a
connected app forever. `revocation_url` has been in the catalog schema (and
populated for Google, Slack and Asana) since the beginning without anything ever
reading it.

Deliberately best-effort and non-blocking for the disconnect itself. Removing
the local credential is the part the user asked for and it must always succeed;
a provider that is unreachable, has already dropped the grant, or does not
implement RFC 7009 must not turn "disconnect" into an error. The outcome is
reported back so the UI can tell the user honestly whether they still need to
visit the provider's own connected-apps page.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from jarvis.marketplace.catalog import (
    OAuthDeviceFlowAuth,
    OAuthPkceLoopbackAuth,
    PluginSpec,
)
from jarvis.marketplace.token_store import Tokens, TokenStore

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 6.0

# Revocation endpoints that end the grant for the whole OAuth project, not just
# the token presented. Google documents it: "Revocation removes all OAuth 2.0
# scopes previously granted to a project, invalidating any issued access or
# refresh tokens for all clients registered under that project." Gmail, Drive,
# Calendar, YouTube Music, YouTube Studio and Google Cloud share one client, so
# revoking on one plugin's disconnect silently killed every other Google plugin.
_PROJECT_WIDE_REVOCATION_HOSTS = frozenset({"oauth2.googleapis.com", "accounts.google.com"})
# Google client ids are "<project number>-<id>.apps.googleusercontent.com"; the
# project number is what a project-wide revocation is scoped to.
_GOOGLE_CLIENT_SUFFIX = ".apps.googleusercontent.com"

# Outcome when revocation was deliberately skipped because another connection
# still depends on the same provider grant.
REVOCATION_SHARED = "shared"


def revocation_url(spec: PluginSpec) -> str | None:
    """The provider's RFC 7009 endpoint for this plugin, if it declares one."""
    auth = spec.auth
    if isinstance(auth, OAuthPkceLoopbackAuth):
        return auth.revocation_url
    if isinstance(auth, OAuthDeviceFlowAuth):
        return getattr(auth, "revocation_url", None)
    # DCR plugins learn their revocation endpoint from discovery at connect
    # time; it is persisted with the tokens rather than in the catalog.
    return None


def revokes_whole_project(endpoint: str) -> bool:
    """Whether revoking at ``endpoint`` ends every grant of the OAuth project."""
    return (urlparse(endpoint).hostname or "").lower() in _PROJECT_WIDE_REVOCATION_HOSTS


def _grant_owner(tokens: Tokens) -> str | None:
    """The OAuth project (Google) or client a stored grant belongs to."""
    client_id = tokens.extra.get("client_id")
    if not client_id:
        return None
    if client_id.endswith(_GOOGLE_CLIENT_SUFFIX) and "-" in client_id:
        return "project:" + client_id.split("-", 1)[0]
    return "client:" + client_id


def _shares_grant(plugin_id: str, tokens: Tokens, other_id: str, other: Tokens) -> bool:
    owner, other_owner = _grant_owner(tokens), _grant_owner(other)
    if owner and other_owner:
        return owner == other_owner
    # A grant stored before Jarvis persisted its issuing client: fall back to
    # the secret family both plugins resolve their OAuth client from.
    from jarvis.marketplace.connect_helpers import oauth_client_family

    family = oauth_client_family(plugin_id)
    return family is not None and family == oauth_client_family(other_id)


def shared_grant_holders(plugin_id: str, tokens: Tokens, store: TokenStore) -> list[str]:
    """Other connected plugins whose grant a project-wide revocation would end.

    Every stored connection counts, flagged ones included: a reconnect flag can
    be wrong (the scheduler retries it daily), and Jarvis must never destroy a
    grant it still holds just because one sibling was removed.
    """
    from jarvis.marketplace.catalog_data import load_catalog

    holders: list[str] = []
    for spec in load_catalog().plugins:
        if spec.id == plugin_id:
            continue
        try:
            other = store.load(spec.id)
        except Exception:  # noqa: BLE001 - an unreadable sibling is still a holder
            log.info("plugin %s token unreadable; treating it as a shared holder", spec.id)
            holders.append(spec.id)
            continue
        if other is not None and _shares_grant(plugin_id, tokens, spec.id, other):
            holders.append(spec.id)
    return holders


async def revoke_unless_shared(
    spec: PluginSpec,
    tokens: Tokens,
    store: TokenStore,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Revoke at the provider unless that would end other plugins' grants too.

    Returns :data:`REVOCATION_SHARED` when the provider revokes per project and
    another stored connection belongs to the same project; the last plugin of
    the project to disconnect then revokes the grant for all of them.
    """
    endpoint = revocation_url(spec) or tokens.extra.get("revocation_endpoint")
    if endpoint and revokes_whole_project(endpoint):
        holders = shared_grant_holders(spec.id, tokens, store)
        if holders:
            log.info(
                "plugin %s: provider revocation skipped; it would also end the grant of %s",
                spec.id,
                ", ".join(holders),
            )
            return REVOCATION_SHARED
    return await revoke_tokens(spec, tokens, transport=transport)


async def revoke_tokens(
    spec: PluginSpec,
    tokens: Tokens,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Ask the provider to drop the grant. Returns a short outcome word.

    ``revoked``     - the provider accepted the request
    ``unsupported`` - no endpoint is known for this plugin
    ``failed``      - we tried and the provider refused or was unreachable
    """
    endpoint = revocation_url(spec) or tokens.extra.get("revocation_endpoint")
    if not endpoint:
        return "unsupported"

    # Revoking the REFRESH token invalidates the whole grant at every provider
    # that follows RFC 7009 §2.1; revoking only the access token would leave the
    # grant renewable. Fall back to the access token when no refresh exists
    # (a PAT-style or non-refreshable grant).
    token = tokens.refresh or tokens.access
    if not token:
        return "unsupported"

    body = {
        "token": token,
        "token_type_hint": "refresh_token" if tokens.refresh else "access_token",
    }
    client_id = tokens.extra.get("client_id")
    if client_id:
        body["client_id"] = client_id
    try:
        from jarvis.marketplace.auth.oauth_dcr import apply_client_auth

        auth = None
        client_secret = tokens.extra.get("client_secret")
        if client_id:
            auth = apply_client_auth(
                body,
                client_id,
                client_secret,
                tokens.extra.get(
                    "token_endpoint_auth_method", "client_secret_post" if client_secret else "none"
                ),
            )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_SECONDS), transport=transport
        ) as client:
            response = await client.post(
                endpoint,
                data=body,
                auth=auth,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Personal-Jarvis/1.0",
                },
            )
    except Exception:  # noqa: BLE001 - the local disconnect still stands
        log.info("plugin %s revocation could not be delivered", spec.id)
        return "failed"

    # RFC 7009 §2.2: a server returns 200 for a successful revocation AND for a
    # token it does not recognize — an already-dead grant is the outcome we
    # wanted anyway. Some providers answer 204.
    if response.status_code in (200, 204):
        return "revoked"
    log.info(
        "plugin %s revocation refused: HTTP %s",
        spec.id,
        response.status_code,
    )
    return "failed"
