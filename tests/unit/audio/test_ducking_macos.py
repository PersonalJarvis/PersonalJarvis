"""MacOSScriptDucker: tiered AppleScript duck/restore with a fake runner.

The permission layer is a ``FakePermissionService`` that reads Automation as GRANTED
(the duck/restore logic is the subject here); ``test_ducking_permissions.py`` covers
the permission behaviour against ``FakeTCC``.
"""
from __future__ import annotations

import subprocess

import pytest

from jarvis.audio.ducking import macos
from jarvis.audio.ducking.macos import _MASTER_TOKEN, MacOSScriptDucker
from jarvis.platform.permissions import PermissionId
from tests.fakes.fake_permission_service import FakePermissionService

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
    kwargs.setdefault("access_gate", FakePermissionService())  # Automation: granted
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


def test_master_fallback_skipped_when_a_running_player_is_already_quiet():
    """"Nothing was ducked" is not "no player is running".

    Music running at volume 0 needs no duck, so the tier is already handled.
    Falling through to the MASTER output there lowered Jarvis's own TTS — the
    side effect the fallback is opt-in to avoid — and silenced the answer the
    user was waiting for, for no gain.
    """
    run = FakeRunner({_MUSIC: "0", _SPOTIFY: "-", "master": "80"})
    d = _ducker(run, master_fallback=True, duck_volume_percent=0)
    assert d.mute_others(own_pid=1, never=frozenset()) == []
    assert not any("output volume" in s for s in run.scripts)
    assert d._saved == {}


def test_unrestored_player_is_re_adopted_by_the_next_session():
    """A duck whose restore never landed must not strand the user's volume.

    The player then sits AT the duck volume with our saved level still in
    ``_saved``: every later duck reads that already-ducked volume, so
    ``prev > duck`` stays false and the token is never reported again — the
    music stays silent for good. The next session has to re-adopt it.
    """
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "-"})
    d = _ducker(run)
    assert d.mute_others(own_pid=1, never=frozenset()) == [1]

    # Session 1 ends, but the restore osascript times out -> saved level stays.
    run._results[_MUSIC] = subprocess.TimeoutExpired(cmd="osascript", timeout=3.0)
    d.restore([1])
    assert d._saved == {1: 65}

    # Session 2: Music still reads 0 (ducked). It must come back as a token.
    run._results[_MUSIC] = "0"
    assert d.mute_others(own_pid=1, never=frozenset()) == [1]
    d.restore([1])
    assert any("set sound volume to 65" in s for s in run.scripts)
    assert d._saved == {}


def test_re_adoption_needs_a_saved_level_not_just_a_quiet_player():
    """A player the USER turned down must never be claimed as ours."""
    run = FakeRunner({_MUSIC: "0", _SPOTIFY: "-"})
    d = _ducker(run)
    assert d.mute_others(own_pid=1, never=frozenset()) == []
    assert d._saved == {}


def test_unrestored_master_is_re_adopted_by_the_next_session():
    """The stranded-restore shape exists for the MASTER output too.

    A master duck whose restore never landed leaves the WHOLE Mac output at
    the duck volume with our saved level still in ``_saved``; the next duck
    reads that already-ducked volume, ``prev > duck`` stays false, and without
    re-adoption no session ever restores it — the Mac stays quiet for good.
    """
    run = FakeRunner({_MUSIC: "-", _SPOTIFY: "-", "master": "80"})
    d = _ducker(run, master_fallback=True)
    assert d.mute_others(own_pid=1, never=frozenset()) == [_MASTER_TOKEN]

    # Session 1 ends, but the master restore osascript times out.
    def _raise(_script: str):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=3.0)

    original = d._run
    d._run = _raise
    d.restore([_MASTER_TOKEN])
    d._run = original
    assert d._saved == {_MASTER_TOKEN: 80}

    # Session 2: the master still reads the duck volume (0). It must come
    # back as a token so THIS session's restore can finally land.
    run._results["master"] = "0"
    assert d.mute_others(own_pid=1, never=frozenset()) == [_MASTER_TOKEN]
    run._results["master"] = "80"
    d.restore([_MASTER_TOKEN])
    assert any("set volume output volume 80" in s for s in run.scripts)
    assert d._saved == {}


def test_master_re_adoption_needs_a_saved_level_not_just_a_quiet_master():
    """An output volume the USER turned down must never be claimed as ours."""
    run = FakeRunner({_MUSIC: "-", _SPOTIFY: "-", "master": "0"})
    d = _ducker(run, master_fallback=True)
    assert d.mute_others(own_pid=1, never=frozenset()) == []
    assert d._saved == {}


def test_master_fallback_withheld_while_a_never_muted_player_is_running():
    """never_mute must beat the master fallback.

    The master output carries the protected player's audio too: with Spotify
    on the never list and running, ducking the master would silence exactly
    the app the user told us to leave alone.
    """
    run = FakeRunner({_MUSIC: "-", _SPOTIFY: "40", "master": "80"})
    d = _ducker(run, master_fallback=True)
    assert d.mute_others(own_pid=1, never=frozenset({"Spotify"})) == []
    assert not any("output volume" in s for s in run.scripts)
    assert d._saved == {}


def test_master_fallback_available_when_the_never_muted_player_is_not_running():
    """A merely-LISTED player must not disable the fallback for good."""
    run = FakeRunner({_MUSIC: "-", _SPOTIFY: "-", "master": "80"})
    d = _ducker(run, master_fallback=True)
    assert d.mute_others(own_pid=1, never=frozenset({"Spotify"})) == [_MASTER_TOKEN]
    assert d._saved == {_MASTER_TOKEN: 80}


