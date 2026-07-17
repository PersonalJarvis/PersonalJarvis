"""MacOSScriptDucker: tiered AppleScript duck/restore with a fake runner."""
from __future__ import annotations

import subprocess

from jarvis.audio.ducking.macos import _MASTER_TOKEN, MacOSScriptDucker

_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"


class FakeRunner:
    """Records every script; returns scripted results per app / master.

    ``results`` maps a bundle id (or ``"master"``) to a stdout string, a
    ``CompletedProcess``, or an exception instance to raise.
    """

    def __init__(self, results: dict[str, object]):
        self.scripts: list[str] = []
        self._results = results

    def __call__(self, script: str) -> subprocess.CompletedProcess:
        self.scripts.append(script)
        for key, res in self._results.items():
            if key != "master" and key in script:
                return self._result(res)
        if "output volume" in script:
            return self._result(self._results.get("master", "50"))
        raise AssertionError(f"unexpected script: {script}")

    @staticmethod
    def _result(res: object) -> subprocess.CompletedProcess:
        if isinstance(res, BaseException):
            raise res
        if isinstance(res, subprocess.CompletedProcess):
            return res
        return subprocess.CompletedProcess(
            ["osascript"], 0, stdout=f"{res}\n", stderr=""
        )


def _ducker(runner: FakeRunner, **kwargs) -> MacOSScriptDucker:
    return MacOSScriptDucker(run=runner, **kwargs)


def test_ducks_running_players_and_skips_stopped_ones():
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "-"})
    d = _ducker(run)
    tokens = d.mute_others(own_pid=123, never=frozenset())
    assert tokens == [1]
    assert d._saved == {1: 65}  # Spotify not running → never tokenized


def test_every_script_carries_the_is_running_guard():
    # Regression pin: a bare tell-application LAUNCHES the app; every player
    # script must guard with "is running" inside the same script.
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "40"})
    d = _ducker(run)
    tokens = d.mute_others(own_pid=123, never=frozenset())
    d.restore(tokens)
    d.prewarm()
    assert run.scripts, "no scripts were run"
    assert all("is running" in s for s in run.scripts)


def test_restore_sets_previous_volume_and_clears_state():
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "-"})
    d = _ducker(run)
    assert d.mute_others(own_pid=1, never=frozenset()) == [1]
    d.restore([1])
    assert any("set sound volume to 65" in s for s in run.scripts)
    assert d._saved == {}


def test_restore_is_idempotent_and_unknown_token_is_noop():
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "-"})
    d = _ducker(run)
    d.mute_others(own_pid=1, never=frozenset())
    d.restore([1])
    calls_after_first = len(run.scripts)
    d.restore([1])  # already restored → no further osascript call
    d.restore([42])  # unknown token → no-op
    assert len(run.scripts) == calls_after_first


def test_never_mute_maps_names_case_insensitively_stripping_suffixes():
    for entry in ("Spotify", "spotify", "Spotify.exe", "Spotify.app"):
        run = FakeRunner({_MUSIC: "65", _SPOTIFY: "40"})
        d = _ducker(run)
        tokens = d.mute_others(own_pid=1, never=frozenset({entry}))
        assert tokens == [1], entry
        assert not any(_SPOTIFY in s for s in run.scripts), entry


def test_runner_timeout_skips_player_but_others_still_duck():
    run = FakeRunner(
        {
            _MUSIC: subprocess.TimeoutExpired(cmd="osascript", timeout=3.0),
            _SPOTIFY: "70",
        }
    )
    d = _ducker(run)
    tokens = d.mute_others(own_pid=1, never=frozenset())
    assert tokens == [2]
    assert d._saved == {2: 70}


def test_nonzero_returncode_skips_player_but_others_still_duck():
    # rc=1 covers the Automation TCC denial (-1743) shape as well.
    denied = subprocess.CompletedProcess(
        ["osascript"], 1, stdout="", stderr="Not authorized to send Apple events (-1743)"
    )
    run = FakeRunner({_MUSIC: denied, _SPOTIFY: "70"})
    d = _ducker(run)
    assert d.mute_others(own_pid=1, never=frozenset()) == [2]


def test_master_fallback_off_and_no_players_ducks_nothing():
    run = FakeRunner({_MUSIC: "-", _SPOTIFY: "-"})
    d = _ducker(run, master_fallback=False)
    assert d.mute_others(own_pid=1, never=frozenset()) == []
    assert not any("output volume" in s for s in run.scripts)


def test_master_fallback_on_and_no_players_ducks_master_and_restores():
    run = FakeRunner({_MUSIC: "-", _SPOTIFY: "-", "master": "80"})
    d = _ducker(run, master_fallback=True)
    tokens = d.mute_others(own_pid=1, never=frozenset())
    assert tokens == [_MASTER_TOKEN]
    assert d._saved == {_MASTER_TOKEN: 80}
    assert any("set volume output volume" in s for s in run.scripts)
    d.restore(tokens)
    assert any("set volume output volume 80" in s for s in run.scripts)
    assert d._saved == {}


def test_master_fallback_untouched_when_a_player_was_ducked():
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "-", "master": "80"})
    d = _ducker(run, master_fallback=True)
    assert d.mute_others(own_pid=1, never=frozenset()) == [1]
    assert not any("output volume" in s for s in run.scripts)


def test_already_quiet_player_is_not_tokenized():
    run = FakeRunner({_MUSIC: "0", _SPOTIFY: "-"})
    d = _ducker(run, duck_volume_percent=0)
    assert d.mute_others(own_pid=1, never=frozenset()) == []
    assert d._saved == {}
