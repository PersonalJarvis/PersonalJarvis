"""Unit tests for the YouTube Music tool.

Weighted towards what decides whether the plugin is usable: the deep links it
opens (a song must open as its own radio, an album as its playlist), the OS
media session it steers, the rationed search, and Google's three real
refusals — quota, API not enabled, missing scope.
"""
from __future__ import annotations

from typing import Any

import httpx

from jarvis.platform.media_session import MediaSessionCapability, NowPlaying
from jarvis.plugins.tool.youtube_music_rest import (
    HOME_URL,
    YouTubeMusicRestTool,
    match_playlist,
    playlist_url,
    song_url,
)

_FAKE_TOKEN = "tok123"  # noqa: S105 — a literal for MockTransport, not a credential

_SONG_HIT = {
    "id": {"kind": "youtube#video", "videoId": "vid123"},
    "snippet": {"title": "Karma Police", "channelTitle": "Radiohead - Topic"},
}
_ALBUM_HIT = {
    "id": {"kind": "youtube#playlist", "playlistId": "OLAK5uy_abc"},
    "snippet": {"title": "OK Computer", "channelTitle": "Radiohead - Topic"},
}
_MY_PLAYLISTS = {
    "items": [
        {
            "id": "PLrun",
            "snippet": {"title": "Running 2026"},
            "contentDetails": {"itemCount": 12},
        },
        {"id": "PLchill", "snippet": {"title": "Chill evenings"}, "contentDetails": {}},
    ]
}


class FakeMedia:
    """A media session that remembers what it was told to do."""

    def __init__(
        self,
        now: NowPlaying | None = None,
        can_read: bool = True,
        can_control: bool = True,
        backend: str = "fake",
        note: str = "",
        after_open: NowPlaying | None = None,
    ) -> None:
        self.now = now
        self.after_open = after_open
        self.cap = MediaSessionCapability(can_read, can_control, backend, note)
        self.calls: list[str] = []
        self.opened = False

    async def capability(self):
        return self.cap

    async def now_playing(self):
        if self.opened and self.after_open is not None:
            return self.after_open
        return self.now

    async def play(self):
        self.calls.append("play")
        return True

    async def pause(self):
        self.calls.append("pause")
        return True

    async def toggle(self):
        self.calls.append("toggle")
        return True

    async def next(self):
        self.calls.append("next")
        return True

    async def previous(self):
        self.calls.append("previous")
        return True


def _playing(title="Karma Police", artist="Radiohead", app="Google Chrome", browser=True):
    return NowPlaying(title, artist, "OK Computer", app, "playing", 10.0, 260.0, browser)


def _paused(title="Karma Police", artist="Radiohead"):
    return NowPlaying(title, artist, "", "Google Chrome", "paused", 10.0, 260.0, True)


class Opener:
    def __init__(self, ok: bool = True, media: FakeMedia | None = None) -> None:
        self.ok = ok
        self.urls: list[str] = []
        self.media = media

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        if self.media is not None:
            self.media.opened = True
        return self.ok


def _tool(handler, media=None, opener=None, token=_FAKE_TOKEN, refresher=None):
    return YouTubeMusicRestTool(
        access_token_provider=lambda: token,
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
        media=media or FakeMedia(),
        opener=opener or Opener(),
        confirm_timeout_s=0.6,
    )


def _google_error(status: int, reason: str, message: str = "nope") -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "code": status,
                "message": message,
                "errors": [{"reason": reason, "domain": "youtube.quota"}],
            }
        },
    )


def _search_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/search"):
        kind = request.url.params.get("type")
        if kind == "video":
            assert request.url.params.get("videoCategoryId") == "10"
            return httpx.Response(200, json={"items": [_SONG_HIT]})
        return httpx.Response(200, json={"items": [_ALBUM_HIT]})
    if request.url.path.endswith("/playlists"):
        return httpx.Response(200, json=_MY_PLAYLISTS)
    return httpx.Response(404, json={"error": {"code": 404, "message": "?"}})


# -- deep links ---------------------------------------------------------------


def test_song_url_opens_the_song_as_its_own_radio():
    assert song_url("abc") == "https://music.youtube.com/watch?v=abc&list=RDAMVMabc"