def test_denied_restore_keeps_the_saved_level_for_re_adoption():
    """rc != 0 (a dismissed Automation prompt, -1743) is a restore that never
    landed — popping the saved level there stranded the duck for good, because
    re-adoption keys on exactly that saved level."""
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "-"})
    d = _ducker(run)
    assert d.mute_others(own_pid=1, never=frozenset()) == [1]

    run._results[_MUSIC] = subprocess.CompletedProcess(
        ["osascript"], 1, stdout="", stderr="Not authorized to send Apple events (-1743)"
    )
    d.restore([1])
    assert d._saved == {1: 65}

    # The user granted the prompt: the next session re-adopts and restores.
    run._results[_MUSIC] = "0"
    assert d.mute_others(own_pid=1, never=frozenset()) == [1]
    d.restore([1])
    assert any("set sound volume to 65" in s for s in run.scripts)
    assert d._saved == {}


def test_denied_master_restore_keeps_the_saved_level():
    """The same rc != 0 shape must keep the MASTER level re-adoptable."""
    run = FakeRunner({_MUSIC: "-", _SPOTIFY: "-", "master": "80"})
    d = _ducker(run, master_fallback=True)
    assert d.mute_others(own_pid=1, never=frozenset()) == [_MASTER_TOKEN]

    run._results["master"] = subprocess.CompletedProcess(
        ["osascript"], 1, stdout="", stderr="execution error (-1743)"
    )
    d.restore([_MASTER_TOKEN])
    assert d._saved == {_MASTER_TOKEN: 80}


# --------------------------------------------------------------------------- #
# Permission gating (FakePermissionService)                                    #
# --------------------------------------------------------------------------- #


def _music_scripts(run: FakeRunner) -> list[str]:
    return [s for s in run.scripts if _MUSIC in s]


def _music_scripts_with_a_send(run: FakeRunner) -> list[str]:
    return [s for s in _music_scripts(run) if "tell application" in s]


def test_a_player_without_a_live_grant_is_never_scripted_and_never_asked():
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "40"})
    gate = FakePermissionService({PermissionId.AUTOMATION: "pending"})
    d = _ducker(run, access_gate=gate)
    assert d.mute_others(own_pid=1, never=frozenset()) == []
    assert d._saved == {}
    # Only the pure running-state query ever reaches a player that was not granted.
    assert all("tell application" not in s for s in run.scripts)
    # The service was only READ, or told non-interactively: nothing could have asked.
    assert gate.native_free()
    assert {c.target for c in gate.check_calls()} == {_MUSIC, _SPOTIFY}


def test_a_miss_is_recorded_through_a_non_interactive_ensure_only_for_a_running_player():
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "-"})  # Spotify is not running
    gate = FakePermissionService({PermissionId.AUTOMATION: "pending"})
    d = _ducker(run, access_gate=gate)
    d.mute_others(own_pid=1, never=frozenset())
    calls = gate.ensure_calls(PermissionId.AUTOMATION)
    assert [(c.target, c.interactive, c.feature) for c in calls] == [
        (_MUSIC, False, "audio_ducking")
    ]


def test_a_player_the_service_calls_not_required_is_left_alone():
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "40"})
    gate = FakePermissionService({PermissionId.AUTOMATION: "not_required"})
    d = _ducker(run, access_gate=gate)
    assert d.mute_others(own_pid=1, never=frozenset()) == []
    assert run.scripts == []  # not installed (or not macOS): not even a probe
    assert gate.ensure_calls() == []


def test_master_fallback_still_applies_when_a_running_player_has_no_grant():
    run = FakeRunner({_MUSIC: "65", _SPOTIFY: "-", "master": "80"})
    gate = FakePermissionService({PermissionId.AUTOMATION: "denied"})
    d = _ducker(run, master_fallback=True, access_gate=gate)
    assert d.mute_others(own_pid=1, never=frozenset()) == [_MASTER_TOKEN]
    assert not _music_scripts_with_a_send(run)


def test_minus_1743_after_a_granted_read_skips_the_player_and_drops_the_cache():
    refused = subprocess.CompletedProcess(
        ["osascript"], 1, stdout="", stderr="Not authorized to send Apple events (-1743)"
    )

    class Gate(FakePermissionService):
        invalidated: list[object]

        def invalidate(self, permission=None):
            self.invalidated.append(permission)

    gate = Gate()
    gate.invalidated = []
    run = FakeRunner({_MUSIC: refused, _SPOTIFY: "70"})
    d = _ducker(run, access_gate=gate)
    assert d.mute_others(own_pid=1, never=frozenset()) == [2]  # Spotify still ducks
    assert gate.invalidated == [PermissionId.AUTOMATION]
    assert [c.interactive for c in gate.ensure_calls(PermissionId.AUTOMATION)] == [False]


def test_prewarm_asks_through_the_gate_for_running_players_only():
    run = FakeRunner({_MUSIC: "+", _SPOTIFY: "-"})
    gate = FakePermissionService({PermissionId.AUTOMATION: "granted"})
    report = _ducker(run, access_gate=gate).prewarm()
    calls = gate.ensure_calls(PermissionId.AUTOMATION)
    # The ask runs on a worker thread, so it waits for the dialog (budget minus a margin).
    (call,) = calls
    assert (call.target, call.interactive) == (_MUSIC, True)
    assert call.wait_s == pytest.approx(macos._ASK_BUDGET_S - macos._ASK_WAIT_MARGIN_S)
    assert [p.player for p in report.players] == ["Music"]
    assert report.not_running == ("Spotify",)
    assert all("is running" in s and "tell application" not in s for s in run.scripts)
