"""Being installed is not the same as being findable.

Each desktop has an index between the launcher file and the user's search box —
the Windows app index, the XDG desktop database, LaunchServices — and writing
the file without announcing it leaves the app on disk but absent from search
until the next login. That is the shape of the 2026-08-16 report, and these
tests pin the announcement on every platform.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.setup import macos_app_bundle
from jarvis.ui import icon_utils


def _entry(applications_dir: Path) -> Path:
    return applications_dir / "personal-jarvis.desktop"


def test_linux_entry_carries_search_keywords(tmp_path: Path) -> None:
    """The name is two words; a user types the half they remember."""
    apps = tmp_path / "applications"

    assert icon_utils.ensure_linux_desktop_entry(applications_dir=apps) is True

    content = _entry(apps).read_text(encoding="utf-8")
    assert "Keywords=" in content
    keywords = next(
        line.split("=", 1)[1] for line in content.splitlines() if line.startswith("Keywords=")
    )
    assert "jarvis" in keywords
    assert keywords.endswith(";")


def test_linux_entry_is_announced_to_the_desktop_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this the entry shows up only after the next login."""
    apps = tmp_path / "applications"
    announced: list[Path] = []
    monkeypatch.setattr(
        icon_utils,
        "_refresh_linux_desktop_database",
        lambda directory: announced.append(directory) or True,
    )

    icon_utils.ensure_linux_desktop_entry(applications_dir=apps)

    assert announced == [apps]


def test_an_unchanged_linux_entry_is_still_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install whose write succeeded but whose announcement did not must heal.

    The content check short-circuits the rewrite; if it also skipped the
    announcement, a half-finished earlier run would stay unsearchable forever.
    """
    apps = tmp_path / "applications"
    icon_utils.ensure_linux_desktop_entry(applications_dir=apps)

    announced: list[Path] = []
    monkeypatch.setattr(
        icon_utils,
        "_refresh_linux_desktop_database",
        lambda directory: announced.append(directory) or True,
    )

    assert icon_utils.ensure_linux_desktop_entry(applications_dir=apps) is True
    assert announced == [apps]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_linux_entry_is_executable(tmp_path: Path) -> None:
    """Some shells refuse to offer an entry that is not user-executable."""
    apps = tmp_path / "applications"

    icon_utils.ensure_linux_desktop_entry(applications_dir=apps)

    assert _entry(apps).stat().st_mode & 0o100


def test_missing_update_tool_degrades_to_a_later_rescan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """desktop-file-utils is not installed everywhere; that is not an error."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: pytest.fail("no tool means no subprocess"),
    )

    assert icon_utils._refresh_linux_desktop_database(tmp_path) is False


def test_linux_refresh_targets_the_applications_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/update-desktop-database")
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = ""

    def _run(argv, **_kwargs):
        calls.append(argv)
        return _Result()

    monkeypatch.setattr(subprocess, "run", _run)

    assert icon_utils._refresh_linux_desktop_database(tmp_path) is True
    assert calls == [["/usr/bin/update-desktop-database", str(tmp_path)]]


def test_launch_services_registration_is_a_noop_off_macos() -> None:
    """Every other OS reaches this through its own index, never lsregister."""
    if sys.platform == "darwin":
        pytest.skip("this asserts the non-macOS branch")

    assert macos_app_bundle.register_with_launch_services(Path("/x/Personal Jarvis.app")) is False


def test_launch_services_registration_forces_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``-f`` re-registers a bundle LaunchServices may already have stale."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = ""

    def _run(argv, **_kwargs):
        calls.append(argv)
        return _Result()

    monkeypatch.setattr(subprocess, "run", _run)
    bundle = tmp_path / "Personal Jarvis.app"

    assert macos_app_bundle.register_with_launch_services(bundle) is True
    assert calls[0][1:] == ["-f", str(bundle)]
    assert calls[0][0].endswith("lsregister")