def test_playlist_url_shapes():
    assert playlist_url("PL1") == "https://music.youtube.com/watch?list=PL1"
    assert playlist_url("LM", "v1") == "https://music.youtube.com/watch?v=v1&list=LM"


def test_match_playlist_exact_contains_fuzzy_and_floor():
    lists = [{"title": "Running 2026"}, {"title": "Chill evenings"}, {"title": "Workout"}]
    assert match_playlist("running 2026", lists)["title"] == "Running 2026"
    assert match_playlist("my running playlist", lists)["title"] == "Running 2026"
    assert match_playlist("chill evening", lists)["title"] == "Chill evenings"
    assert match_playlist("wrkout", lists)["title"] == "Workout"
    assert match_playlist("jazz classics", lists) is None
    assert match_playlist("", lists) is None


# -- search -------------------------------------------------------------------


async def test_search_song_slims_and_strips_topic_suffix():
    out = await _tool(_search_handler).search(query="karma police", item_type="song")
    assert out["results"] == [
        {
            "title": "Karma Police",
            "type": "song",
            "artist": "Radiohead",
            "video_id": "vid123",
            "url": song_url("vid123"),
        }
    ]


async def test_search_is_cached_per_process_because_google_rations_it():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _search_handler(request)

    tool = _tool(handler)
    await tool.search(query="Karma Police", item_type="song")
    await tool.search(query="karma police", item_type="song")
    assert calls["n"] == 1


async def test_search_album_asks_for_playlists_and_labels_olak_as_album():
    out = await _tool(_search_handler).search(query="OK Computer", item_type="album")
    assert out["results"][0]["type"] == "album"
    assert out["results"][0]["url"] == playlist_url("OLAK5uy_abc")


async def test_search_playlist_prefers_the_users_own_lists():
    out = await _tool(_search_handler).search(query="my running playlist", item_type="playlist")
    assert out["own"] is True
    assert out["results"][0]["playlist_id"] == "PLrun"


async def test_not_connected_without_token():
    tool = YouTubeMusicRestTool(access_token_provider=lambda: None, media=FakeMedia())
    out = await tool.search(query="x")
    assert "not connected" in out["error"].lower()


# -- play ---------------------------------------------------------------------


async def test_play_song_pauses_the_old_player_opens_the_radio_link_and_confirms():
    media = FakeMedia(now=_playing("Old song", "Someone"), after_open=_playing())
    opener = Opener(media=media)
    out = await _tool(_search_handler, media=media, opener=opener).play(query="karma police")
    assert opener.urls == [song_url("vid123")]
    assert media.calls == ["pause"]
    assert out["paused_previous"] == "Google Chrome"
    assert out["started"]["title"] == "Karma Police"
    assert out["playback_confirmed"] is True
    assert out["now"]["track"] == "Karma Police"


async def test_play_reports_when_the_browser_withholds_autoplay():
    media = FakeMedia(now=None, after_open=_paused())
    opener = Opener(media=media)
    out = await _tool(_search_handler, media=media, opener=opener).play(query="karma police")
    assert out["ok"] is True and out["playback_confirmed"] is False
    assert "press play" in out["note"]


async def test_play_without_browser_returns_the_link_honestly():
    opener = Opener(ok=False)
    out = await _tool(_search_handler, opener=opener).play(query="karma police")
    assert out["url"] == song_url("vid123")
    assert "cannot open a browser" in out["error"]


async def test_play_artist_opens_top_song_radio_with_a_note():
    opener = Opener()
    out = await _tool(_search_handler, opener=opener).play(query="Radiohead", item_type="artist")
    assert opener.urls == [song_url("vid123")]
    assert "radio" in out["started"]["note"]


async def test_play_album_opens_the_album_playlist():
    opener = Opener()
    out = await _tool(_search_handler, opener=opener).play(query="OK Computer", item_type="album")
    assert opener.urls == [playlist_url("OLAK5uy_abc")]
    assert out["started"]["type"] == "album"


async def test_play_own_playlist_by_fuzzy_name_costs_no_search():
    def handler(request: httpx.Request) -> httpx.Response:
        assert not request.url.path.endswith("/search"), "own playlist must not burn a search"
        return _search_handler(request)

    opener = Opener()
    tool = _tool(handler, opener=opener)
    out = await tool.play(query="my running playlist", item_type="playlist")
    assert opener.urls == [playlist_url("PLrun")]
    assert out["started"]["own"] is True


