"""make_audio_ducker selects NullDucker when pycaw/Windows unavailable."""
from __future__ import annotations

from jarvis.audio.ducking.factory import make_audio_ducker
from jarvis.audio.ducking.null import NullDucker


def test_factory_returns_null_when_pycaw_absent(monkeypatch):
    monkeypatch.setattr(
        "jarvis.audio.ducking.factory._pycaw_available", lambda: False
    )
    assert isinstance(make_audio_ducker(), NullDucker)


def test_factory_returns_null_off_windows(monkeypatch):
    monkeypatch.setattr("jarvis.audio.ducking.factory.sys.platform", "linux")
    assert isinstance(make_audio_ducker(), NullDucker)


def test_null_ducker_is_noop():
    d = NullDucker()
    assert d.mute_others(own_pid=123, never=frozenset()) == []
    d.restore([1, 2, 3])  # must not raise
