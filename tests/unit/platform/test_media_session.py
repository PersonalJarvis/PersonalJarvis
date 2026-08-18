"""Unit tests for the OS media-session seam (``jarvis/platform/media_session``).

Every backend is driven through its injectable fake (a WinRT-shaped manager,
a CLI runner) — no real player, no mocks. The picker is the part that decides
whether "pause the music" hits YouTube Music or a paused YouTube tab, so it
gets the most attention.
"""
from __future__ import annotations

from datetime import timedelta

from jarvis.platform.media_session import (
    LinuxMediaSession,
    MacMediaSession,
    NowPlaying,
    NullMediaSession,
    WindowsMediaSession,
    app_label,
    is_browser_identifier,
    pick_session,
)

# -- shared helpers -----------------------------------------------------------


def _np(title: str, app: str, status: str, browser: bool, ytm: bool = False) -> NowPlaying:
    return NowPlaying(
        title=title,
        artist="a",
        album="",
        app=app,
        status=status,  # type: ignore[arg-type]
        is_browser=browser,
        is_youtube_music_app=ytm,
    )


def test_app_label_maps_raw_identifiers_to_names():
    assert app_label("Chrome") == "Google Chrome"
    assert app_label("MSEdge") == "Microsoft Edge"
    assert app_label("Chrome._crx_cinhimbnkkaeohfgghhklpknlkffjgod") == "YouTube Music app"
    assert app_label("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify") == "Spotify"
    assert app_label("") == "unknown app"
    assert app_label("SomeUnknownPlayer") == "SomeUnknownPlayer"


def test_browser_identifier_detection():
    assert is_browser_identifier("Chrome")
    assert is_browser_identifier("firefox.instance1234")
    assert is_browser_identifier("Chrome._crx_cinhimbnkkaeohfgghhklpknlkffjgod")
    assert not is_browser_identifier("Spotify.exe")


def test_pick_session_prefers_playing_browser_over_current_paused_tab():
    """Two Chrome tabs: a paused YouTube video is the OS's "current" session,
    YouTube Music plays in the other. The music wins."""
    paused_video = _np("Some video", "Google Chrome", "paused", browser=True)
    music = _np("Karma Police", "Google Chrome", "playing", browser=True)
    assert pick_session([(paused_video, True), (music, False)]) is music


def test_pick_session_prefers_browser_over_other_player():
    spotify = _np("x", "Spotify", "playing", browser=False)
    tab = _np("y", "Google Chrome", "paused", browser=True)
    assert pick_session([(spotify, True), (tab, False)]) is tab


def test_pick_session_prefers_the_youtube_music_app_over_a_tab():
    tab = _np("y", "Google Chrome", "playing", browser=True)
    app = _np("z", "YouTube Music app", "paused", browser=True, ytm=True)
    assert pick_session([(tab, True), (app, False)]) is app


def test_pick_session_uses_current_as_tiebreak_and_handles_empty():
    a = _np("a", "Google Chrome", "paused", browser=True)
    b = _np("b", "Google Chrome", "paused", browser=True)
    assert pick_session([(a, False), (b, True)]) is b
    assert pick_session([]) is None


def test_now_playing_as_dict_is_speech_sized():
    entry = NowPlaying("T", "A", "Al", "Google Chrome", "playing", 12.4, 200.6)
    out = entry.as_dict()
    assert out == {
        "is_playing": True,
        "status": "playing",
        "track": "T",
        "artist": "A",
        "app": "Google Chrome",
        "album": "Al",
        "position_s": 12,
        "duration_s": 201,
    }


# -- Windows (WinRT-shaped fakes) --------------------------------------------


class _Props:
    def __init__(self, title: str, artist: str, album: str = "") -> None:
        self.title, self.artist, self.album_title = title, artist, album


class _Playback:
    def __init__(self, status: int) -> None:
        self.playback_status = status


class _Timeline:
    def __init__(self) -> None:
        self.position = timedelta(seconds=30)
        self.end_time = timedelta(seconds=180)


class _FakeSession:
    def __init__(self, aumid: str, title: str, artist: str, status: int) -> None:
        self.source_app_user_model_id = aumid
        self._props = _Props(title, artist)
        self._status = status
        self.calls: list[str] = []

    async def try_get_media_properties_async(self):
        return self._props

    def get_playback_info(self):
        return _Playback(self._status)

    def get_timeline_properties(self):
        return _Timeline()

    async def try_pause_async(self):
        self.calls.append("pause")
        self._status = 5
        return True

    async def try_play_async(self):
        self.calls.append("play")
        self._status = 4
        return True

    async def try_toggle_play_pause_async(self):
        self.calls.append("toggle")
        return True

    async def try_skip_next_async(self):
        self.calls.append("next")
        return True

    async def try_skip_previous_async(self):
        self.calls.append("previous")
        return True


class _FakeManager:
    def __init__(self, sessions: list[_FakeSession], current: _FakeSession | None) -> None:
        self._sessions = sessions
        self._current = current

    def get_current_session(self):
        return self._current

    def get_sessions(self):
        return list(self._sessions)


def _windows(sessions, current):
    async def factory():
        return _FakeManager(sessions, current)

    return WindowsMediaSession(manager_factory=factory, key_sender=lambda vk: True)


async def test_windows_reads_the_current_session_once_despite_shared_app_id():
    """Chrome registers every tab under the same app id — the current one must
    not be double-counted or confused with a sibling tab."""
    video = _FakeSession("Chrome", "Some video", "Channel", 5)
    music = _FakeSession("Chrome", "Karma Police", "Radiohead", 4)
    backend = _windows([video, music], current=video)
    rows = await backend._sessions()
    assert [(e.title, is_current) for e, is_current, _ in rows] == [
        ("Some video", True),
        ("Karma Police", False),
    ]
    now = await backend.now_playing()
    assert now is not None and now.title == "Karma Police" and now.is_playing
    assert now.app == "Google Chrome" and now.position_s == 30.0


