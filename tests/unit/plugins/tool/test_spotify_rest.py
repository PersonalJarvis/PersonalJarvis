"""Unit tests for the Spotify REST tool.

Weighted towards the three failures Spotify actually produces in the wild — no
active device, a free account, and an access token that died after its hour —
because those are what decide whether the plugin is usable or merely present.
"""
from __future__ import annotations

import httpx

from jarvis.plugins.tool.spotify_rest import SpotifyRestTool

_TRACK = {
    "name": "Paranoid Android",
    "uri": "spotify:track:6LgJvl0Xdtc73RJ1mmpotq",
    "type": "track",
    "artists": [{"name": "Radiohead"}],
    "album": {"name": "OK Computer"},
}


_FAKE_TOKEN = "tok123"  # noqa: S105 — a literal for MockTransport, not a credential


def _tool(handler, token: str | None = _FAKE_TOKEN, refresher=None):
    return SpotifyRestTool(
        access_token_provider=lambda: token,
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )


def _no_device_response() -> httpx.Response:
    return httpx.Response(
        404,
        json={
            "error": {
                "status": 404,
                "message": "Player command failed: No active device found",
                "reason": "NO_ACTIVE_DEVICE",
            }
        },
    )


async def test_not_connected_without_token():
    tool = SpotifyRestTool(access_token_provider=lambda: None)
    out = await tool.now_playing()
    assert "not connected" in out["error"].lower()


async def test_now_playing_handles_empty_204_body():
    """Spotify answers 204 with no body when nothing is loaded. Parsing that as
    JSON is the crash this guards."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    out = await _tool(handler).now_playing()
    assert out["is_playing"] is False
    assert "Nothing is playing" in out["message"]


async def test_now_playing_summarizes_track_and_device():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "is_playing": True,
                "item": _TRACK,
                "device": {"name": "Küchenlautsprecher", "volume_percent": 40},
            },
        )

    out = await _tool(handler).now_playing()
    assert out["track"] == "Paranoid Android"
    assert out["artist"] == "Radiohead"
    assert out["album"] == "OK Computer"
    assert out["device"] == "Küchenlautsprecher"


async def test_play_searches_then_starts_track_as_uris():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"tracks": {"items": [_TRACK]}})
        assert request.url.path == "/v1/me/player/play"
        # A track must go out as `uris`; a context_uri here is a 400 from Spotify.
        assert b"uris" in request.content
        return httpx.Response(204)

    out = await _tool(handler).play(query="Paranoid Android")
    assert out["ok"] is True
    assert out["started"]["artist"] == "Radiohead"
    assert [m for m, _ in seen] == ["GET", "PUT"]


async def test_play_playlist_uses_context_uri():
    playlist = {"name": "Laufen", "uri": "spotify:playlist:42", "type": "playlist"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"playlists": {"items": [playlist]}})
        assert b"context_uri" in request.content
        return httpx.Response(204)

    out = await _tool(handler).play(query="Laufen", item_type="playlist")
    assert out["ok"] is True and out["started"]["name"] == "Laufen"


async def test_play_without_query_resumes_and_never_searches():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(204)

    out = await _tool(handler).play()
    assert out["resumed"] is True
    assert seen == ["/v1/me/player/play"]


async def test_play_reports_when_spotify_has_no_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tracks": {"items": []}})

    out = await _tool(handler).play(query="asdkjhasd")
    assert "nothing called" in out["error"]


async def test_no_active_device_adopts_the_only_open_one():
    """One open client is unambiguous, so the command is re-aimed rather than
    handed back to the user as an error."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path == "/v1/me/player/devices":
            return httpx.Response(
                200,
                json={"devices": [{"id": "dev1", "name": "Laptop", "is_active": False}]},
            )
        if "device_id=dev1" in str(request.url):
            return httpx.Response(204)
        return _no_device_response()

    out = await _tool(handler).pause()
    assert out["ok"] is True
    assert out["device"] == "Laptop"
    assert calls == [
        "/v1/me/player/pause",
        "/v1/me/player/devices",
        "/v1/me/player/pause",
    ]


async def test_no_active_device_with_several_asks_instead_of_guessing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/me/player/devices":
            return httpx.Response(
                200,
                json={
                    "devices": [
                        {"id": "a", "name": "Laptop", "is_active": False},
                        {"id": "b", "name": "Küche", "is_active": False},
                    ]
                },
            )
        return _no_device_response()

    out = await _tool(handler).pause()
    assert "several devices" in out["error"]
    assert {d["name"] for d in out["devices"]} == {"Laptop", "Küche"}


async def test_no_device_at_all_is_explained_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/me/player/devices":
            return httpx.Response(200, json={"devices": []})
        return _no_device_response()

    out = await _tool(handler).next_track()
    assert "not open anywhere" in out["error"]


async def test_free_account_403_names_premium_as_spotifys_rule():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"status": 403, "reason": "PREMIUM_REQUIRED"}})

    out = await _tool(handler).pause()
    assert "Premium" in out["error"]
    assert "Reading what is playing still works" in out["error"]


async def test_expired_token_refreshes_once_and_retries():
    tokens = iter(["stale", "fresh"])
    current = {"value": next(tokens)}
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization", ""))
        if request.headers.get("authorization") == "Bearer stale":
            return httpx.Response(401, json={"error": {"status": 401}})
        return httpx.Response(200, json={"is_playing": False, "item": {}})

    async def refresher() -> bool:
        current["value"] = next(tokens)
        return True

    tool = SpotifyRestTool(
        access_token_provider=lambda: current["value"],
        transport=httpx.MockTransport(handler),
        token_refresher=refresher,
    )
    out = await tool.now_playing()
    assert "error" not in out
    assert seen_auth == ["Bearer stale", "Bearer fresh"]


async def test_failed_refresh_asks_for_reconnect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"status": 401}})

    async def refresher() -> bool:
        return False

    out = await _tool(handler, refresher=refresher).now_playing()
    assert "Reconnect" in out["error"]


async def test_search_limit_is_capped_at_the_dev_mode_ceiling():
    """Spotify's Development Mode rejects limit > 10 with a 400."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"tracks": {"items": []}})

    await _tool(handler).search(query="x", limit=50)
    assert "limit=10" in seen["url"]


async def test_set_volume_clamps_to_range():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(204)

    out = await _tool(handler).set_volume(volume_percent=180)
    assert "volume_percent=100" in seen["url"]
    assert out["volume_percent"] == 100


async def test_queue_reports_what_was_queued():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"tracks": {"items": [_TRACK]}})
        return httpx.Response(204)

    out = await _tool(handler).queue(query="Paranoid Android")
    assert out["queued"]["name"] == "Paranoid Android"


async def test_execute_unknown_action_fails():
    res = await SpotifyRestTool(access_token_provider=lambda: "x").execute(
        {"action": "wipe_library"}, ctx=None  # type: ignore[arg-type]
    )
    assert not res.success


async def test_execute_set_volume_requires_a_level():
    res = await SpotifyRestTool(access_token_provider=lambda: "x").execute(
        {"action": "set_volume"}, ctx=None  # type: ignore[arg-type]
    )
    assert not res.success and "volume_percent" in (res.error or "")


async def test_execute_search_requires_a_query():
    res = await SpotifyRestTool(access_token_provider=lambda: "x").execute(
        {"action": "search"}, ctx=None  # type: ignore[arg-type]
    )
    assert not res.success and "query" in (res.error or "")