async def test_play_liked_songs_uses_the_first_like_and_the_lm_list():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            assert request.url.params.get("myRating") == "like"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "likedvid", "snippet": {"title": "Angels", "channelTitle": "Robbie"}}
                    ]
                },
            )
        return _search_handler(request)

    opener = Opener()
    out = await _tool(handler, opener=opener).play(query="liked songs", item_type="playlist")
    assert opener.urls == [playlist_url("LM", "likedvid")]
    assert out["started"]["type"] == "liked"


async def test_play_without_query_resumes_the_paused_session():
    media = FakeMedia(now=_paused())
    out = await _tool(_search_handler, media=media).play(query="")
    assert media.calls == ["play"] and out["resumed"] is True and out["track"] == "Karma Police"


async def test_play_without_query_and_nothing_paused_asks_for_a_name():
    media = FakeMedia(now=None)
    out = await _tool(_search_handler, media=media).play(query="")
    assert "say what to play" in out["error"]


async def test_play_unknown_song_says_so():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    out = await _tool(handler).play(query="zzz")
    assert "nothing called" in out["error"]


# -- media session actions ---------------------------------------------------


async def test_now_playing_reports_the_session_and_its_source():
    media = FakeMedia(now=_playing())
    out = await _tool(_search_handler, media=media).now_playing()
    assert out["track"] == "Karma Police" and out["is_playing"] and out["source"] == "browser"


async def test_now_playing_without_read_capability_carries_the_fix():
    media = FakeMedia(now=None, can_read=False, backend="media-keys", note="install X")
    out = await _tool(_search_handler, media=media).now_playing()
    assert out["is_playing"] is False and out["note"] == "install X"


async def test_pause_and_next_report_the_app_and_new_track():
    media = FakeMedia(now=_playing())
    tool = _tool(_search_handler, media=media)
    out = await tool.pause()
    assert out["ok"] and out["app"] == "Google Chrome" and out["track"] == "Karma Police"
    out = await tool.next_track()
    assert out["ok"] and out["now"]["track"] == "Karma Police"
    assert media.calls == ["pause", "next"]


async def test_control_without_capability_is_an_honest_error():
    media = FakeMedia(now=None, can_read=False, can_control=False, note="brew install it")
    out = await _tool(_search_handler, media=media).pause()
    assert "cannot control" in out["error"] and "brew install it" in out["error"]


async def test_control_with_nothing_registered_is_an_error():
    out = await _tool(_search_handler, media=FakeMedia(now=None)).next_track()
    assert "Nothing is registered" in out["error"]


# -- library writes -----------------------------------------------------------


async def test_like_current_song_looks_it_up_by_title_and_artist():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            seen["q"] = request.url.params.get("q")
            return httpx.Response(200, json={"items": [_SONG_HIT]})
        if request.url.path.endswith("/videos/rate"):
            seen["rate"] = dict(request.url.params)
            return httpx.Response(204)
        return httpx.Response(404)

    media = FakeMedia(now=_playing())
    out = await _tool(handler, media=media).like()
    assert seen["q"] == "Karma Police Radiohead"
    assert seen["rate"] == {"id": "vid123", "rating": "like"}
    assert out["ok"] and out["song"]["title"] == "Karma Police"


async def test_like_with_nothing_playing_asks_for_the_song():
    out = await _tool(_search_handler, media=FakeMedia(now=None)).like()
    assert "name the song" in out["error"]


async def test_add_to_playlist_inserts_into_the_matched_list():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/playlistItems"):
            import json

            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "item1"})
        return _search_handler(request)

    out = await _tool(handler).add_to_playlist(query="karma police", playlist="running")
    assert seen["body"]["snippet"]["playlistId"] == "PLrun"
    assert seen["body"]["snippet"]["resourceId"]["videoId"] == "vid123"
    assert out["playlist"]["title"] == "Running 2026"


async def test_add_to_playlist_unknown_list_says_create_it():
    out = await _tool(_search_handler).add_to_playlist(query="x", playlist="jazz classics")
    assert "create it first" in out["error"]


async def test_create_playlist_is_private_by_default():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"id": "PLnew", "snippet": {"title": "Late night"}, "contentDetails": {}}
        )

    out = await _tool(handler).create_playlist(name="Late night")
    assert seen["body"]["status"]["privacyStatus"] == "private"
    assert out["playlist"]["playlist_id"] == "PLnew" and out["playlist"]["privacy"] == "private"


