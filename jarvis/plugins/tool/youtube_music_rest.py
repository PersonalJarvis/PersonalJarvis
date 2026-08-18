"""youtube_music tool — play, steer and read the user's YouTube Music by voice.

Google publishes NO YouTube Music API. What it does publish, and what this
tool is built on, is the **YouTube Data API v3** — the catalog behind YouTube
Music is YouTube's catalog (a song is a video in category 10, an album is an
``OLAK5uy_…`` playlist, the user's playlists and likes are ordinary YouTube
playlists and ratings). Everything the community "YouTube Music APIs" add on top
is a reverse-engineered private client that breaks with each redesign and sits
outside YouTube's terms, so it stays out (AP-2 spirit: an official key-backed
path over a fragile scrape).

Three facts of that API shape everything below:

* **It produces no audio and offers no remote control.** There is no YouTube
  equivalent of Spotify Connect. Playing means opening a ``music.youtube.com``
  watch link in the user's browser — their own account, Premium and history —
  and steering means the OS media session (``jarvis.platform.media_session``),
  the same channel the keyboard's play/pause keys use. Reading "what is playing"
  comes from that session too, so it works for the browser tab, the installed
  YouTube Music app, and honestly names any other player that happens to be
  current.
* **Search is rationed.** Google gives every Cloud project 100 ``search.list``
  calls a day (its own bucket, separate from the 10,000-unit pool the other
  calls draw from), so each "play X" is one of them; results are cached per
  process and the day's exhaustion is reported as Google's limit, not a fault.
* **A "song" search hits videos.** The user's own playlists are matched by name
  first (a cheap ``playlists.list``), likes are read with ``videos.list
  myRating=like``, and every deep link is built so YouTube Music keeps going
  after the first track (a song opens as its own radio, ``list=RDAMVM<id>``).

Risk tier ``monitor``: starting music is audible and instantly reversible, and
the writes (like, add to playlist, create playlist) are all undoable in the app.
A direct gated action, never a spawn (AP-5/AP-14).
"""
from __future__ import annotations

import asyncio
import difflib
import html
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_API = "https://www.googleapis.com/youtube/v3"
_MUSIC = "https://music.youtube.com"

HOME_URL = f"{_MUSIC}/"
LIKED_PAGE_URL = f"{_MUSIC}/playlist?list=LM"

_NOT_CONNECTED = "YouTube Music is not connected — connect it in the Plugins view."
_NEEDS_RECONNECT = (
    "Google rejected the stored YouTube authorization. Reconnect YouTube Music "
    "in the Plugins view."
)
_QUOTA = (
    "Google's daily YouTube API allowance for this app is used up — it resets "
    "at midnight Pacific time. Playback control and reading what plays still work."
)
_SEARCH_QUOTA = (
    "Google allows this app 100 YouTube searches a day and they are used up — "
    "the counter resets at midnight Pacific time. Playing one of your own "
    "playlists, pausing, skipping and reading what plays still work."
)
_API_DISABLED = (
    "The YouTube Data API v3 is not enabled in the Google Cloud project behind "
    "your OAuth client. Enable it (APIs & Services → Library → YouTube Data API "
    "v3) and try again."
)
_NO_SCOPE = (
    "The stored Google authorization does not include YouTube. Reconnect "
    "YouTube Music in the Plugins view and approve the YouTube permission."
)
_NO_BROWSER = (
    "This machine cannot open a browser, so nothing can play here — open the "
    "link on a device with YouTube Music."
)
_NO_SESSION_READ = (
    "Nothing is registered as playing on this machine, so there is nothing to "
    "report or control here."
)

# Music category on YouTube. Songs on YouTube Music are videos in it.
_MUSIC_CATEGORY_ID = "10"
_SEARCH_MAX = 25
_ART_TRACK_SUFFIX = " - Topic"

