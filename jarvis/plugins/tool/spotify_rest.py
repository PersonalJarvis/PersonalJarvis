"""spotify tool — play, steer and read the user's Spotify from voice or chat.

Native REST tool rather than MCP, for the same reason as the other marketplace
connectors: Spotify publishes no MCP server and no agent integration at all —
only the Web API. What community MCP servers exist want a plaintext
``spotify-config.json`` holding a client secret plus a terminal ``npm run auth``,
which trades this repo's keyring-backed connect button for a worse flow (AP-2).

Three properties of Spotify's API shape everything below:

* **The Web API produces no audio.** It drives Spotify Connect, so it can only
  command a device that already runs a Spotify client. With nothing open, every
  playback call answers 404 ``NO_ACTIVE_DEVICE`` — a state this tool resolves
  (one device: adopt it) or reports plainly, never retries blindly.
* **Controlling playback requires Premium.** Spotify answers 403 for a free
  account on play/pause/skip/volume. Reading stays open to everyone, so a free
  listener still gets "what is playing" instead of a dead plugin.
* **Answers are frequently 204 with an empty body.** Every player command
  returns no content on success, so nothing here may parse a response body it
  did not check for first.

Risk tier ``monitor``: starting music is audible and instantly reversible, so it
is audited but never prompts. Unlike Home Assistant, nothing here can unlock a
door. A direct gated action, never a spawn (AP-5/AP-14).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

_API = "https://api.spotify.com/v1"

_NOT_CONNECTED = "Spotify is not connected — connect it in the Plugins view."
_NEEDS_RECONNECT = (
    "Spotify rejected the stored authorization. Reconnect Spotify in the "
    "Plugins view."
)
_NEEDS_PREMIUM = (
    "Spotify only allows remote control with Premium — that is Spotify's rule, "
    "not a limit of this app. Reading what is playing still works."
)
_NO_DEVICE = (
    "Spotify is not open anywhere, so there is nothing to play on. Open Spotify "
    "on this machine, a phone or a speaker and try again."
)

# Spotify's Development Mode caps search at 10 results (default 5). Asking for
# more is a 400, not a silent trim, so the ceiling lives here rather than in the
# caller's head.
_SEARCH_MAX = 10

# What `play` accepts. A track plays as a `uris` list; everything else is a
# context (album/artist/playlist) — Spotify rejects the wrong one with a 400.
_CONTEXT_TYPES = ("album", "artist", "playlist")


def _default_token_provider() -> str | None:
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("spotify")
    return tokens.access if tokens is not None else None


async def _default_refresher(observed_access_token: str | None = None) -> bool:
    """Refresh the stored Spotify token in place. Returns True on success.

    On an un-healable failure (revoked grant, placeholder client id, a refresh
    token past Spotify's six-month ceiling) it flags ``needs_reauth`` so the
    Plugins view stops showing a green "connected" that lies. Best-effort: any
    error returns False and never raises into the tool."""
    from jarvis.marketplace.connect_helpers import build_handler_from_catalog
    from jarvis.marketplace.refresh_scheduler import refresh_plugin_token
    from jarvis.marketplace.token_store import TokenStore

    attempt = await refresh_plugin_token(
        "spotify",
        TokenStore(),
        build_handler_from_catalog,
        force=True,
        observed_access_token=observed_access_token,
    )
    return attempt.usable


def _preference_hint_for(service_id: str) -> str:
    """One sentence about ``[music] preferred_service`` for the tool
    description — cached in ``jarvis.core.music_service`` so a router turn
    never re-parses the config. Never raises (a description must not)."""
    try:
        from jarvis.core.music_service import description_hint

        return description_hint(service_id)
    except Exception:  # noqa: BLE001 — a fault means no hint, not no tool
        return ""
def _artists(item: dict[str, Any]) -> str:
    names = [a.get("name") for a in item.get("artists") or [] if a.get("name")]
    return ", ".join(n for n in names if n)


def _slim_item(item: dict[str, Any]) -> dict[str, Any]:
    """Project a search hit to what a spoken answer needs: who, what, and the
    URI needed to actually play it. The raw object carries images, markets and
    external ids that would bury the answer and blow the prompt budget."""
    out: dict[str, Any] = {
        "name": item.get("name"),
        "uri": item.get("uri"),
        "type": item.get("type"),
    }
    if artists := _artists(item):
        out["artist"] = artists
    album = item.get("album")
    if isinstance(album, dict) and album.get("name"):
        out["album"] = album["name"]
    if (owner := item.get("owner")) and isinstance(owner, dict):
        out["owner"] = owner.get("display_name")
    if isinstance(item.get("total_tracks"), int):
        out["total_tracks"] = item["total_tracks"]
    return out


def _slim_device(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": device.get("id"),
        "name": device.get("name"),
        "type": device.get("type"),
        "is_active": bool(device.get("is_active")),
        "volume_percent": device.get("volume_percent"),
    }


def _summarize_playback(state: dict[str, Any]) -> dict[str, Any]:
    """Reduce the playback state object to what is worth reading aloud."""
    item = state.get("item") or {}
    out: dict[str, Any] = {
        "is_playing": bool(state.get("is_playing")),
        "track": item.get("name"),
        "uri": item.get("uri"),
    }
    if artists := _artists(item):
        out["artist"] = artists
    album = item.get("album")
    if isinstance(album, dict) and album.get("name"):
        out["album"] = album["name"]
    device = state.get("device")
    if isinstance(device, dict):
        out["device"] = device.get("name")
        out["volume_percent"] = device.get("volume_percent")
    return out


class SpotifyRestTool:
    name: str = "spotify"
    risk_tier: str = "monitor"
    _BASE_DESCRIPTION: str = (
        "Play and control the user's Spotify: start a song, artist, album or "
        "playlist, pause, skip, go back, set the volume, queue something up, or "
        "say what is currently playing. Use for 'play Radiohead', 'skip this "
        "one', 'what song is this', 'pause the music', 'turn the music down', "
        "'play my running playlist'. Actions: now_playing, play, pause, next, "
        "previous, set_volume, queue, search, list_devices, list_playlists. "
        "Requires the Spotify plugin to be connected in the Plugins view; "
        "controlling playback needs Spotify Premium."
    )

    @property
    def description(self) -> str:
        """The static description plus the user's music preference (see
        ``jarvis.core.music_service``): with YouTube Music also connected, the
        LLM router must send an unnamed music request where the setting says."""
        return self._BASE_DESCRIPTION + _preference_hint_for(self.name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "now_playing",
                    "play",
                    "pause",
                    "next",
                    "previous",
                    "set_volume",
                    "queue",
                    "search",
                    "list_devices",
                    "list_playlists",
                ],
                "default": "now_playing",
            },
            "query": {
                "type": "string",
                "description": (
                    "what to play, queue or search for, e.g. 'Radiohead', "
                    "'Bohemian Rhapsody', 'my running playlist'. Leave out on "
                    "play to simply resume what is paused."
                ),
            },
            "type": {
                "type": "string",
                "enum": ["track", "album", "artist", "playlist"],
                "default": "track",
                "description": "what the query names",
            },
            "device_id": {
                "type": "string",
                "description": "play on a specific device (from list_devices)",
            },
            "volume_percent": {
                "type": "integer",
                "description": "0-100, for set_volume",
            },
            "limit": {"type": "integer", "default": 5, "description": "search hits, max 10"},
        },
        "required": ["action"],
    }

    def __init__(
        self,
        access_token_provider: Callable[[], str | None] | None = None,
        transport: Any | None = None,
        token_refresher: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        from ._http_pool import HttpClientPool

        self._token_provider = access_token_provider or _default_token_provider
        self._refresher = token_refresher
        # Keep-alive pool: a play is search + devices + play in a row, so one
        # warm TLS connection beats three handshakes.
        self._pool = HttpClientPool(transport=transport)

    # -- internal helpers ---------------------------------------------------

    def _bearer(self) -> dict[str, str] | None:
        token = self._token_provider()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "User-Agent": "Personal-Jarvis/1.0"}

    async def _with_auth_retry(
        self, do_call: Callable[[dict[str, str]], Awaitable[Any]]
    ) -> Any:
        """Run an authenticated call; on a 401 refresh once and retry.

        Spotify access tokens live exactly one hour, so this path is ordinary
        rather than exceptional — without it every session longer than an hour
        would hand the user an auth error for a connection that is perfectly
        healthy."""
        import httpx

        headers = self._bearer()
        if headers is None:
            return {"error": _NOT_CONNECTED}
        try:
            return await do_call(headers)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
        observed_token = headers["Authorization"].removeprefix("Bearer ")
        try:
            if self._refresher is None:
                refreshed = bool(await _default_refresher(observed_token))
            else:
                refreshed = bool(await self._refresher())
        except Exception:  # noqa: BLE001 — refresher must never crash the tool
            refreshed = False
        if not refreshed:
            return {"error": _NEEDS_RECONNECT}
        headers = self._bearer()
        if headers is None:
            return {"error": _NEEDS_RECONNECT}
        return await do_call(headers)

    async def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """One Spotify call. Returns parsed JSON, or ``None`` for the empty
        204 body every successful player command answers with."""
        client = self._pool.client()
        resp = await client.request(
            method,
            f"{_API}{path}",
            params=params,
            json=json_body,
            headers=headers,
        )
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    @staticmethod
    def _reason(exc: Any) -> str:
        """Spotify's machine-readable failure reason, e.g. NO_ACTIVE_DEVICE.

        The body is `{"error": {"status", "message", "reason"}}` — but only on
        player endpoints, and only sometimes, so every step is guarded."""
        try:
            payload = exc.response.json()
        except Exception:  # noqa: BLE001 — a non-JSON error body is still an error
            return ""
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return str(error.get("reason") or "")
        return ""

    async def _devices(self, headers: dict[str, str]) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/me/player/devices", headers)
        devices = (payload or {}).get("devices")
        return [d for d in devices if isinstance(d, dict)] if isinstance(devices, list) else []

    async def _player_command(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """A playback command, with the two failures Spotify actually produces
        turned into something a person can act on.

        ``NO_ACTIVE_DEVICE`` is the common one and is worth healing rather than
        reporting: if the user has exactly one Spotify client open, that is
        unambiguously the one they meant, so the command is re-sent aimed at it.
        With several open, guessing would start music in the wrong room, so the
        list goes back to the user instead."""
        import httpx

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            query = dict(params or {})
            if device_id:
                query["device_id"] = device_id
            try:
                await self._request(
                    method, path, headers, params=query or None, json_body=json_body
                )
                return {"ok": True, "device_id": device_id}
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 403:
                    return {"error": _NEEDS_PREMIUM}
                if status != 404 or self._reason(exc) != "NO_ACTIVE_DEVICE":
                    raise
            # 404 NO_ACTIVE_DEVICE — nothing is listening yet.
            if device_id:
                return {"error": _NO_DEVICE}
            open_devices = await self._devices(headers)
            if not open_devices:
                return {"error": _NO_DEVICE}
            if len(open_devices) > 1:
                return {
                    "error": (
                        "Spotify is idle on several devices — say which one, or "
                        "start playback there once."
                    ),
                    "devices": [_slim_device(d) for d in open_devices],
                }
            target = open_devices[0]
            query = dict(params or {})
            query["device_id"] = target.get("id")
            try:
                await self._request(
                    method, path, headers, params=query, json_body=json_body
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 403:
                    return {"error": _NEEDS_PREMIUM}
                raise
            return {"ok": True, "device": target.get("name"), "device_id": target.get("id")}

        return await self._with_auth_retry(_do)

    # -- public actions (also directly unit-testable) -----------------------

    async def now_playing(self) -> dict[str, Any]:
        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            state = await self._request("GET", "/me/player", headers)
            # 204: Spotify is connected but nothing is loaded anywhere.
            if not state:
                return {"is_playing": False, "message": "Nothing is playing on Spotify."}
            return _summarize_playback(state)

        return await self._with_auth_retry(_do)

    async def search(
        self, *, query: str, item_type: str = "track", limit: int = 5
    ) -> dict[str, Any]:
        capped = max(1, min(int(limit), _SEARCH_MAX))

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            payload = await self._request(
                "GET",
                "/search",
                headers,
                params={"q": query, "type": item_type, "limit": capped},
            )
            bucket = (payload or {}).get(f"{item_type}s") or {}
            items = [i for i in (bucket.get("items") or []) if isinstance(i, dict)]
            return {
                "query": query,
                "type": item_type,
                "results": [_slim_item(i) for i in items],
            }

        return await self._with_auth_retry(_do)

    async def play(
        self,
        *,
        query: str = "",
        item_type: str = "track",
        device_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] | None = None
        started: dict[str, Any] | None = None

        if query:
            found = await self.search(query=query, item_type=item_type, limit=1)
            if found.get("error"):
                return found
            results = found.get("results") or []
            if not results:
                return {"error": f"Spotify has nothing called {query!r}."}
            started = results[0]
            uri = started.get("uri")
            if not uri:
                return {"error": f"Spotify returned no playable id for {query!r}."}
            body = (
                {"context_uri": uri}
                if item_type in _CONTEXT_TYPES
                else {"uris": [uri]}
            )

        out = await self._player_command(
            "PUT", "/me/player/play", json_body=body, device_id=device_id
        )
        if out.get("error"):
            return out
        # Report what the SERVER said is playable, not what the model asked for.
        # Spotify needs a moment before /me/player reflects the new track, so
        # reading it straight back would report the previous song as the result.
        if started:
            out["started"] = started
        else:
            out["resumed"] = True
        return out

    async def pause(self) -> dict[str, Any]:
        return await self._player_command("PUT", "/me/player/pause")

    async def next_track(self) -> dict[str, Any]:
        return await self._player_command("POST", "/me/player/next")

    async def previous_track(self) -> dict[str, Any]:
        return await self._player_command("POST", "/me/player/previous")

    async def set_volume(self, *, volume_percent: int) -> dict[str, Any]:
        level = max(0, min(int(volume_percent), 100))
        out = await self._player_command(
            "PUT", "/me/player/volume", params={"volume_percent": level}
        )
        if not out.get("error"):
            out["volume_percent"] = level
        return out

    async def queue(self, *, query: str, item_type: str = "track") -> dict[str, Any]:
        found = await self.search(query=query, item_type=item_type, limit=1)
        if found.get("error"):
            return found
        results = found.get("results") or []
        if not results:
            return {"error": f"Spotify has nothing called {query!r}."}
        item = results[0]
        out = await self._player_command(
            "POST", "/me/player/queue", params={"uri": item.get("uri")}
        )
        if not out.get("error"):
            out["queued"] = item
        return out

    async def list_devices(self) -> dict[str, Any]:
        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            devices = await self._devices(headers)
            return {"devices": [_slim_device(d) for d in devices]}

        return await self._with_auth_retry(_do)

    async def list_playlists(self, *, limit: int = 20) -> dict[str, Any]:
        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            payload = await self._request(
                "GET", "/me/playlists", headers, params={"limit": max(1, min(limit, 50))}
            )
            items = [i for i in ((payload or {}).get("items") or []) if isinstance(i, dict)]
            return {"playlists": [_slim_item(i) for i in items]}

        return await self._with_auth_retry(_do)

    # -- Tool protocol ------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "now_playing").strip()
        query = (args.get("query") or "").strip()
        item_type = (args.get("type") or "track").strip() or "track"
        try:
            if action == "now_playing":
                out = await self.now_playing()
            elif action == "play":
                out = await self.play(
                    query=query, item_type=item_type, device_id=args.get("device_id")
                )
            elif action == "pause":
                out = await self.pause()
            elif action == "next":
                out = await self.next_track()
            elif action == "previous":
                out = await self.previous_track()
            elif action == "set_volume":
                level = args.get("volume_percent")
                if level is None:
                    return ToolResult(
                        success=False, output=None, error="volume_percent missing"
                    )
                out = await self.set_volume(volume_percent=int(level))
            elif action == "queue":
                if not query:
                    return ToolResult(
                        success=False, output=None, error="queue needs a query"
                    )
                out = await self.queue(query=query, item_type=item_type)
            elif action == "search":
                if not query:
                    return ToolResult(
                        success=False, output=None, error="search needs a query"
                    )
                out = await self.search(
                    query=query, item_type=item_type, limit=int(args.get("limit", 5))
                )
            elif action == "list_devices":
                out = await self.list_devices()
            elif action == "list_playlists":
                out = await self.list_playlists()
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=self._explain(exc))

        if isinstance(out, dict) and out.get("error"):
            return ToolResult(success=False, output=None, error=out["error"])
        return ToolResult(success=True, output=out)

    @staticmethod
    def _explain(exc: Exception) -> str:
        """Turn a transport or API failure into something a person can act on."""
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 403:
                return _NEEDS_PREMIUM
            if status == 404:
                return _NO_DEVICE
            if status == 429:
                retry = exc.response.headers.get("Retry-After", "a moment")
                return f"Spotify is rate-limiting this app — try again in {retry}s."
        if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
            return "Could not reach Spotify. Check this machine's internet connection."
        return str(exc)