async def test_playlist_tracks_lists_a_named_playlist():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/playlistItems"):
            assert request.url.params.get("playlistId") == "PLchill"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "snippet": {
                                "title": "Angels",
                                "videoOwnerChannelTitle": "Robbie Williams - Topic",
                                "resourceId": {"videoId": "v9"},
                            }
                        }
                    ]
                },
            )
        return _search_handler(request)

    out = await _tool(handler).playlist_tracks(name="chill evenings")
    assert out["playlist"] == "Chill evenings"
    assert out["tracks"] == [
        {"title": "Angels", "artist": "Robbie Williams", "video_id": "v9", "url": song_url("v9")}
    ]


# -- auth + Google's refusals -------------------------------------------------


async def test_401_triggers_one_refresh_and_retry():
    current = {"value": "old"}
    calls: list[str] = []

    async def refresher() -> bool:
        current["value"] = "new"
        return True

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Authorization"])
        if request.headers["Authorization"] == "Bearer old":
            return httpx.Response(401, json={"error": {"code": 401, "message": "expired"}})
        return httpx.Response(200, json=_MY_PLAYLISTS)

    tool = YouTubeMusicRestTool(
        access_token_provider=lambda: current["value"],
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
        media=FakeMedia(),
        opener=Opener(),
    )
    out = await tool.list_playlists()
    assert calls == ["Bearer old", "Bearer new"]
    assert len(out["playlists"]) == 2


async def test_failed_refresh_asks_to_reconnect():
    async def refresher() -> bool:
        return False

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401}})

    out = await _tool(handler, refresher=refresher).list_playlists()
    assert "Reconnect" in out["error"]


async def test_execute_explains_search_quota_api_disabled_and_scope():
    def quota(request: httpx.Request) -> httpx.Response:
        return _google_error(403, "quotaExceeded")

    res = await _tool(quota).execute({"action": "search", "query": "x"}, ctx=None)  # type: ignore[arg-type]
    assert not res.success and "100 YouTube searches" in res.error

    def disabled(request: httpx.Request) -> httpx.Response:
        return _google_error(403, "accessNotConfigured", "YouTube Data API v3 has not been used")

    res = await _tool(disabled).execute({"action": "list_playlists"}, ctx=None)  # type: ignore[arg-type]
    assert "not enabled" in res.error

    def scope(request: httpx.Request) -> httpx.Response:
        return _google_error(403, "insufficientPermissions")

    res = await _tool(scope).execute({"action": "liked_songs"}, ctx=None)  # type: ignore[arg-type]
    assert "approve the YouTube permission" in res.error


# -- tool protocol ------------------------------------------------------------


def test_risk_tiers_reads_safe_everything_else_monitor():
    tool = YouTubeMusicRestTool(access_token_provider=lambda: None, media=FakeMedia())
    for action in ("now_playing", "search", "list_playlists", "playlist_tracks", "liked_songs"):
        assert tool.risk_tier_for_args({"action": action}) == "safe"
    for action in ("play", "pause", "next", "previous", "open", "like", "add_to_playlist"):
        assert tool.risk_tier_for_args({"action": action}) == "monitor"
    assert tool.risk_tier_for_args({"action": "nuke"}) == "ask"


async def test_execute_routes_and_validates():
    opener = Opener()
    tool = _tool(_search_handler, media=FakeMedia(now=_playing()), opener=opener)
    res = await tool.execute({"action": "open"}, ctx=None)  # type: ignore[arg-type]
    assert res.success and opener.urls == [HOME_URL]
    res = await tool.execute({"action": "search"}, ctx=None)  # type: ignore[arg-type]
    assert not res.success and "needs a query" in res.error
    res = await tool.execute({"action": "playlist_tracks"}, ctx=None)  # type: ignore[arg-type]
    assert not res.success and "playlist name" in res.error
    res = await tool.execute({"action": "teleport"}, ctx=None)  # type: ignore[arg-type]
    assert not res.success and "unknown action" in res.error
    res = await tool.execute({"action": "now_playing"}, ctx=None)  # type: ignore[arg-type]
    assert res.success and res.output["track"] == "Karma Police"