# Names people use for the liked-songs list; matched after normalisation.
_LIKED_NAMES: frozenset[str] = frozenset({
    "liked", "likes", "liked songs", "liked music", "my likes", "favorites", "favourites",
    # i18n-allow: spoken-input vocabulary (de/es), not prose
    "gelikte songs", "gelikte lieder", "geliked", "meine likes", "favoriten",
    "lieblingslieder", "lieblingssongs",
    "canciones que me gustan", "me gusta", "favoritos", "mis me gusta",
})
# Filler around a playlist name ("my running playlist" → "running").
_PLAYLIST_NOISE_RE = re.compile(
    r"\b(my|the|playlist"
    r"|mein|meine|meiner|meinen|wiedergabeliste|die|der|das"  # i18n-allow: spoken-input vocabulary
    r"|mi|mis|lista|el|la)\b",  # i18n-allow: spoken-input vocabulary
    re.IGNORECASE,
)

# How long `play` watches the media session for the new track before it stops
# claiming to know. A warm browser registers the session within a second; a
# cold start needs four to five (measured live 2026-08-18: Edge from closed took
# ~4 s). Bounded so a voice turn never hangs on a browser that never starts,
# and the wait ends the moment playback is seen.
_CONFIRM_TIMEOUT_S = 6.0
_CONFIRM_STEP_S = 0.5


def song_url(video_id: str) -> str:
    """The song plus its own radio, so playback continues like in the app."""
    return f"{_MUSIC}/watch?v={video_id}&list=RDAMVM{video_id}"


def playlist_url(playlist_id: str, first_video_id: str | None = None) -> str:
    """Autoplays the list (verified live 2026-08-18 for an album playlist)."""
    if first_video_id:
        return f"{_MUSIC}/watch?v={first_video_id}&list={playlist_id}"
    return f"{_MUSIC}/watch?list={playlist_id}"


def _default_token_provider() -> str | None:
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("youtube_music")
    return tokens.access if tokens is not None else None


async def _default_refresher(observed_access_token: str | None = None) -> bool:
    """Refresh the stored token in place; flags ``needs_reauth`` on a dead grant
    so the Plugins view stops showing a green "connected" that lies."""
    from jarvis.marketplace.connect_helpers import build_handler_from_catalog
    from jarvis.marketplace.refresh_scheduler import refresh_plugin_token
    from jarvis.marketplace.token_store import TokenStore

    attempt = await refresh_plugin_token(
        "youtube_music",
        TokenStore(),
        build_handler_from_catalog,
        force=True,
        observed_access_token=observed_access_token,
    )
    return attempt.usable


def _default_opener(url: str) -> bool:
    from jarvis.platform.open_path import open_url

    return open_url(url)


def _clean(text: Any) -> str:
    return html.unescape(str(text or "")).strip()


def _artist_from_channel(channel_title: str) -> str:
    """Auto-generated music channels are named "<Artist> - Topic"."""
    name = _clean(channel_title)
    if name.endswith(_ART_TRACK_SUFFIX):
        return name[: -len(_ART_TRACK_SUFFIX)].strip()
    return name


def _slim_search_item(item: dict[str, Any]) -> dict[str, Any] | None:
    ident = item.get("id") or {}
    snippet = item.get("snippet") or {}
    kind = str(ident.get("kind") or "")
    out: dict[str, Any] = {"title": _clean(snippet.get("title"))}
    if kind.endswith("#video") and ident.get("videoId"):
        video_id = str(ident["videoId"])
        out.update(
            type="song",
            artist=_artist_from_channel(snippet.get("channelTitle", "")),
            video_id=video_id,
            url=song_url(video_id),
        )
    elif kind.endswith("#playlist") and ident.get("playlistId"):
        playlist_id = str(ident["playlistId"])
        out.update(
            type="album" if playlist_id.startswith("OLAK5uy_") else "playlist",
            owner=_artist_from_channel(snippet.get("channelTitle", "")),
            playlist_id=playlist_id,
            url=playlist_url(playlist_id),
        )
    elif kind.endswith("#channel") and ident.get("channelId"):
        out.update(
            type="artist",
            channel_id=str(ident["channelId"]),
            url=f"{_MUSIC}/channel/{ident['channelId']}",
        )
    else:
        return None
    return out


