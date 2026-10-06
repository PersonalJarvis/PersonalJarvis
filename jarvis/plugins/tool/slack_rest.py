"""slack tool — search, read and post in the user's Slack workspace.

Native REST tool over the Slack Web API, acting as the signed-in person with
the user token the marketplace PKCE-loopback flow stores (key
``plugin_slack_tokens``). It replaces the catalog's former binding to Slack's
hosted MCP server: Slack admits only Marketplace-listed or internal apps to
that server, and the shared publisher app is a distributed but unlisted app,
so every end user would have been refused. The Web API accepts the same user
token, so the tool talks to it directly — the pattern ``drive_rest.py`` and
``spotify_rest.py`` already follow.

Three properties of the Web API shape everything below:

* **Failures arrive as HTTP 200.** Every method answers ``{"ok": false,
  "error": "<code>"}``; the status code alone says nothing. Each call checks
  ``ok`` and turns known codes into fixed sentences. The provider's own text
  never reaches a log line, the UI or storage (AP-34).
* **User tokens rotate.** With token rotation (always on for PKCE apps) the
  access token lives 12 hours and an expired one answers ``token_expired``,
  not a 401. That code triggers exactly one refresh through the shared
  refresh path, then the call is retried.
* **Rate limits are per method.** A 429 carries ``Retry-After`` in seconds. A
  short wait is absorbed inline once; a longer one is reported, because a
  voice turn cannot sit out a 30-second penalty.

Per-action risk tiers: every read is ``safe``; ``post_message`` speaks for the
user in front of other people, so it keeps the ``ask`` tier and the two-turn
echo-confirm. A direct gated action, never a spawn (AP-5/AP-14).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_API = "https://slack.com/api"

_NOT_CONNECTED = "Slack is not connected — connect it in the Plugins view."
_NEEDS_RECONNECT = (
    "Slack rejected the stored authorization and it could not be renewed — "
    "reconnect Slack in the Plugins view."
)
_MISSING_SCOPE = (
    "Slack has not granted the permission this needs. Reconnect Slack in the "
    "Plugins view to grant it."
)
_GENERIC_FAILURE = "Slack could not complete the request. Try again in a moment."

# Fixed explanations for the Slack error codes a person can act on. Codes not
# listed fall back to _GENERIC_FAILURE; the provider's text is never echoed.
_ERROR_MESSAGES: dict[str, str] = {
    "not_authed": _NEEDS_RECONNECT,
    "invalid_auth": _NEEDS_RECONNECT,
    "token_expired": _NEEDS_RECONNECT,
    "token_revoked": _NEEDS_RECONNECT,
    "account_inactive": _NEEDS_RECONNECT,
    "missing_scope": _MISSING_SCOPE,
    "no_permission": _MISSING_SCOPE,
    "channel_not_found": (
        "Slack could not find that conversation, or the user is not a member of it."
    ),
    "not_in_channel": "The user is not a member of that channel, so nothing can be posted there.",
    "is_archived": "That channel is archived, so nothing can be posted there.",
    "msg_too_long": "That message is too long for Slack.",
    "no_text": "The message is empty — say what should be posted.",
    "thread_not_found": "Slack could not find that thread.",
    "message_not_found": "Slack could not find that message.",
    "user_not_found": "Slack could not find that person.",
    "invalid_cursor": "That page marker has expired — start the listing again.",
    "restricted_action": "The workspace's settings do not allow this action.",
    "team_access_not_granted": "The workspace has not granted access to this app.",
    "ekm_access_denied": "The workspace's administrators have blocked access to this content.",
}

# Codes a single token refresh can heal (rotation expired the access token).
_REFRESHABLE = frozenset({"token_expired", "invalid_auth", "not_authed"})

# Absorb a rate limit inline only when it fits the voice tool budget.
_MAX_INLINE_RETRY_S = 2.0

_ID_RE = re.compile(r"^[CGD][A-Z0-9]{6,}$")
_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")

_ALL_TYPES = "public_channel,private_channel,mpim,im"
_NAMED_TYPES = "public_channel,private_channel,mpim"
_PAGE_SIZE = 200
_MAX_PAGES = 10
_LIST_DEFAULT = 100
_LIST_MAX = 1000
_TEXT_CAP = 4_000
_POST_MAX_CHARS = 40_000
_USER_LOOKUPS_PER_CALL = 15
_DIRECTORY_TTL_S = 300.0


class SlackApiError(Exception):
    """A Slack ``ok: false`` answer or HTTP refusal, reduced to its code.

    Only the machine code is kept. The string form never contains provider
    text, so an accidental ``str(exc)`` cannot leak a response body."""

    def __init__(self, code: str, *, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class _ToolError(Exception):
    """A failure whose message this module wrote itself (safe to show)."""

    def __init__(self, message: str, *, candidates: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.candidates = candidates


def _default_token_provider() -> str | None:
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("slack")
    return tokens.access if tokens is not None else None


async def _default_refresher(observed_access_token: str | None = None) -> bool:
    """Refresh the stored Slack token in place. Returns True on success.

    On an un-healable failure (revoked grant, a refresh token past Slack's
    30-day PKCE ceiling, a placeholder client) it flags ``needs_reauth`` so
    the Plugins view offers Reconnect instead of a green dot that lies.
    Best-effort: any error returns False and never raises into the tool."""
    from jarvis.marketplace.connect_helpers import build_handler_from_catalog
    from jarvis.marketplace.refresh_scheduler import refresh_plugin_token
    from jarvis.marketplace.token_store import TokenStore

    attempt = await refresh_plugin_token(
        "slack",
        TokenStore(),
        build_handler_from_catalog,
        force=True,
        observed_access_token=observed_access_token,
    )
    return attempt.usable


def _retry_after_seconds(headers: Any) -> float:
    try:
        return max(0.0, float(headers.get("Retry-After", "")))
    except (TypeError, ValueError):
        return 30.0  # Slack always sends it; an unreadable value means "not now"


def _cap(text: Any) -> str:
    value = str(text or "")
    if len(value) > _TEXT_CAP:
        return value[:_TEXT_CAP] + "… [truncated]"
    return value


def _conversation_kind(conv: dict[str, Any]) -> str:
    if conv.get("is_im"):
        return "dm"
    if conv.get("is_mpim"):
        return "group_dm"
    if conv.get("is_private") or conv.get("is_group"):
        return "private_channel"
    return "channel"


def _slim_conversation(conv: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": conv.get("id"),
        "name": conv.get("name"),
        "kind": _conversation_kind(conv),
    }
    if conv.get("is_im"):
        out["user"] = conv.get("user")
    else:
        out["is_member"] = bool(conv.get("is_member"))
        if isinstance(conv.get("num_members"), int):
            out["num_members"] = conv["num_members"]
    return out


def _slim_message(msg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ts": msg.get("ts"),
        "user": msg.get("user") or msg.get("bot_id"),
        "text": _cap(msg.get("text")),
    }
    if msg.get("thread_ts"):
        out["thread_ts"] = msg["thread_ts"]
    if isinstance(msg.get("reply_count"), int):
        out["reply_count"] = msg["reply_count"]
    if msg.get("files"):
        out["files"] = [f.get("name") for f in msg["files"] if isinstance(f, dict)]
    return out


def _slim_user(user: dict[str, Any]) -> dict[str, Any]:
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
    return {
        "id": user.get("id"),
        "name": user.get("name"),
        "real_name": user.get("real_name") or profile.get("real_name"),
        "display_name": profile.get("display_name") or None,
        "title": profile.get("title") or None,
        "tz": user.get("tz"),
        "is_bot": bool(user.get("is_bot")),
        "deleted": bool(user.get("deleted")),
    }


def _display_name(user: dict[str, Any]) -> str:
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
    return str(
        profile.get("display_name")
        or user.get("real_name")
        or profile.get("real_name")
        or user.get("name")
        or user.get("id")
        or ""
    )


def _user_matches(user: dict[str, Any], needle: str) -> bool:
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
    fields = (
        user.get("name"),
        user.get("real_name"),
        profile.get("real_name"),
        profile.get("display_name"),
    )
    return any(needle in str(f).lower() for f in fields if f)


def _user_exact(user: dict[str, Any], needle: str) -> bool:
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
    fields = (
        user.get("name"),
        user.get("real_name"),
        profile.get("real_name"),
        profile.get("display_name"),
    )
    return any(str(f).lower() == needle for f in fields if f)


class SlackRestTool:
    name: str = "slack"
    # Static tier is the conservative one; reads are downgraded per call.
    risk_tier: str = "ask"
    description: str = (
        "Search, read and post in the user's connected Slack workspace, acting "
        "as the user. Use for 'what did Anna say in Slack', 'search Slack for "
        "the release date', 'what's new in #general', 'read that thread', "
        "'post in #team that I'm late'. Actions: search_messages (query, Slack "
        "search syntax works: in:#channel from:@name), list_conversations "
        "(channels, private channels, group DMs and DMs the user belongs to), "
        "read_history (channel by #name, @person for an existing DM, or id), "
        "read_thread (channel + thread_ts), get_user (user id), find_users "
        "(name), post_message (channel + text, optional thread_ts to reply in "
        "a thread). Requires the Slack plugin to be connected in the Plugins "
        "view. Posting needs the user's confirmation."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search_messages",
                    "list_conversations",
                    "read_history",
                    "read_thread",
                    "get_user",
                    "find_users",
                    "post_message",
                ],
                "default": "search_messages",
            },
            "query": {
                "type": "string",
                "description": "search text (search_messages) or a person's name (find_users)",
            },
            "channel": {
                "type": "string",
                "description": (
                    "conversation: '#name', '@person' for an existing DM, or a "
                    "Slack id like C0123ABCD"
                ),
            },
            "thread_ts": {
                "type": "string",
                "description": "parent message ts (read_thread; post_message to reply in a thread)",
            },
            "user": {"type": "string", "description": "Slack user id (get_user)"},
            "text": {"type": "string", "description": "message text (post_message)"},
            "types": {
                "type": "string",
                "description": (
                    "list_conversations filter, comma-separated from "
                    "public_channel, private_channel, mpim, im (default all)"
                ),
            },
            "limit": {"type": "integer", "description": "how many results to return"},
            "cursor": {
                "type": "string",
                "description": "next_cursor from a previous answer, to continue a listing",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        access_token_provider: Callable[[], str | None] | None = None,
        transport: Any | None = None,
        token_refresher: Callable[[], Awaitable[bool]] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        from ._http_pool import HttpClientPool

        self._token_provider = access_token_provider or _default_token_provider
        self._refresher = token_refresher
        self._sleep = sleep or asyncio.sleep
        # Keep-alive pool: resolving a channel name and then reading it is two
        # calls in a row, so one warm TLS connection beats two handshakes.
        self._pool = HttpClientPool(transport=transport)
        self._user_names: dict[str, str] = {}
        self._directory: list[dict[str, Any]] | None = None
        self._directory_at = 0.0

    # -- transport ----------------------------------------------------------

    def _bearer(self) -> dict[str, str] | None:
        token = self._token_provider()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "User-Agent": "Personal-Jarvis/1.0"}

    async def _api(
        self,
        method: str,
        headers: dict[str, str],
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One Web API call. Returns the payload of an ``ok: true`` answer and
        raises :class:`SlackApiError` for everything else."""
        client = self._pool.client()
        url = f"{_API}/{method}"
        for attempt in range(2):
            if json_body is not None:
                resp = await client.post(
                    url,
                    json=json_body,
                    headers={**headers, "Content-Type": "application/json; charset=utf-8"},
                )
            else:
                resp = await client.get(url, params=params or {}, headers=headers)
            if resp.status_code == 429:
                wait = _retry_after_seconds(resp.headers)
                if attempt == 0 and wait <= _MAX_INLINE_RETRY_S:
                    # A 429 means nothing was done, so a retry cannot double-post.
                    await self._sleep(wait)
                    continue
                raise SlackApiError("ratelimited", retry_after=wait)
            if resp.status_code == 401:
                raise SlackApiError("invalid_auth")
            if resp.status_code >= 400:
                raise SlackApiError(f"http_{resp.status_code}")
            try:
                payload = resp.json()
            except ValueError:
                raise SlackApiError("invalid_response") from None
            if not isinstance(payload, dict):
                raise SlackApiError("invalid_response")
            if payload.get("ok") is not True:
                raise SlackApiError(str(payload.get("error") or "unknown"))
            return payload
        raise SlackApiError("ratelimited", retry_after=_MAX_INLINE_RETRY_S)

    async def _with_auth_retry(
        self, do_call: Callable[[dict[str, str]], Awaitable[Any]]
    ) -> Any:
        """Run an authenticated action; on an expired token refresh once and
        retry. With rotation the access token lives 12 hours, so this path is
        ordinary rather than exceptional."""
        headers = self._bearer()
        if headers is None:
            return {"error": _NOT_CONNECTED}
        try:
            return await do_call(headers)
        except SlackApiError as exc:
            if exc.code not in _REFRESHABLE:
                raise
        observed_token = headers["Authorization"].removeprefix("Bearer ")
        try:
            if self._refresher is None:
                refreshed = bool(await _default_refresher(observed_token))
            else:
                refreshed = bool(await self._refresher())
        except Exception:  # noqa: BLE001 — refresher must never crash the tool
            log.warning("slack token refresh raised; asking the user to reconnect")
            refreshed = False
        if not refreshed:
            return {"error": _NEEDS_RECONNECT}
        headers = self._bearer()
        if headers is None:
            return {"error": _NEEDS_RECONNECT}
        return await do_call(headers)

    async def _paged(
        self,
        method: str,
        headers: dict[str, str],
        key: str,
        params: dict[str, Any],
        *,
        want: int,
        cursor: str = "",
        stop: Callable[[list[dict[str, Any]]], bool] | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Follow ``response_metadata.next_cursor`` until ``want`` items, the
        last page, ``stop`` says enough, or the page cap. Returns the items and
        the cursor to continue from ("" when exhausted)."""
        items: list[dict[str, Any]] = []
        next_cursor = cursor
        for _ in range(_MAX_PAGES):
            page_params = {**params, "limit": min(_PAGE_SIZE, max(1, want - len(items)))}
            if next_cursor:
                page_params["cursor"] = next_cursor
            payload = await self._api(method, headers, params=page_params)
            batch = [i for i in (payload.get(key) or []) if isinstance(i, dict)]
            items.extend(batch)
            meta = payload.get("response_metadata")
            next_cursor = str(meta.get("next_cursor") or "") if isinstance(meta, dict) else ""
            # `stop` sees every page, including the last one.
            satisfied = stop(batch) if stop is not None else False
            if satisfied or not next_cursor or len(items) >= want:
                break
        return items[:want], next_cursor

    # -- lookups ------------------------------------------------------------

    async def _users(self, headers: dict[str, str]) -> list[dict[str, Any]]:
        """The workspace directory, cached for a few minutes per tool."""
        now = time.monotonic()
        if self._directory is not None and now - self._directory_at < _DIRECTORY_TTL_S:
            return self._directory
        users, _ = await self._paged(
            "users.list", headers, "members", {}, want=_PAGE_SIZE * _MAX_PAGES
        )
        self._directory = users
        self._directory_at = now
        for user in users:
            if user.get("id"):
                self._user_names[str(user["id"])] = _display_name(user)
        return users

    async def _names_for(self, ids: list[str], headers: dict[str, str]) -> dict[str, str]:
        """Display names for user ids, from cache or ``users.info``.

        Bounded per call. A lookup that fails leaves the raw id in place: the
        answer is still correct, only less friendly, so it is logged and the
        read carries on."""
        lookups = 0
        for uid in dict.fromkeys(i for i in ids if i and _USER_ID_RE.match(i)):
            if uid in self._user_names or lookups >= _USER_LOOKUPS_PER_CALL:
                continue
            lookups += 1
            try:
                payload = await self._api("users.info", headers, params={"user": uid})
            except SlackApiError as exc:
                if exc.code in _REFRESHABLE:
                    raise
                log.debug("slack users.info failed (%s); keeping the raw id", exc.code)
                continue
            user = payload.get("user")
            if isinstance(user, dict):
                self._user_names[uid] = _display_name(user)
        return {uid: self._user_names[uid] for uid in ids if uid in self._user_names}

    async def _with_names(
        self, messages: list[dict[str, Any]], headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        names = await self._names_for([str(m.get("user") or "") for m in messages], headers)
        for message in messages:
            uid = str(message.get("user") or "")
            if uid in names:
                message["user_name"] = names[uid]
        return messages

    async def _find_user(self, needle: str, headers: dict[str, str]) -> dict[str, Any]:
        users = [u for u in await self._users(headers) if not u.get("deleted")]
        exact = [u for u in users if _user_exact(u, needle)]
        hits = exact or [u for u in users if _user_matches(u, needle)]
        if not hits:
            raise _ToolError(f"Slack has nobody called {needle!r}.")
        if len(hits) > 1:
            raise _ToolError(
                f"Several people match {needle!r} — say which one.",
                candidates=[_slim_user(u) for u in hits[:10]],
            )
        return hits[0]

    async def _resolve_channel(self, channel: str, headers: dict[str, str]) -> str:
        """Turn '#name', '@person', a user id or a conversation id into a
        conversation id. Never guesses: no match is an error, several are a
        question back to the user."""
        raw = channel.strip()
        if not raw:
            raise _ToolError("Say which channel or person.")
        if _ID_RE.match(raw):
            return raw
        if raw.startswith("@") or _USER_ID_RE.match(raw):
            return await self._resolve_dm(raw, headers)
        name = raw.lstrip("#").strip().lower()
        found: list[dict[str, Any]] = []

        def _stop(batch: list[dict[str, Any]]) -> bool:
            found.extend(
                c
                for c in batch
                if str(c.get("name") or "").lower() == name
                or str(c.get("name_normalized") or "").lower() == name
            )
            return bool(found)

        await self._paged(
            "conversations.list",
            headers,
            "channels",
            {"types": _NAMED_TYPES, "exclude_archived": "true"},
            want=_PAGE_SIZE * _MAX_PAGES,
            stop=_stop,
        )
        if not found:
            raise _ToolError(
                f"Slack has no channel called #{name} that the user belongs to."
            )
        return str(found[0]["id"])

    async def _resolve_dm(self, raw: str, headers: dict[str, str]) -> str:
        target = raw.lstrip("@").strip()
        if _USER_ID_RE.match(target):
            uid = target
            label = target
        else:
            user = await self._find_user(target.lower(), headers)
            uid = str(user.get("id"))
            label = _display_name(user)
        found: list[str] = []

        def _stop(batch: list[dict[str, Any]]) -> bool:
            found.extend(str(c["id"]) for c in batch if c.get("user") == uid and c.get("id"))
            return bool(found)

        await self._paged(
            "conversations.list",
            headers,
            "channels",
            {"types": "im"},
            want=_PAGE_SIZE * _MAX_PAGES,
            stop=_stop,
        )
        if not found:
            raise _ToolError(
                f"There is no direct message with {label} yet. Slack only lets "
                "Jarvis use direct messages that already exist."
            )
        return found[0]

    # -- public actions (also directly unit-testable) -----------------------

    async def search_messages(
        self, *, query: str, limit: int = 10, page: int = 1
    ) -> dict[str, Any]:
        count = max(1, min(int(limit), 50))

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            payload = await self._api(
                "search.messages",
                headers,
                params={
                    "query": query,
                    "count": count,
                    "page": max(1, int(page)),
                    "sort": "timestamp",
                    "sort_dir": "desc",
                },
            )
            block = payload.get("messages") if isinstance(payload.get("messages"), dict) else {}
            matches = [m for m in (block.get("matches") or []) if isinstance(m, dict)]
            results = []
            for match in matches:
                chan = match.get("channel") if isinstance(match.get("channel"), dict) else {}
                results.append(
                    {
                        "channel_id": chan.get("id"),
                        "channel": chan.get("name"),
                        "user": match.get("user"),
                        "user_name": match.get("username"),
                        "text": _cap(match.get("text")),
                        "ts": match.get("ts"),
                        "permalink": match.get("permalink"),
                    }
                )
            paging = block.get("paging") if isinstance(block.get("paging"), dict) else {}
            return {
                "query": query,
                "total": block.get("total", len(results)),
                "page": paging.get("page", page),
                "pages": paging.get("pages"),
                "messages": results,
            }

        return await self._with_auth_retry(_do)

    async def list_conversations(
        self, *, types: str = "", limit: int = _LIST_DEFAULT, cursor: str = ""
    ) -> dict[str, Any]:
        allowed = {"public_channel", "private_channel", "mpim", "im"}
        wanted = [t.strip() for t in (types or _ALL_TYPES).split(",") if t.strip() in allowed]
        want = max(1, min(int(limit), _LIST_MAX))

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            convs, next_cursor = await self._paged(
                "conversations.list",
                headers,
                "channels",
                {"types": ",".join(wanted or [_ALL_TYPES]), "exclude_archived": "true"},
                want=want,
                cursor=cursor,
            )
            slim = [_slim_conversation(c) for c in convs]
            dm_users = [str(c.get("user") or "") for c in slim if c["kind"] == "dm"]
            names = await self._names_for(dm_users, headers)
            for conv in slim:
                if conv["kind"] == "dm" and conv.get("user") in names:
                    conv["name"] = names[conv["user"]]
            out: dict[str, Any] = {"conversations": slim, "count": len(slim)}
            if next_cursor:
                out["next_cursor"] = next_cursor
            return out

        return await self._with_auth_retry(_do)

    async def read_history(
        self, *, channel: str, limit: int = 20, cursor: str = ""
    ) -> dict[str, Any]:
        count = max(1, min(int(limit), 100))

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            channel_id = await self._resolve_channel(channel, headers)
            params: dict[str, Any] = {"channel": channel_id, "limit": count}
            if cursor:
                params["cursor"] = cursor
            payload = await self._api("conversations.history", headers, params=params)
            raw = [m for m in (payload.get("messages") or []) if isinstance(m, dict)]
            messages = await self._with_names([_slim_message(m) for m in raw], headers)
            meta = payload.get("response_metadata")
            out: dict[str, Any] = {
                "channel_id": channel_id,
                "order": "newest first",
                "messages": messages,
            }
            next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else ""
            if payload.get("has_more") and next_cursor:
                out["next_cursor"] = next_cursor
            return out

        return await self._with_auth_retry(_do)

    async def read_thread(
        self, *, channel: str, thread_ts: str, limit: int = 50, cursor: str = ""
    ) -> dict[str, Any]:
        count = max(1, min(int(limit), 200))

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            channel_id = await self._resolve_channel(channel, headers)
            params: dict[str, Any] = {"channel": channel_id, "ts": thread_ts, "limit": count}
            if cursor:
                params["cursor"] = cursor
            payload = await self._api("conversations.replies", headers, params=params)
            raw = [m for m in (payload.get("messages") or []) if isinstance(m, dict)]
            messages = await self._with_names([_slim_message(m) for m in raw], headers)
            meta = payload.get("response_metadata")
            out: dict[str, Any] = {
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "order": "oldest first",
                "messages": messages,
            }
            next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else ""
            if payload.get("has_more") and next_cursor:
                out["next_cursor"] = next_cursor
            return out

        return await self._with_auth_retry(_do)

    async def get_user(self, *, user: str) -> dict[str, Any]:
        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            payload = await self._api("users.info", headers, params={"user": user.strip()})
            raw = payload.get("user")
            if not isinstance(raw, dict):
                raise SlackApiError("user_not_found")
            return _slim_user(raw)

        return await self._with_auth_retry(_do)

    async def find_users(self, *, query: str, limit: int = 10) -> dict[str, Any]:
        needle = query.strip().lstrip("@").lower()

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            users = [u for u in await self._users(headers) if not u.get("deleted")]
            hits = [u for u in users if _user_matches(u, needle)]
            capped = hits[: max(1, min(int(limit), 50))]
            return {"query": query, "users": [_slim_user(u) for u in capped]}

        return await self._with_auth_retry(_do)

    async def post_message(
        self, *, channel: str, text: str, thread_ts: str = ""
    ) -> dict[str, Any]:
        if not text.strip():
            return {"error": _ERROR_MESSAGES["no_text"]}
        if len(text) > _POST_MAX_CHARS:
            return {"error": _ERROR_MESSAGES["msg_too_long"]}

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            channel_id = await self._resolve_channel(channel, headers)
            body: dict[str, Any] = {"channel": channel_id, "text": text}
            if thread_ts.strip():
                body["thread_ts"] = thread_ts.strip()
            payload = await self._api("chat.postMessage", headers, json_body=body)
            return {
                "posted": True,
                "channel_id": payload.get("channel") or channel_id,
                "ts": payload.get("ts"),
                "thread_ts": body.get("thread_ts"),
            }

        return await self._with_auth_retry(_do)

    # -- Tool protocol ------------------------------------------------------

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        """Per-action risk tier (consulted by ``RiskTierEvaluator``).

        Reads are ``safe``. ``post_message`` speaks for the user in front of
        colleagues and cannot be taken back unnoticed, so it keeps ``ask`` and
        the two-turn echo-confirm. An unknown action stays ``ask``."""
        action = str(args.get("action") or "search_messages").strip()
        if action in (
            "search_messages",
            "list_conversations",
            "read_history",
            "read_thread",
            "get_user",
            "find_users",
        ):
            return "safe"
        return "ask"

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = str(args.get("action") or "search_messages").strip()
        query = str(args.get("query") or "").strip()
        channel = str(args.get("channel") or "").strip()
        cursor = str(args.get("cursor") or "").strip()
        thread_ts = str(args.get("thread_ts") or "").strip()
        try:
            limit = int(args["limit"]) if args.get("limit") is not None else None
        except (TypeError, ValueError):  # the model gets the reason in the tool result
            return ToolResult(success=False, output=None, error="limit must be a number")
        try:
            if action == "search_messages":
                if not query:
                    return _missing("search_messages needs a query")
                out = await self.search_messages(query=query, limit=limit or 10)
            elif action == "list_conversations":
                out = await self.list_conversations(
                    types=str(args.get("types") or ""),
                    limit=limit or _LIST_DEFAULT,
                    cursor=cursor,
                )
            elif action == "read_history":
                if not channel:
                    return _missing("read_history needs a channel")
                out = await self.read_history(channel=channel, limit=limit or 20, cursor=cursor)
            elif action == "read_thread":
                if not channel or not thread_ts:
                    return _missing("read_thread needs a channel and thread_ts")
                out = await self.read_thread(
                    channel=channel, thread_ts=thread_ts, limit=limit or 50, cursor=cursor
                )
            elif action == "get_user":
                user = str(args.get("user") or "").strip()
                if not user:
                    return _missing("get_user needs a user id")
                out = await self.get_user(user=user)
            elif action == "find_users":
                if not query:
                    return _missing("find_users needs a query")
                out = await self.find_users(query=query, limit=limit or 10)
            elif action == "post_message":
                if not channel:
                    return _missing("post_message needs a channel")
                out = await self.post_message(
                    channel=channel, text=str(args.get("text") or ""), thread_ts=thread_ts
                )
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except _ToolError as exc:  # the error goes back to the model as the tool result
            if exc.candidates:
                return ToolResult(
                    success=False,
                    output={"candidates": exc.candidates},
                    error=exc.message,
                )
            return ToolResult(success=False, output=None, error=exc.message)
        except Exception as exc:  # noqa: BLE001 — every failure reaches the model via _explain()
            return ToolResult(success=False, output=None, error=self._explain(exc, action))

        if isinstance(out, dict) and out.get("error"):
            return ToolResult(success=False, output=None, error=out["error"])
        return ToolResult(success=True, output=out)

    @staticmethod
    def _explain(exc: Exception, action: str) -> str:
        """Turn a transport or API failure into a fixed sentence a person can
        act on. Logs only the action and the machine code, never a body."""
        import httpx

        if isinstance(exc, SlackApiError):
            log.info("slack %s failed: %s", action, exc.code)
            if exc.code == "ratelimited":
                wait = int(exc.retry_after or 30)
                return f"Slack is rate-limiting these requests — try again in {wait} seconds."
            if exc.code.startswith("http_5"):
                return "Slack is having trouble right now. Try again shortly."
            return _ERROR_MESSAGES.get(exc.code, _GENERIC_FAILURE)
        if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
            return "Could not reach Slack. Check this machine's internet connection."
        if isinstance(exc, httpx.TimeoutException):
            return "Slack did not answer in time. Try again in a moment."
        log.warning("slack %s failed with %s", action, type(exc).__name__)
        return _GENERIC_FAILURE


def _missing(message: str) -> ToolResult:
    return ToolResult(success=False, output=None, error=message)
