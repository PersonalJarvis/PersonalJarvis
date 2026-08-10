"""WindowsPycawDucker: mute mode, volume mode, allowlist and restore.

pycaw is imported lazily inside the methods, so a fake ``pycaw.pycaw`` module
covers the whole decision logic on any OS — no Core Audio, no Windows host.
"""
from __future__ import annotations

import sys
import types

import pytest

from jarvis.audio.ducking.windows import WindowsPycawDucker


class _FakeVolume:
    def __init__(self, master: float = 1.0, muted: bool = False) -> None:
        self.master = float(master)
        self.muted = bool(muted)

    def GetMute(self) -> int:  # noqa: N802 — mirrors the pycaw COM name
        return int(self.muted)

    def SetMute(self, value: int, _ctx: object) -> None:  # noqa: N802
        self.muted = bool(value)

    def GetMasterVolume(self) -> float:  # noqa: N802
        return self.master

    def SetMasterVolume(self, value: float, _ctx: object) -> None:  # noqa: N802
        self.master = float(value)


class _FakeProcess:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


class _FakeSession:
    def __init__(
        self,
        pid: int,
        process_name: str | None = None,
        *,
        master: float = 1.0,
        muted: bool = False,
    ) -> None:
        self.ProcessId = pid
        self.Process = _FakeProcess(process_name) if process_name else None
        self.SimpleAudioVolume = _FakeVolume(master=master, muted=muted)


@pytest.fixture
def fake_pycaw(monkeypatch):
    """Install a fake ``pycaw.pycaw`` whose session list the test controls."""
    sessions: list[_FakeSession] = []
    module = types.ModuleType("pycaw.pycaw")
    module.AudioUtilities = types.SimpleNamespace(
        GetAllSessions=lambda: list(sessions),
    )
    package = types.ModuleType("pycaw")
    package.pycaw = module
    monkeypatch.setitem(sys.modules, "pycaw", package)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", module)
    return sessions


# --------------------------------------------------------------------------- #
# Mute mode (duck_volume_percent = 0, the default)                             #
# --------------------------------------------------------------------------- #


def test_mutes_other_sessions_and_restores_them(fake_pycaw):
    other = _FakeSession(4321, "spotify.exe")
    fake_pycaw.extend([other])
    ducker = WindowsPycawDucker()

    assert ducker.mute_others(own_pid=111, never=frozenset()) == [4321]
    assert other.SimpleAudioVolume.muted is True

    ducker.restore([4321])
    assert other.SimpleAudioVolume.muted is False


def test_own_pid_and_system_session_are_never_touched(fake_pycaw):
    system = _FakeSession(0)
    own = _FakeSession(111, "pythonw.exe")
    fake_pycaw.extend([system, own])

    assert WindowsPycawDucker().mute_others(own_pid=111, never=frozenset()) == []
    assert system.SimpleAudioVolume.muted is False
    assert own.SimpleAudioVolume.muted is False


def test_already_muted_session_is_left_alone_and_not_unmuted(fake_pycaw):
    """A session the USER muted must never come back on through restore().

    The guarantee is carried by the token list: an already-silent session is
    not reported by ``mute_others``, so the controller — which only ever hands
    back what it received — never asks restore() about it.
    """
    user_muted = _FakeSession(4321, "spotify.exe", muted=True)
    audible = _FakeSession(4322, "chrome.exe")
    fake_pycaw.extend([user_muted, audible])
    ducker = WindowsPycawDucker()

    ducked = ducker.mute_others(own_pid=1, never=frozenset())
    assert ducked == [4322]

    ducker.restore(ducked)
    assert user_muted.SimpleAudioVolume.muted is True
    assert audible.SimpleAudioVolume.muted is False


@pytest.mark.parametrize(
    "entry", ["Discord.exe", "discord.exe", "Discord", "discord", "Discord.app"],
)
def test_never_list_matches_case_and_suffix_insensitively(fake_pycaw, entry):
    """One allowlist entry means the same thing however the user wrote it."""
    protected = _FakeSession(900, "Discord.exe")
    other = _FakeSession(901, "chrome.exe")
    fake_pycaw.extend([protected, other])

    ducked = WindowsPycawDucker().mute_others(own_pid=1, never=frozenset({entry}))

    assert ducked == [901]
    assert protected.SimpleAudioVolume.muted is False