def _slim_playlist(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    details = item.get("contentDetails") or {}
    playlist_id = str(item.get("id") or "")
    out: dict[str, Any] = {
        "title": _clean(snippet.get("title")),
        "playlist_id": playlist_id,
        "url": playlist_url(playlist_id),
    }
    if isinstance(details.get("itemCount"), int):
        out["track_count"] = details["itemCount"]
    return out


def _slim_playlist_item(item: dict[str, Any]) -> dict[str, Any] | None:
    snippet = item.get("snippet") or {}
    video_id = ((snippet.get("resourceId") or {}).get("videoId")) or (
        (item.get("contentDetails") or {}).get("videoId")
    )
    if not video_id:
        return None
    return {
        "title": _clean(snippet.get("title")),
        "artist": _artist_from_channel(snippet.get("videoOwnerChannelTitle", "")),
        "video_id": str(video_id),
        "url": song_url(str(video_id)),
    }


def _slim_video(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    video_id = str(item.get("id") or "")
    return {
        "title": _clean(snippet.get("title")),
        "artist": _artist_from_channel(snippet.get("channelTitle", "")),
        "video_id": video_id,
        "url": song_url(video_id),
    }


def _normalize_name(name: str) -> str:
    cleaned = _PLAYLIST_NOISE_RE.sub(" ", name.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def is_liked_name(name: str) -> bool:
    return _normalize_name(name) in {_normalize_name(n) for n in _LIKED_NAMES} or (
        name.strip().lower() in _LIKED_NAMES
    )


def match_playlist(name: str, playlists: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The user's playlist a spoken name most plausibly means: exact, then
    contains, then a fuzzy ratio — never a wild guess below the floor."""
    wanted = _normalize_name(name)
    if not wanted or not playlists:
        return None
    titled = [(p, _normalize_name(str(p.get("title") or ""))) for p in playlists]
    for playlist, title in titled:
        if title == wanted:
            return playlist
    for playlist, title in titled:
        if wanted in title or (title and title in wanted):
            return playlist
    scored = sorted(
        ((difflib.SequenceMatcher(None, wanted, title).ratio(), p) for p, title in titled),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if scored and scored[0][0] >= 0.6:
        return scored[0][1]
    return None


class YouTubeMusicRestTool:
    name: str = "youtube_music"
    risk_tier: str = "monitor"
    description: str = (
        "Play and control the user's YouTube Music: start a song, artist, album, "
        "one of their playlists or their liked songs (opens in YouTube Music in the "
        "browser and keeps playing like a radio), pause, resume, skip, go back, say "
        "what is playing right now, like a song, add a song to a playlist, create a "
        "playlist, list playlists. Use for 'play Radiohead on YouTube Music', "
        "'skip this', 'what song is this', 'pause the music', 'like this song', "
        "'add this to my running playlist'. Actions: now_playing, play, pause, "
        "next, previous, search, list_playlists, playlist_tracks, liked_songs, "
        "like, add_to_playlist, create_playlist, open. Requires the YouTube Music "
        "plugin to be connected in the Plugins view. Volume is not controllable "
        "here — use the system or app volume."
    )
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
                    "search",
                    "list_playlists",
                    "playlist_tracks",
                    "liked_songs",
                    "like",
                    "add_to_playlist",
                    "create_playlist",
                    "open",
                ],
                "default": "now_playing",
            },
            "query": {
                "type": "string",
                "description": (
                    "what to play, search for, like or add: 'Radiohead', 'Karma "
                    "Police', 'OK Computer', 'my running playlist'. Leave out on "
                    "play to resume what is paused; leave out on like/add_to_playlist "
                    "to use the song playing right now."
                ),
            },
            "type": {
                "type": "string",
                "enum": ["song", "album", "artist", "playlist", "liked"],
                "default": "song",
                "description": "what the query names ('liked' = the user's liked songs)",
            },
            "playlist": {
                "type": "string",
                "description": (
                    "target playlist name for add_to_playlist / playlist_tracks, or "
                    "the new playlist's name for create_playlist"
                ),
            },
            "rating": {
                "type": "string",
                "enum": ["like", "dislike", "none"],
                "default": "like",
                "description": "for like: 'none' removes an earlier like or dislike",
            },
            "limit": {"type": "integer", "default": 5, "description": "results, max 25"},
        },
        "required": ["action"],
    }

    def __init__(
        self,
        access_token_provider: Callable[[], str | None] | None = None,
        transport: Any | None = None,
        token_refresher: Callable[[], Awaitable[bool]] | None = None,
        media: Any | None = None,
        opener: Callable[[str], bool] | None = None,
        confirm_timeout_s: float = _CONFIRM_TIMEOUT_S,
    ) -> None:
        from ._http_pool import HttpClientPool

        self._token_provider = access_token_provider or _default_token_provider
        self._refresher = token_refresher
        self._media = media
        self._opener = opener or _default_opener
        self._confirm_timeout_s = confirm_timeout_s
        self._pool = HttpClientPool(transport=transport)
        # search.list is the rationed call (100/day). Same words → same answer
        # within a session, so a repeat costs nothing.
        self._search_cache: dict[tuple[str, str, int], list[dict[str, Any]]] = {}

    # -- media session (OS) --------------------------------------------------

    def _media_controller(self) -> Any:
        if self._media is None:
            from jarvis.platform.media_session import make_media_session_controller

            self._media = make_media_session_controller()
        return self._media

    async def _capability(self) -> Any:
        return await self._media_controller().capability()

    # -- HTTP -----------------------------------------------------------------

    def _bearer(self) -> dict[str, str] | None:
        token = self._token_provider()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "User-Agent": "Personal-Jarvis/1.0"}

    async def _with_auth_retry(
        self, do_call: Callable[[dict[str, str]], Awaitable[Any]]
    ) -> Any:
        """Run an authenticated call; on a 401 refresh once and retry (Google
        access tokens live one hour, so this is the ordinary path)."""
        import httpx

        headers = self._bearer()
        if headers is None:
            return {"error": _NOT_CONNECTED}
        try:
            return await do_call(headers)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
        observed = headers["Authorization"].removeprefix("Bearer ")
        try:
            if self._refresher is None:
                refreshed = bool(await _default_refresher(observed))
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
        client = self._pool.client()
        resp = await client.request(
            method, f"{_API}{path}", params=params, json=json_body, headers=headers
        )
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    @staticmethod
    def _reason(exc: Any) -> str:
        """Google's machine-readable failure reason (``quotaExceeded``,
        ``accessNotConfigured``, ``insufficientPermissions`` …)."""
        try:
            payload = exc.response.json()
        except Exception:  # noqa: BLE001 — a non-JSON error body is still an error
            return ""
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return ""
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            reason = str(errors[0].get("reason") or "")
            if reason:
                return reason
        return str(error.get("status") or "")

    # -- Data API reads ------------------------------------------------------

    async def _search_raw(
        self, headers: dict[str, str], *, query: str, kind: str, limit: int
    ) -> list[dict[str, Any]]:
        key = (kind, query.lower().strip(), limit)
        cached = self._search_cache.get(key)
        if cached is not None:
            return cached
        params: dict[str, Any] = {"part": "snippet", "q": query, "maxResults": limit}
        if kind in ("song", "artist"):
            params.update(type="video", videoCategoryId=_MUSIC_CATEGORY_ID)
        elif kind == "album":
            params["type"] = "playlist"
            if "album" not in query.lower():
                params["q"] = f"{query} album"
        else:  # playlist (public)
            params["type"] = "playlist"
        payload = await self._request("GET", "/search", headers, params=params)
        raw = [i for i in ((payload or {}).get("items") or []) if isinstance(i, dict)]
        slim = [s for s in (_slim_search_item(i) for i in raw) if s]
        self._search_cache[key] = slim
        if len(self._search_cache) > 128:
            self._search_cache.pop(next(iter(self._search_cache)))
        return slim

    async def _my_playlists(self, headers: dict[str, str]) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/playlists",
            headers,
            params={"part": "snippet,contentDetails", "mine": "true", "maxResults": 50},
        )
        items = [i for i in ((payload or {}).get("items") or []) if isinstance(i, dict)]
        return [_slim_playlist(i) for i in items]

    async def _liked(self, headers: dict[str, str], limit: int) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/videos",
            headers,
            params={"part": "snippet", "myRating": "like", "maxResults": limit},
        )
        items = [i for i in ((payload or {}).get("items") or []) if isinstance(i, dict)]
        return [_slim_video(i) for i in items]

    async def _resolve_song(
        self, headers: dict[str, str], query: str
    ) -> dict[str, Any] | None:
        hits = await self._search_raw(headers, query=query, kind="song", limit=1)
        return hits[0] if hits else None

    async def _resolve_playlist(
        self, headers: dict[str, str], name: str
    ) -> dict[str, Any] | None:
        mine = await self._my_playlists(headers)
        return match_playlist(name, mine)

    # -- public actions ------------------------------------------------------

    async def now_playing(self) -> dict[str, Any]:
        media = self._media_controller()
        cap = await media.capability()
        entry = await media.now_playing() if cap.can_read else None
        if entry is None:
            out: dict[str, Any] = {"is_playing": False}
            if cap.can_read:
                out["message"] = _NO_SESSION_READ
            else:
                out["message"] = "This machine cannot read what is playing."
                if cap.note:
                    out["note"] = cap.note
            return out
        out = entry.as_dict()
        out["source"] = "youtube_music_app" if entry.is_youtube_music_app else (
            "browser" if entry.is_browser else "other_player"
        )
        return out

    async def _control(self, verb: str) -> dict[str, Any]:
        media = self._media_controller()
        cap = await media.capability()
        if not cap.can_control:
            return {"error": "This machine cannot control playback. " + (cap.note or "")}
        before = await media.now_playing() if cap.can_read else None
        if cap.can_read and before is None:
            return {"error": _NO_SESSION_READ}
        ok = bool(await getattr(media, verb)())
        if not ok:
            return {"error": f"The player did not accept '{verb}'."}
        out: dict[str, Any] = {"ok": True, "action": verb}
        if before is not None:
            out["app"] = before.app
            if verb in ("next", "previous"):
                await asyncio.sleep(0.8)
                after = await media.now_playing()
                if after is not None:
                    out["now"] = after.as_dict()
            else:
                out["track"] = before.title
                out["artist"] = before.artist
        elif cap.backend == "media-keys":
            out["note"] = "Sent as a media key: this toggles play/pause blindly."
        return out

    async def pause(self) -> dict[str, Any]:
        return await self._control("pause")

    async def resume(self) -> dict[str, Any]:
        return await self._control("play")

    async def next_track(self) -> dict[str, Any]:
        return await self._control("next")

    async def previous_track(self) -> dict[str, Any]:
        return await self._control("previous")

    async def search(
        self, *, query: str, item_type: str = "song", limit: int = 5
    ) -> dict[str, Any]:
        capped = max(1, min(int(limit), _SEARCH_MAX))
        kind = item_type if item_type in ("song", "album", "artist", "playlist") else "song"

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            if kind == "playlist":
                mine = match_playlist(query, await self._my_playlists(headers))
                if mine is not None:
                    return {"query": query, "type": kind, "results": [mine], "own": True}
            hits = await self._search_raw(headers, query=query, kind=kind, limit=capped)
            return {"query": query, "type": kind, "results": hits}

        return await self._with_auth_retry(_do)

    async def _open_and_confirm(
        self, url: str, started: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Open the deep link, then watch the OS media session so the answer
        says what actually happened — including a browser that opened the page
        but withheld autoplay."""
        media = self._media_controller()
        cap = await media.capability()
        paused_app: str | None = None
        if cap.can_read and cap.can_control:
            before = await media.now_playing()
            # A second tab would play on top of the first; silence the old one.
            if before is not None and before.is_playing:
                if await media.pause():
                    paused_app = before.app
        opened = self._opener(url)
        out: dict[str, Any] = {"ok": opened, "url": url}
        if started:
            out["started"] = started
        if paused_app:
            out["paused_previous"] = paused_app
        if not opened:
            out["error"] = _NO_BROWSER
            return out
        if cap.can_read:
            wanted = (started or {}).get("title", "").lower()
            deadline = self._confirm_timeout_s
            elapsed = 0.0
            confirmed = False
            while elapsed < deadline:
                await asyncio.sleep(_CONFIRM_STEP_S)
                elapsed += _CONFIRM_STEP_S
                now = await media.now_playing()
                if now is not None and now.is_playing and now.is_browser:
                    if not wanted or wanted[:12] in now.title.lower() or (
                        started and started.get("type") != "song"
                    ):
                        out["now"] = now.as_dict()
                        confirmed = True
                        break
            out["playback_confirmed"] = confirmed
            if not confirmed:
                out["note"] = (
                    "YouTube Music opened, but the browser has not reported playback "
                    "yet — if it stays silent, press play once (browsers block "
                    "autoplay on a site until it has been used there)."
                )
        return out

    async def play(
        self, *, query: str = "", item_type: str = "song"
    ) -> dict[str, Any]:
        if not query and item_type != "liked":
            # Resume what is paused; opening the home page would start nothing.
            resumed = await self.resume()
            if not resumed.get("error"):
                resumed["resumed"] = True
                return resumed
            return {
                "error": (
                    "Nothing is paused that could be resumed — say what to play, "
                    "for example a song, an artist or one of your playlists."
                )
            }
        kinds = ("song", "album", "artist", "playlist", "liked")
        kind = item_type if item_type in kinds else "song"
        if kind == "playlist" and is_liked_name(query):
            kind = "liked"

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            if kind == "liked":
                liked = await self._liked(headers, 1)
                if not liked:
                    return await self._open_and_confirm(
                        LIKED_PAGE_URL, {"type": "liked", "title": "Liked songs"}
                    )
                first = liked[0]
                return await self._open_and_confirm(
                    playlist_url("LM", first["video_id"]),
                    {"type": "liked", "title": "Liked songs", "first": first},
                )
            if kind == "playlist":
                mine = await self._resolve_playlist(headers, query)
                if mine is not None:
                    return await self._open_and_confirm(
                        mine["url"], {"type": "playlist", "own": True, **mine}
                    )
                hits = await self._search_raw(headers, query=query, kind="playlist", limit=1)
                if not hits:
                    return {
                        "error": f"No playlist called {query!r} — neither yours nor a public one."
                    }
                return await self._open_and_confirm(hits[0]["url"], hits[0])
            if kind == "album":
                hits = await self._search_raw(headers, query=query, kind="album", limit=3)
                albums = [h for h in hits if h.get("type") == "album"] or hits
                if not albums:
                    return {"error": f"YouTube Music has no album called {query!r}."}
                return await self._open_and_confirm(albums[0]["url"], albums[0])
            # song or artist: top song, then its radio keeps the artist's world going
            hits = await self._search_raw(headers, query=query, kind="song", limit=1)
            if not hits:
                return {"error": f"YouTube Music has nothing called {query!r}."}
            started = dict(hits[0])
            if kind == "artist":
                started["note"] = "Started the artist's top song; the radio continues with them."
            return await self._open_and_confirm(started["url"], started)

        return await self._with_auth_retry(_do)

    async def open_home(self) -> dict[str, Any]:
        opened = self._opener(HOME_URL)
        if not opened:
            return {"error": _NO_BROWSER, "url": HOME_URL}
        return {"ok": True, "url": HOME_URL}

    async def list_playlists(self) -> dict[str, Any]:
        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            return {"playlists": await self._my_playlists(headers)}

        return await self._with_auth_retry(_do)

    async def playlist_tracks(self, *, name: str, limit: int = 10) -> dict[str, Any]:
        capped = max(1, min(int(limit), 50))

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            if is_liked_name(name):
                return {"playlist": "Liked songs", "tracks": await self._liked(headers, capped)}
            mine = await self._resolve_playlist(headers, name)
            if mine is None:
                return {"error": f"You have no playlist called {name!r}."}
            payload = await self._request(
                "GET",
                "/playlistItems",
                headers,
                params={
                    "part": "snippet,contentDetails",
                    "playlistId": mine["playlist_id"],
                    "maxResults": capped,
                },
            )
            items = [i for i in ((payload or {}).get("items") or []) if isinstance(i, dict)]
            tracks = [t for t in (_slim_playlist_item(i) for i in items) if t]
            return {"playlist": mine["title"], "url": mine["url"], "tracks": tracks}

        return await self._with_auth_retry(_do)

    async def liked_songs(self, *, limit: int = 10) -> dict[str, Any]:
        capped = max(1, min(int(limit), 50))

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            return {"tracks": await self._liked(headers, capped)}

        return await self._with_auth_retry(_do)

    async def _current_or_query_song(
        self, headers: dict[str, str], query: str
    ) -> dict[str, Any] | str:
        """The song a like/add refers to: the named one, else what plays now
        (looked up by title + artist, since the OS session carries no id)."""
        if query:
            found = await self._resolve_song(headers, query)
            return found or f"YouTube Music has nothing called {query!r}."
        media = self._media_controller()
        cap = await media.capability()
        now = await media.now_playing() if cap.can_read else None
        if now is None or not now.title:
            return "Nothing is playing that could be meant — name the song."
        found = await self._resolve_song(headers, f"{now.title} {now.artist}".strip())
        return found or f"Could not find {now.title!r} by {now.artist!r} on YouTube Music."

    async def like(self, *, query: str = "", rating: str = "like") -> dict[str, Any]:
        value = rating if rating in ("like", "dislike", "none") else "like"

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            song = await self._current_or_query_song(headers, query)
            if isinstance(song, str):
                return {"error": song}
            await self._request(
                "POST",
                "/videos/rate",
                headers,
                params={"id": song["video_id"], "rating": value},
            )
            return {"ok": True, "rating": value, "song": song}

        return await self._with_auth_retry(_do)

    async def add_to_playlist(self, *, query: str = "", playlist: str = "") -> dict[str, Any]:
        if not playlist:
            return {"error": "add_to_playlist needs the playlist name."}

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            target = await self._resolve_playlist(headers, playlist)
            if target is None:
                return {"error": f"You have no playlist called {playlist!r} — create it first."}
            song = await self._current_or_query_song(headers, query)
            if isinstance(song, str):
                return {"error": song}
            await self._request(
                "POST",
                "/playlistItems",
                headers,
                params={"part": "snippet"},
                json_body={
                    "snippet": {
                        "playlistId": target["playlist_id"],
                        "resourceId": {"kind": "youtube#video", "videoId": song["video_id"]},
                    }
                },
            )
            return {"ok": True, "song": song, "playlist": target}

        return await self._with_auth_retry(_do)

    async def create_playlist(self, *, name: str) -> dict[str, Any]:
        if not name.strip():
            return {"error": "create_playlist needs a name."}

        async def _do(headers: dict[str, str]) -> dict[str, Any]:
            payload = await self._request(
                "POST",
                "/playlists",
                headers,
                params={"part": "snippet,status"},
                json_body={
                    "snippet": {"title": name.strip()},
                    "status": {"privacyStatus": "private"},
                },
            )
            created = _slim_playlist(payload or {})
            created["privacy"] = "private"
            return {"ok": True, "playlist": created}

        return await self._with_auth_retry(_do)

    # -- Tool protocol ------------------------------------------------------

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        """Reads are ``safe``; everything that makes sound or writes to the
        account is ``monitor`` (audited, no prompt) — every write is undoable
        in the app. An unknown action stays conservative."""
        action = (args.get("action") or "now_playing").strip()
        if action in ("now_playing", "search", "list_playlists", "playlist_tracks", "liked_songs"):
            return "safe"
        if action in (
            "play", "pause", "next", "previous", "open", "like",
            "add_to_playlist", "create_playlist",
        ):
            return "monitor"
        return "ask"

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "now_playing").strip()
        query = (args.get("query") or "").strip()
        item_type = (args.get("type") or "song").strip() or "song"
        playlist = (args.get("playlist") or "").strip()
        limit = int(args.get("limit") or 5)
        try:
            if action == "now_playing":
                out = await self.now_playing()
            elif action == "play":
                out = await self.play(query=query, item_type=item_type)
            elif action == "pause":
                out = await self.pause()
            elif action == "next":
                out = await self.next_track()
            elif action == "previous":
                out = await self.previous_track()
            elif action == "search":
                if not query:
                    return ToolResult(success=False, output=None, error="search needs a query")
                out = await self.search(query=query, item_type=item_type, limit=limit)
            elif action == "list_playlists":
                out = await self.list_playlists()
            elif action == "playlist_tracks":
                name = playlist or query
                if not name:
                    return ToolResult(
                        success=False, output=None, error="playlist_tracks needs a playlist name"
                    )
                out = await self.playlist_tracks(name=name, limit=limit)
            elif action == "liked_songs":
                out = await self.liked_songs(limit=limit)
            elif action == "like":
                out = await self.like(query=query, rating=(args.get("rating") or "like"))
            elif action == "add_to_playlist":
                out = await self.add_to_playlist(query=query, playlist=playlist)
            elif action == "create_playlist":
                out = await self.create_playlist(name=playlist or query)
            elif action == "open":
                out = await self.open_home()
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except Exception as exc:  # noqa: BLE001 — every failure becomes a spoken, actionable error
            return ToolResult(success=False, output=None, error=self._explain(exc))

        if isinstance(out, dict) and out.get("error"):
            return ToolResult(success=False, output=None, error=out["error"])
        return ToolResult(success=True, output=out)

    @classmethod
    def _explain(cls, exc: Exception) -> str:
        """Turn a transport or API failure into something a person can act on."""
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            reason = cls._reason(exc)
            if status == 403:
                if reason == "quotaExceeded":
                    if "search" in str(exc.request.url):
                        return _SEARCH_QUOTA
                    return _QUOTA
                if reason in ("accessNotConfigured", "SERVICE_DISABLED"):
                    return _API_DISABLED
                if reason in ("insufficientPermissions", "forbidden", "PERMISSION_DENIED"):
                    return _NO_SCOPE
                return f"Google refused the request ({reason or 'forbidden'})."
            if status == 404:
                return "YouTube has no such item any more (it may have been removed)."
            if status == 429:
                retry = exc.response.headers.get("Retry-After", "a moment")
                return f"Google is rate-limiting this app — try again in {retry}s."
        if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
            return "Could not reach Google. Check this machine's internet connection."
        return str(exc)
