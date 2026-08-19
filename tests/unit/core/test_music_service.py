"""The one resolver that decides Spotify vs YouTube Music (2026-08-18).

Named service wins, then the preference, then the only connected one, then
whatever matched — and the tool-description hint says the same thing, so the
skill capture and the LLM router can never disagree.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.core import music_service as ms
from jarvis.core.config import JarvisConfig, MusicConfig
from jarvis.core.music_constants import (
    MUSIC_PLAYBACK_MODES,
    MUSIC_SERVICES,
)


def test_named_service_wins_over_everything():
    assert ms.explicit_music_service("spiel karma police auf spotify") == "spotify"
    assert ms.explicit_music_service("play it on youtube music") == "youtube_music"
    assert ms.explicit_music_service("spiel das auf yt music") == "youtube_music"  # i18n-allow
    assert ms.explicit_music_service("spiel das auf youtube") == "youtube_music"  # i18n-allow
    assert ms.explicit_music_service("spiel musik") is None
    # naming both is not naming one
    assert ms.explicit_music_service("spotify oder youtube music?") is None
    both = ("spotify", "youtube_music")
    assert ms.resolve_music_service(
        "spiel radiohead auf youtube music", preferred="spotify", connected=both, matched="spotify"
    ) == "youtube_music"


def test_preference_applies_only_when_that_service_is_connected():
    both = ("spotify", "youtube_music")
    assert ms.resolve_music_service("spiel musik", preferred="youtube_music", connected=both) == (
        "youtube_music"
    )
    assert ms.resolve_music_service(
        "spiel musik", preferred="youtube_music", connected=("spotify",), matched="spotify"
    ) == "spotify"


def test_auto_takes_the_only_connected_service_else_the_match():
    def auto(connected, matched=None):
        return ms.resolve_music_service(
            "spiel musik", preferred="auto", connected=connected, matched=matched
        )

    both = ("spotify", "youtube_music")
    assert auto(("youtube_music",)) == "youtube_music"
    assert auto(both, matched="spotify") == "spotify"
    assert auto(both) == "spotify"
    assert auto(()) is None
    assert auto((), matched="spotify") == "spotify"


def test_preference_hint_only_when_both_connected_and_a_preference_exists():
    both = ("spotify", "youtube_music")
    assert ms.preference_hint("spotify", preferred="auto", connected=both) == ""
    only_spotify = ms.preference_hint(
        "spotify", preferred="youtube_music", connected=("spotify",)
    )
    assert "connected music service" in only_spotify
    pro = ms.preference_hint("youtube_music", preferred="youtube_music", connected=both)
    con = ms.preference_hint("spotify", preferred="youtube_music", connected=both)
    assert "prefers YouTube Music" in pro and "does not name another service" in pro
    assert "prefers YouTube Music" in con and "names Spotify explicitly" in con


def test_preference_hint_when_only_one_service_is_connected():
    live = ("youtube_music",)
    assert "connected music service" in ms.preference_hint(
        "youtube_music", preferred="auto", connected=live
    )
    dead = ms.preference_hint("spotify", preferred="auto", connected=live)
    assert "not connected" in dead and "YouTube Music" in dead


def test_compose_tool_description_puts_the_hint_first(monkeypatch):
    monkeypatch.setattr(ms, "description_hint", lambda _sid: " HINT.")
    composed = ms.compose_tool_description("spotify", "Play songs.")
    assert composed.startswith("HINT.") and composed.endswith("Play songs.")


def test_reroute_music_tool_sends_unnamed_requests_to_the_preferred_service(
    monkeypatch,
):
    monkeypatch.setattr(ms, "preferred_music_service", lambda: "youtube_music")
    monkeypatch.setattr(
        ms, "connected_music_services", lambda: ("spotify", "youtube_music")
    )
    unnamed = "Mach einfach irgendwie Schönes, was mir gefällt."  # i18n-allow
    assert ms.reroute_music_tool("spotify", unnamed) == "youtube_music"
    named = "spiel das auf spotify"  # i18n-allow: spoken-input sample
    assert ms.reroute_music_tool("youtube_music", named) == "spotify"
    assert ms.reroute_music_tool("search_web", unnamed) == "search_web"


def test_reroute_music_tool_uses_the_only_connected_service(monkeypatch):
    monkeypatch.setattr(ms, "preferred_music_service", lambda: "auto")
    monkeypatch.setattr(ms, "connected_music_services", lambda: ("youtube_music",))
    assert ms.reroute_music_tool("spotify", "play a nice song") == "youtube_music"


def test_adapt_music_arguments_taste_request_plays_liked_songs():
    text = "Mach einfach irgendwie Schönes, was mir gefällt."  # i18n-allow
    out = ms.adapt_music_arguments(
        text, source="spotify", target="youtube_music", args={"action": "play"}
    )
    assert out["action"] == "play" and out["type"] == "liked"
    titled = ms.adapt_music_arguments(
        text,
        source="spotify",
        target="youtube_music",
        args={"action": "play", "query": "Karma Police", "type": "track"},
    )
    assert titled["type"] == "song" and titled["query"] == "Karma Police"


def test_connected_music_services_reads_the_store_and_isolates_faults():
    class Store:
        def load(self, plugin_id):
            if plugin_id == "spotify":
                raise RuntimeError("corrupt")
            return SimpleNamespace(access="tok", needs_reauth=False)

    assert ms.connected_music_services(store=Store()) == ("youtube_music",)

    class Empty:
        def load(self, plugin_id):
            return None

    assert ms.connected_music_services(store=Empty()) == ()


def test_music_config_defaults_and_sanitising():
    cfg = JarvisConfig()
    assert cfg.music.preferred_service == "auto" and cfg.music.playback == "background"
    assert MusicConfig(preferred_service="Nope", playback="???").preferred_service == "auto"
    assert MusicConfig(playback="Browser").playback == "browser"
    assert set(MUSIC_SERVICES) == {"auto", "spotify", "youtube_music"}
    assert set(MUSIC_PLAYBACK_MODES) == {"background", "browser"}


def test_description_hint_reads_the_cached_preference(monkeypatch):
    import jarvis.core.config as config_mod

    calls = {"n": 0}

    def fake_load_config():
        calls["n"] += 1
        return SimpleNamespace(music=SimpleNamespace(preferred_service="youtube_music"))

    monkeypatch.setattr(config_mod, "load_config", fake_load_config)
    monkeypatch.setattr(ms, "connected_music_services", lambda: ("spotify", "youtube_music"))
    ms.forget_connected_music_services()
    assert "prefers YouTube Music" in ms.description_hint("youtube_music")
    assert "names Spotify explicitly" in ms.description_hint("spotify")
    assert calls["n"] == 1  # one config read for both descriptions
    ms.forget_connected_music_services()
    ms.description_hint("spotify")
    assert calls["n"] == 2  # a settings write drops the cache
    ms.forget_connected_music_services()