async def test_windows_pause_targets_the_picked_session_not_the_current_one():
    video = _FakeSession("Chrome", "Some video", "Channel", 5)
    music = _FakeSession("Chrome", "Karma Police", "Radiohead", 4)
    backend = _windows([video, music], current=video)
    assert await backend.pause() is True
    assert music.calls == ["pause"] and video.calls == []


async def test_windows_next_and_previous_reach_the_session():
    music = _FakeSession("Chrome", "Karma Police", "Radiohead", 4)
    backend = _windows([music], current=music)
    assert await backend.next() is True
    assert await backend.previous() is True
    assert music.calls == ["next", "previous"]


async def test_windows_capability_and_no_sessions():
    backend = _windows([], current=None)
    cap = await backend.capability()
    assert cap.can_read and cap.can_control and cap.backend == "winrt"
    assert await backend.now_playing() is None
    assert await backend.pause() is False  # WinRT present, nothing registered: no blind key


async def test_windows_without_winrt_falls_back_to_media_keys():
    sent: list[int] = []

    def sender(vk: int) -> bool:
        sent.append(vk)
        return True

    backend = WindowsMediaSession(manager_factory=None, key_sender=sender)
    backend._winrt_checked = True  # pretend the import already failed
    backend._winrt_cls = None
    cap = await backend.capability()
    assert cap.can_read is False and cap.can_control is True and cap.backend == "media-keys"
    assert "desktop" in cap.note
    assert await backend.now_playing() is None
    assert await backend.pause() is True and sent == [0xB3]
    assert await backend.next() is True and sent[-1] == 0xB0


async def test_windows_survives_a_broken_manager():
    async def factory():
        raise RuntimeError("winrt exploded")

    backend = WindowsMediaSession(manager_factory=factory, key_sender=lambda vk: True)
    assert await backend.now_playing() is None
    assert await backend.pause() is False


# -- Linux (playerctl fakes) --------------------------------------------------


def _linux(outputs: dict[tuple[str, ...], tuple[int, str]], have_playerctl: bool = True):
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return outputs.get(tuple(argv), (1, ""))

    backend = LinuxMediaSession(
        runner=runner, which=lambda name: "/usr/bin/playerctl" if have_playerctl else None
    )
    return backend, calls


async def test_linux_reads_and_controls_the_browser_player():
    fmt = "{{title}}\x1f{{artist}}\x1f{{album}}\x1f{{position}}\x1f{{mpris:length}}"
    outputs = {
        ("playerctl", "-l"): (0, "spotify\nchromium.instance42\n"),
        ("playerctl", "-p", "spotify", "status"): (0, "Playing\n"),
        ("playerctl", "-p", "spotify", "metadata", "--format", fmt): (
            0,
            "Song\x1fBand\x1f\x1f10\x1f200000000\n",
        ),
        ("playerctl", "-p", "chromium.instance42", "status"): (0, "Paused\n"),
        ("playerctl", "-p", "chromium.instance42", "metadata", "--format", fmt): (
            0,
            "Karma Police\x1fRadiohead\x1fOK Computer\x1f31\x1f265000000\n",
        ),
        ("playerctl", "-p", "chromium.instance42", "pause"): (0, ""),
    }
    backend, calls = _linux(outputs)
    cap = await backend.capability()
    assert cap.can_read and cap.can_control and cap.backend == "playerctl"
    now = await backend.now_playing()
    assert now is not None
    assert now.title == "Karma Police" and now.app == "Chromium" and now.status == "paused"
    assert now.duration_s == 265.0 and now.position_s == 31.0
    assert await backend.pause() is True
    assert ["playerctl", "-p", "chromium.instance42", "pause"] in calls


async def test_linux_without_playerctl_degrades_honestly():
    backend, calls = _linux({}, have_playerctl=False)
    cap = await backend.capability()
    assert cap.can_read is False and cap.can_control is False
    assert "playerctl" in cap.note
    assert await backend.now_playing() is None
    assert await backend.next() is False
    assert calls == []


# -- macOS (nowplaying-cli fakes) --------------------------------------------


async def test_mac_reads_and_controls_via_nowplaying_cli():
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        if argv[:2] == ["nowplaying-cli", "get"]:
            return 0, "Karma Police\nRadiohead\nOK Computer\n1\n42.5\n265\n"
        return 0, ""

    backend = MacMediaSession(runner=runner, which=lambda name: "/opt/homebrew/bin/nowplaying-cli")
    now = await backend.now_playing()
    assert now is not None and now.title == "Karma Police" and now.is_playing
    assert now.position_s == 42.5 and now.duration_s == 265.0
    assert await backend.pause() is True
    assert calls[-1] == ["nowplaying-cli", "pause"]


async def test_mac_null_fields_and_missing_tool():
    def runner(argv):
        return 0, "null\nnull\nnull\n0\nnull\nnull\n"

    present = MacMediaSession(runner=runner, which=lambda name: "/x/nowplaying-cli")
    assert await present.now_playing() is None
    absent = MacMediaSession(runner=runner, which=lambda name: None)
    cap = await absent.capability()
    assert not cap.can_read and not cap.can_control and "brew install" in cap.note
    assert await absent.play() is False


async def test_null_backend_is_honest():
    backend = NullMediaSession("no desktop here")
    cap = await backend.capability()
    assert cap.backend == "none" and cap.note == "no desktop here"
    assert await backend.now_playing() is None
    assert await backend.toggle() is False