def test_session_without_a_process_object_is_still_ducked(fake_pycaw):
    """No Process handle (protected session) must not skip the duck entirely."""
    headless = _FakeSession(700, None)
    fake_pycaw.extend([headless])

    ducked = WindowsPycawDucker().mute_others(own_pid=1, never=frozenset({"discord"}))

    assert ducked == [700]


def test_a_failing_session_does_not_abort_the_sweep(fake_pycaw):
    class _Exploding(_FakeSession):
        @property
        def SimpleAudioVolume(self):  # noqa: N802
            raise OSError("COMError: session is protected")

        @SimpleAudioVolume.setter
        def SimpleAudioVolume(self, _value):  # noqa: N802
            pass

    good = _FakeSession(802, "chrome.exe")
    fake_pycaw.extend([_Exploding(801, "protected.exe"), good])

    assert WindowsPycawDucker().mute_others(own_pid=1, never=frozenset()) == [802]
    assert good.SimpleAudioVolume.muted is True


# --------------------------------------------------------------------------- #
# Volume mode (duck_volume_percent > 0) — the setting used to be ignored       #
# --------------------------------------------------------------------------- #


def test_duck_volume_percent_lowers_instead_of_muting(fake_pycaw):
    session = _FakeSession(4321, "spotify.exe", master=0.8)
    fake_pycaw.extend([session])
    ducker = WindowsPycawDucker(duck_volume_percent=20)

    assert ducker.mute_others(own_pid=1, never=frozenset()) == [4321]
    assert session.SimpleAudioVolume.master == pytest.approx(0.2)
    assert session.SimpleAudioVolume.muted is False  # never hard-muted


def test_duck_volume_percent_restores_the_previous_level(fake_pycaw):
    session = _FakeSession(4321, "spotify.exe", master=0.8)
    fake_pycaw.extend([session])
    ducker = WindowsPycawDucker(duck_volume_percent=20)

    ducker.restore(ducker.mute_others(own_pid=1, never=frozenset()))

    assert session.SimpleAudioVolume.master == pytest.approx(0.8)
    assert ducker._saved == {}


def test_session_already_below_the_duck_level_is_not_touched(fake_pycaw):
    quiet = _FakeSession(4321, "spotify.exe", master=0.1)
    fake_pycaw.extend([quiet])

    ducker = WindowsPycawDucker(duck_volume_percent=20)

    assert ducker.mute_others(own_pid=1, never=frozenset()) == []
    assert quiet.SimpleAudioVolume.master == pytest.approx(0.1)


def test_restore_of_a_vanished_session_drops_its_saved_level(fake_pycaw):
    """A ducked app that quits must not leave state a pid reuse could hit."""
    session = _FakeSession(4321, "spotify.exe", master=0.8)
    fake_pycaw.extend([session])
    ducker = WindowsPycawDucker(duck_volume_percent=20)
    ducked = ducker.mute_others(own_pid=1, never=frozenset())

    fake_pycaw.clear()  # the app exited while ducked
    ducker.restore(ducked)

    assert ducker._saved == {}


def test_restore_uses_what_was_recorded_not_the_current_mode(fake_pycaw):
    """Turning the duck-level knob mid-session must still undo cleanly."""
    session = _FakeSession(4321, "spotify.exe", master=0.8)
    fake_pycaw.extend([session])
    ducker = WindowsPycawDucker()  # mute mode
    ducked = ducker.mute_others(own_pid=1, never=frozenset())

    ducker._duck = 20  # user moves the slider before the session ends
    ducker.restore(ducked)

    assert session.SimpleAudioVolume.muted is False
    assert session.SimpleAudioVolume.master == pytest.approx(0.8)


# --------------------------------------------------------------------------- #
# A restore that never landed must not strand the user's audio                 #
# --------------------------------------------------------------------------- #


