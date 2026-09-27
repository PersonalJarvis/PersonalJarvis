"""Official Discord OAuth2 + bot-install helpers (no secrets in code).

Covers the free, approval-free Discord surface over the browser:

- user identity via the Authorization Code grant
  (``identify`` scope, ``GET /users/@me``);
- bot installation into a guild via the OAuth2 bot flow
  (``bot`` scope + permission bitfield, picked in the browser);
- read-only account context (``guilds``, ``guilds.members.read``,
  ``connections``) through the user's Bearer token.

What this module deliberately does NOT do:

- no bot-token handling: the gateway credential (``Authorization: Bot``)
  is a separate runtime secret managed by
  ``jarvis/marketplace/discord_connect.py`` and never flows through here;
- no approval-gated scopes (``activities.*``, ``dm_channels.read``,
  ``rpc*``, ``voice``, ``relationships.read``) and no self-bot behavior —
  automating a personal user account outside the OAuth2/bot API is against
  the Discord developer terms;
- no reading of arbitrary private messages: only conversations with the
  installed bot (its DMs and guild channels it was added to) are processed.

Sources (official Discord documentation):

- ``https://docs.discord.com/developers/topics/oauth2``
  (authorization code grant, bot authorization flow, advanced bot
  authorization, scopes, token revocation);
- ``https://docs.discord.com/developers/reference#authentication``
  (``Bot`` vs ``Bearer`` authorization schemes);
- ``https://docs.discord.com/developers/events/gateway`` (gateway intents;
  the message-content intent stays an operator setting on the app).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

AUTHORIZATION_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"  # noqa: S105 — official public endpoint URL, not a credential
REVOCATION_URL = "https://discord.com/api/oauth2/token/revoke"
API_BASE = "https://discord.com/api/v10"
CURRENT_USER_URL = f"{API_BASE}/users/@me"
CURRENT_USER_GUILDS_URL = f"{API_BASE}/users/@me/guilds"

# Fixed loopback port for the publisher Discord app's registered redirect
# (``http://127.0.0.1:<port>/oauth/callback``). Discord requires an exact
# redirect_uri match, so — like Slack's 3118 — this must be stable, not
# ephemeral. 3118-3128 are taken by other catalog entries; 3129 is free.
CALLBACK_PORT = 3129
CALLBACK_PATH = "/oauth/callback"

# Placeholder until the publisher registers the shared Discord app. Never a
# real id; the connect route reports 409 with operator steps instead of
# sending this to Discord.
PLACEHOLDER_CLIENT_ID = "REPLACE_WITH_JARVIS_DISCORD_APP_CLIENT_ID"

# Free scopes that need no Discord approval. Requested in the browser login:
#   identify             -> /users/@me (id, username, avatar)
#   guilds               -> /users/@me/guilds (basic guild list)
#   guilds.members.read  -> membership info per guild
#   connections          -> linked third-party accounts
# Deliberately excluded: bot/applications.commands (install UI, handled via
# the invite builder below), webhook.incoming (separate token shape),
# email (unneeded PII), and every approval-gated scope.
BROWSER_SCOPES: tuple[str, ...] = (
    "identify",
    "guilds",
    "guilds.members.read",
    "connections",
)

# Minimal guild permission bitfield for the invite link:
#   View Channel (1 << 10 = 1024) + Send Messages (1 << 11 = 2048)
#   + Read Message History (1 << 16 = 65536) = 68608.
# Request the least privilege that keeps the channel usable; never
# Administrator. Server admins can narrow further per channel.
MINIMAL_BOT_PERMISSIONS = (1 << 10) | (1 << 11) | (1 << 16)

USER_AGENT = "Personal-Jarvis/1.0"
REQUEST_TIMEOUT_SECONDS = 15.0


class DiscordApiError(RuntimeError):
    """Discord call failed. Never carries tokens or response bodies."""


@dataclass(frozen=True, slots=True)
class DiscordIdentity:
    """Verified Discord user identity (from ``GET /users/@me``)."""

    user_id: str
    username: str | None = None
    global_name: str | None = None


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: tuple[str, ...] | list[str] = BROWSER_SCOPES,
    code_challenge: str | None = None,
    prompt: str = "consent",
) -> str:
    """Canonical user-OAuth authorize URL (shape of the official docs).

    The live connect flow is driven by ``PkceLoopbackHandler``; this helper
    keeps one canonical, unit-tested URL shape for docs, invite copy and
    the pre-connect checks.
    """
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "prompt": prompt,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return AUTHORIZATION_URL + "?" + urlencode(params)


def build_bot_invite_url(
    client_id: str,
    *,
    permissions: int = MINIMAL_BOT_PERMISSIONS,
    guild_id: str | None = None,
    disable_guild_select: bool = False,
) -> str:
    """Callback-less bot-install URL (official bot authorization flow).

    The user picks the guild in the browser; Discord adds the bot. No
    ``response_type``/``redirect_uri`` is needed for the pure install. Pass
    ``guild_id`` to preselect a guild the user may add the bot to.
    """
    params: dict[str, str] = {
        "client_id": client_id,
        "scope": "bot",
        "permissions": str(int(permissions)),
    }
    if guild_id:
        params["guild_id"] = guild_id
    if disable_guild_select:
        params["disable_guild_select"] = "true"
    return AUTHORIZATION_URL + "?" + urlencode(params)


async def fetch_current_user(access_token: str) -> DiscordIdentity:
    """Return the verified identity for a user Bearer token.

    Raises :class:`DiscordApiError` without leaking the token. A bot token
    sent as Bearer is rejected by Discord (it needs the ``Bot`` scheme),
    which surfaces here as an API error — by design, the two credentials
    are never interchangeable.
    """
    if not access_token:
        raise DiscordApiError("missing access token")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                CURRENT_USER_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": USER_AGENT,
                },
            )
    except httpx.HTTPError as exc:
        raise DiscordApiError(f"could not reach Discord: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise DiscordApiError(f"Discord rejected the token (HTTP {response.status_code})")
    try:
        payload = response.json()
    except ValueError as exc:
        raise DiscordApiError("Discord returned an unusable response") from exc
    if not isinstance(payload, dict) or not payload.get("id"):
        raise DiscordApiError("Discord returned an unusable response")
    user_id = str(payload["id"])
    if not user_id.isdigit():
        raise DiscordApiError("Discord returned an unusable response")
    username = payload.get("username")
    global_name = payload.get("global_name")
    return DiscordIdentity(
        user_id=user_id,
        username=str(username) if username is not None else None,
        global_name=str(global_name) if global_name is not None else None,
    )
