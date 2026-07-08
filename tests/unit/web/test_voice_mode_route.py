"""Task 7 — the browser-voice connect gate, inverted to default OFF.

``_browser_voice_enabled`` now serves the /ws/audio socket only when the user
has explicitly opted into a voice surface: realtime mode ([voice].mode ==
"realtime") or the classic bridge ([browser_voice].enabled == True). A missing
[browser_voice] section is False, not True (the old default-ON contract).

NOTE: a later task (T8) appends settings-route handler tests to this same
file — keep additions here additive and self-contained.
"""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.browser_voice.route import _browser_voice_enabled


def test_gate_default_off_when_pipeline_and_no_browser_voice():
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="pipeline"))
    assert _browser_voice_enabled(cfg) is False


def test_gate_on_for_realtime_mode():
    cfg = SimpleNamespace(voice=SimpleNamespace(mode="realtime"))
    assert _browser_voice_enabled(cfg) is True


def test_gate_on_for_explicit_classic_browser_voice():
    cfg = SimpleNamespace(
        voice=SimpleNamespace(mode="pipeline"), browser_voice=SimpleNamespace(enabled=True)
    )
    assert _browser_voice_enabled(cfg) is True