def test_a_still_ducked_session_is_re_adopted_by_the_next_turn(fake_pycaw):
    """Volume mode: the app sat at the duck level FOREVER after a failed undo.

    The next sweep read the already-lowered volume, ``prev > target`` stayed
    false, so the pid was never reported again — and restore() is only ever
    asked about pids that mute_others returned.
    """
    session = _FakeSession(4321, "spotify.exe", master=0.8)
    fake_pycaw.extend([session])
    ducker = WindowsPycawDucker(duck_volume_percent=20)

    ducker.mute_others(own_pid=1, never=frozenset())     # 0.8 -> 0.2
    # ...and the restore never happens (Jarvis killed, COMError, shutdown race).

    second = ducker.mute_others(own_pid=1, never=frozenset())
    assert second == [4321]

    ducker.restore(second)
    assert session.SimpleAudioVolume.master == pytest.approx(0.8)
    assert ducker._saved == {}


def test_a_session_we_muted_is_re_adopted_but_a_user_muted_one_is_not(fake_pycaw):
    """Mute mode had the same hole, and the harsher symptom: silence for good."""
    ours = _FakeSession(4321, "spotify.exe")
    theirs = _FakeSession(4322, "chrome.exe", muted=True)
    fake_pycaw.extend([ours, theirs])
    ducker = WindowsPycawDucker()

    assert ducker.mute_others(own_pid=1, never=frozenset()) == [4321]
    # No restore happens; both sessions now read back as muted.

    assert ducker.mute_others(own_pid=1, never=frozenset()) == [4321]

    ducker.restore([4321])
    assert ours.SimpleAudioVolume.muted is False
    assert theirs.SimpleAudioVolume.muted is True    # never ours to touch


def test_restore_forgets_a_session_it_successfully_undid(fake_pycaw):
    """The re-adoption record must not survive a restore that DID land —
    otherwise a later pid reuse would unmute a stranger."""
    session = _FakeSession(4321, "spotify.exe")
    fake_pycaw.extend([session])
    ducker = WindowsPycawDucker()

    ducker.restore(ducker.mute_others(own_pid=1, never=frozenset()))

    assert ducker._muted_by_us == set()
    assert ducker.mute_others(own_pid=1, never=frozenset()) == [4321]


def test_one_pid_with_several_sessions_yields_one_token(fake_pycaw):
    """A process can own more than one audio session; reporting the pid once
    per session inflated the "muted N sessions" count the controller logs."""
    fake_pycaw.extend([
        _FakeSession(4321, "chrome.exe"),
        _FakeSession(4321, "chrome.exe"),
    ])

    assert WindowsPycawDucker().mute_others(own_pid=1, never=frozenset()) == [4321]


# --------------------------------------------------------------------------- #
# Config wiring                                                                #
# --------------------------------------------------------------------------- #


def test_from_config_reads_the_duck_level():
    cfg = types.SimpleNamespace(
        ducking=types.SimpleNamespace(duck_volume_percent=35),
    )
    assert WindowsPycawDucker.from_config(cfg)._duck == 35


def test_from_config_without_config_defaults_to_mute_mode():
    assert WindowsPycawDucker.from_config(None)._duck == 0


def test_from_config_clamps_out_of_range_values():
    cfg = types.SimpleNamespace(
        ducking=types.SimpleNamespace(duck_volume_percent=500),
    )
    assert WindowsPycawDucker.from_config(cfg)._duck == 100


def test_factory_threads_cfg_into_the_windows_backend(monkeypatch):
    from jarvis.audio.ducking.factory import make_audio_ducker

    monkeypatch.setattr("jarvis.audio.ducking.factory.sys.platform", "win32")
    monkeypatch.setattr(
        "jarvis.audio.ducking.factory._pycaw_available", lambda: True,
    )
    cfg = types.SimpleNamespace(
        ducking=types.SimpleNamespace(duck_volume_percent=30),
    )

    ducker = make_audio_ducker(cfg)

    assert isinstance(ducker, WindowsPycawDucker)
    assert ducker._duck == 30
