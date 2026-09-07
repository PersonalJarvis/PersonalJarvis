"""Pins for the installed-apps sweep behind the C13 source inventory.

The per-OS sweeps are tested directly against temp trees, so every branch runs
on every CI platform; the cache and the never-raise promise are pinned on the
public entry point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.plugins.tool import app_inventory
from jarvis.plugins.tool.app_inventory import (
    _sweep_darwin,
    _sweep_linux,
    _sweep_windows,
    installed_app_names,
    reset_cache,
)


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    reset_cache()


def test_windows_sweep_reads_start_menu_shortcuts(tmp_path: Path) -> None:
    programs = tmp_path / "Programs"
    (programs / "Chat").mkdir(parents=True)
    (programs / "WhatsApp.lnk").write_bytes(b"")
    (programs / "Chat" / "Discord.lnk").write_bytes(b"")  # nested folders count
    (programs / "notes.txt").write_bytes(b"")  # non-shortcuts do not
    names = _sweep_windows(roots=[str(programs)])
    assert sorted(names) == ["Discord", "WhatsApp"]


def test_darwin_sweep_reads_top_level_app_bundles(tmp_path: Path) -> None:
    (tmp_path / "Spotify.app").mkdir()
    (tmp_path / "Notes.app").mkdir()
    (tmp_path / "loose-file").write_bytes(b"")
    names = _sweep_darwin(roots=(str(tmp_path),))
    assert sorted(names) == ["Notes", "Spotify"]


def test_linux_sweep_shortens_reverse_dns_ids(tmp_path: Path) -> None:
    (tmp_path / "org.mozilla.firefox.desktop").write_bytes(b"")
    (tmp_path / "gimp.desktop").write_bytes(b"")
    names = _sweep_linux(roots=(str(tmp_path),))
    assert sorted(names) == ["firefox", "gimp"]


def test_missing_roots_yield_an_empty_quiet_inventory(tmp_path: Path) -> None:
    """The headless-server promise: no roots, no error, no output."""
    gone = str(tmp_path / "does-not-exist")
    assert _sweep_windows(roots=[gone]) == []
    assert _sweep_darwin(roots=(gone,)) == []
    assert _sweep_linux(roots=(gone,)) == []


def test_uninstall_noise_is_filtered_and_result_is_sorted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_inventory,
        "_sweep_windows",
        lambda roots=None: ["zoom", "Uninstall Zoom", "Anki", "  "],
    )
    monkeypatch.setattr(app_inventory, "detect_platform", lambda: "win32")
    assert installed_app_names(force_refresh=True) == ("Anki", "zoom")


def test_the_sweep_runs_once_a_day_not_once_a_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def counting(roots=None):  # noqa: ANN001, ANN202
        calls.append(1)
        return ["OnlyApp"]

    monkeypatch.setattr(app_inventory, "_sweep_windows", counting)
    monkeypatch.setattr(app_inventory, "detect_platform", lambda: "win32")
    first = installed_app_names()
    second = installed_app_names()
    assert first == second == ("OnlyApp",)
    assert len(calls) == 1


def test_a_broken_sweep_degrades_to_empty_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(roots=None):  # noqa: ANN001, ANN202
        raise OSError("registry on fire")

    monkeypatch.setattr(app_inventory, "_sweep_windows", explode)
    monkeypatch.setattr(app_inventory, "detect_platform", lambda: "win32")
    assert installed_app_names(force_refresh=True) == ()
