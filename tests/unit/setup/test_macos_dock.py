"""The installed app is in the Dock — once, and the user's choice after that.

Reported 2026-09-17: a finished install left the app out of the Dock, so the
only way to start it was to already know where it lived. These tests pin that
the installer adds one tile, never a second, never again after the user
removed it, never confuses the same-named DMG build with itself, and that
uninstall leaves no dead tile behind.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

import jarvis.setup.macos_dock as dock

_BUNDLE = Path("/Applications/Personal Jarvis.app")


def _tile(url: str, bundle_id: str | None) -> dict:
    data: dict = {"file-data": {"_CFURLString": url, "_CFURLStringType": 15}}
    if bundle_id is not None:
        data["bundle-identifier"] = bundle_id
    return {"tile-type": "file-tile", "tile-data": data}


_SAFARI = _tile("file:///Applications/Safari.app/", "com.apple.Safari")
_OURS = _tile("file:///Applications/Personal%20Jarvis.app/", "com.personal-jarvis.desktop")
_DMG = _tile("file:///Applications/Personal%20Jarvis.app/", "ai.personaljarvis.desktop")


class _Result:
    def __init__(self, stdout: bytes = b"") -> None:
        self.returncode = 0
        self.stdout = stdout


@pytest.fixture
def fake_dock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A Dock whose preferences live in a list; records every command."""
    state = {"tiles": [_SAFARI]}
    calls: list[list[str]] = []

    def _run(argv: list[str]):
        calls.append(argv)
        if argv[1] == "export":
            return _Result(plistlib.dumps({"persistent-apps": state["tiles"]}))
        return _Result()

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(dock, "_run", _run)
    monkeypatch.setattr(dock, "_marker_path", lambda: tmp_path / "macos-dock-pinned")
    return state, calls


def _writes(calls: list[list[str]]) -> list[list[str]]:
    return [argv for argv in calls if argv[1] == "write"]


def test_a_fresh_install_lands_in_the_dock(fake_dock) -> None:
    _state, calls = fake_dock

    assert dock.pin_to_dock_once(_BUNDLE) is True

    (write,) = _writes(calls)
    assert write[2:5] == ["com.apple.dock", "persistent-apps", "-array-add"]
    tile = plistlib.loads(b'<plist version="1.0">' + write[5].encode("utf-8") + b"</plist>")
    assert tile["tile-data"]["file-data"]["_CFURLString"] == (
        "file:///Applications/Personal%20Jarvis.app/"
    )
    assert dock.is_our_tile(tile)
    assert calls[-1] == [dock._KILLALL, "Dock"]


def test_a_tile_the_user_removed_stays_removed(fake_dock) -> None:
    """The marker, not the Dock, remembers that the pin already happened."""
    _state, calls = fake_dock
    dock.pin_to_dock_once(_BUNDLE)
    calls.clear()

    assert dock.pin_to_dock_once(_BUNDLE) is False
    assert calls == []


def test_an_app_already_in_the_dock_gets_no_second_tile(fake_dock) -> None:
    state, calls = fake_dock
    state["tiles"] = [_SAFARI, _OURS]

    assert dock.pin_to_dock_once(_BUNDLE) is False
    assert _writes(calls) == []


def test_the_dmg_build_is_not_mistaken_for_the_managed_app() -> None:
    assert dock.is_our_tile(_OURS)
    assert not dock.is_our_tile(_DMG)
    assert not dock.is_our_tile(_SAFARI)
    # Written by us, not yet resolved by the Dock: recognised by its name.
    assert dock.is_our_tile(_tile("file:///Users/u/Applications/Personal%20Jarvis.app/", None))


def test_uninstall_takes_only_our_tile_out(fake_dock) -> None:
    state, calls = fake_dock
    state["tiles"] = [_SAFARI, _OURS, _DMG]

    assert dock.remove_from_dock() is True

    (write,) = _writes(calls)
    assert write[2:5] == ["com.apple.dock", "persistent-apps", "-array"]
    kept = [
        plistlib.loads(b'<plist version="1.0">' + raw.encode("utf-8") + b"</plist>")
        for raw in write[5:]
    ]
    assert kept == [_SAFARI, _DMG]


def test_uninstall_without_a_tile_leaves_the_dock_alone(fake_dock) -> None:
    _state, calls = fake_dock

    assert dock.remove_from_dock() is False
    assert _writes(calls) == []
    assert [dock._KILLALL, "Dock"] not in calls


def test_unreadable_dock_preferences_change_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dock, "_run", lambda _argv: None)
    marker = tmp_path / "macos-dock-pinned"
    monkeypatch.setattr(dock, "_marker_path", lambda: marker)

    assert dock.pin_to_dock_once(_BUNDLE) is False
    # Nothing was decided, so the next installer run looks again.
    assert not marker.exists()
