"""The both-Option appshot gesture: status text only, never a permission request.

Whether ``CGEventSourceKeyState`` needs Input Monitoring is UNVERIFIED (the repo
contradicted itself), so on a Mac the armed status carries a hint sentence and
nothing asks for a permission. Elsewhere the status text is unchanged.
"""

from __future__ import annotations

import pytest

import jarvis.platform as platform_mod
from jarvis.appshot import gesture as gesture_mod
from jarvis.appshot import hotkey as hotkey_mod
from jarvis.appshot.hotkey import BOTH_ALT, BOTH_OPTION_MAC_NOTE, AppshotShortcut
from tests.fakes.fake_tcc import install_port, make_darwin_port


class _Watcher:
    def __init__(self, _on_fire, *, probe, together_s=None) -> None:  # noqa: ANN001
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        return None


async def _arm(monkeypatch: pytest.MonkeyPatch, host: str):
    monkeypatch.setattr(platform_mod, "detect_platform", lambda: host)
    monkeypatch.setattr(
        gesture_mod, "make_probe", lambda _family="alt": (lambda: (False, False), "")
    )
    monkeypatch.setattr(gesture_mod, "BothKeysWatcher", _Watcher)
    shortcut = AppshotShortcut(bus=object())
    return await shortcut._arm_gesture("window", BOTH_ALT)


async def test_a_mac_gets_the_hint_and_no_permission_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, tcc = make_darwin_port()
    install_port(monkeypatch, port)
    status = await _arm(monkeypatch, "darwin")
    assert (status.hotkey, status.armed) == (BOTH_ALT, True)
    assert status.detail == BOTH_OPTION_MAC_NOTE
    # Its permission need is unverified, so the hint sends nobody to grant one.
    assert "key combination" in status.detail and "Input Monitoring" not in status.detail
    assert tcc.requests() == [] and tcc.implicit_prompts() == []
    assert tcc.probes() == [], "the gesture's status text reads no permission at all"


@pytest.mark.parametrize("host", ["win32", "linux"])
async def test_other_hosts_keep_an_empty_status_text(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    status = await _arm(monkeypatch, host)
    assert status.armed is True
    assert status.detail == ""


def test_the_note_never_sends_the_user_to_a_removed_settings_page() -> None:
    assert "Settings > Permissions" not in BOTH_OPTION_MAC_NOTE
    assert hotkey_mod.BOTH_ALT == "alt+alt"
