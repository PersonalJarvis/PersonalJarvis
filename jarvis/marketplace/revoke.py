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

import httpx

from jarvis.marketplace.catalog import (
    OAuthDeviceFlowAuth,
    OAuthPkceLoopbackAuth,
    PluginSpec,
)
from jarvis.marketplace.token_store import Tokens

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 6.0


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
    client_secret = tokens.extra.get("client_secret")
    if client_secret:
        body["client_secret"] = client_secret

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_SECONDS), transport=transport
        ) as client:
            response = await client.post(
                endpoint,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Personal-Jarvis/1.0",
                },
            )
    except Exception as exc:  # noqa: BLE001 - the local disconnect still stands
        log.info("plugin %s revocation could not be delivered: %s", spec.id, exc)
        return "failed"

    # RFC 7009 §2.2: a server returns 200 for a successful revocation AND for a
    # token it does not recognize — an already-dead grant is the outcome we
    # wanted anyway. Some providers answer 204.
    if response.status_code in (200, 204):
        return "revoked"
    log.info(
        "plugin %s revocation refused: HTTP %s %s",
        spec.id,
        response.status_code,
        response.text[:120],
    )
    return "failed"
