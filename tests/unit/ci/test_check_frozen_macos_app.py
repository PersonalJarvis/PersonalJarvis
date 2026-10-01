"""``scripts/ci/check_frozen_macos_app.py`` — the gate that stops a broken .dmg.

The v2.5.0 image shipped without the pyobjc ``AVFoundation`` module (the
microphone permission then reads "unavailable" for good) and with
``LSBackgroundOnly`` in its ``Info.plist`` (LaunchServices runs it as a faceless
background process). The probe was run against that very image by hand and
reports exactly those two problems; these tests pin its decisions.
"""

from __future__ import annotations

import plistlib
import sys
import types
from pathlib import Path

import pytest

from jarvis.core.branding import MACOS_DMG_BUNDLE_ID
from scripts.ci import check_frozen_macos_app as probe

ALL_MODULES = set(probe.REQUIRED_FROZEN_MODULES) | {"jarvis", "jarvis.platform.permissions"}


def _info(**overrides: object) -> dict:
    info: dict = {
        "CFBundleIdentifier": MACOS_DMG_BUNDLE_ID,
        "CFBundleExecutable": "PersonalJarvis",
        "NSMicrophoneUsageDescription": "Personal Jarvis listens for your wake word.",
        "LSBackgroundOnly": False,
    }
    info.update(overrides)
    return info


def _bundle(tmp_path: Path, **overrides: object) -> Path:
    app = tmp_path / "Personal Jarvis.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PersonalJarvis").write_bytes(b"\xcf\xfa\xed\xfe")
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(_info(**overrides), stream)
    return app


def test_a_good_plist_has_no_problems(tmp_path: Path) -> None:
    app = _bundle(tmp_path)

    assert probe.check_info_plist(_info(), app / "Contents" / "MacOS") == []


def test_a_background_only_app_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path, LSBackgroundOnly=True)

    problems = probe.check_info_plist(_info(LSBackgroundOnly=True), app / "Contents" / "MacOS")

    assert len(problems) == 1
    assert "LSBackgroundOnly" in problems[0]


def test_a_bundle_id_the_permission_port_does_not_accept_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path)

    problems = probe.check_info_plist(
        _info(CFBundleIdentifier="com.example.other"), app / "Contents" / "MacOS"
    )

    assert len(problems) == 1
    assert MACOS_DMG_BUNDLE_ID in problems[0]


def test_a_missing_microphone_usage_string_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path)

    problems = probe.check_info_plist(
        _info(NSMicrophoneUsageDescription="  "), app / "Contents" / "MacOS"
    )

    assert len(problems) == 1
    assert "NSMicrophoneUsageDescription" in problems[0]


def test_a_declared_executable_that_is_not_there_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path)

    problems = probe.check_info_plist(
        _info(CFBundleExecutable="Missing"), app / "Contents" / "MacOS"
    )

    assert len(problems) == 1
    assert "Missing" in problems[0]


def test_every_required_module_present_is_fine() -> None:
    assert probe.check_frozen_modules(ALL_MODULES) == []


def test_the_v250_archive_without_avfoundation_is_refused() -> None:
    """The exact defect of the published image."""
    problems = probe.check_frozen_modules(ALL_MODULES - {"AVFoundation"})

    assert len(problems) == 1
    assert "AVFoundation" in problems[0]
    assert "desktop-macos" in problems[0]


def test_check_app_reports_both_defects_of_the_published_image(tmp_path: Path) -> None:
    app = _bundle(tmp_path, LSBackgroundOnly=True)

    problems = probe.check_app(app, read_modules=lambda _exe: ALL_MODULES - {"AVFoundation"})

    assert len(problems) == 2
    assert any("LSBackgroundOnly" in problem for problem in problems)
    assert any("AVFoundation" in problem for problem in problems)


def test_check_app_does_not_read_an_archive_that_cannot_exist(tmp_path: Path) -> None:
    app = _bundle(tmp_path, CFBundleExecutable="Missing")

    def explode(_exe: Path):
        raise AssertionError("the archive of a missing executable must not be read")

    assert probe.check_app(app, read_modules=explode)


def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    good = _bundle(tmp_path / "good")
    broken = _bundle(tmp_path / "broken", LSBackgroundOnly=True)
    monkeypatch.setattr(probe, "frozen_module_names", lambda _exe: ALL_MODULES)

    assert probe.main(["--app", str(good)]) == 0
    assert "OK" in capsys.readouterr().out
    assert probe.main(["--app", str(broken)]) == 1
    assert "LSBackgroundOnly" in capsys.readouterr().out
    assert probe.main(["--app", str(tmp_path / "nowhere.app")]) == 2
    assert "cannot check" in capsys.readouterr().out


def test_frozen_module_names_unions_every_embedded_pyz(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader walks the executable's table and opens each PYZ it finds."""

    class _Pyz:
        def __init__(self, names: list[str]) -> None:
            self.toc = dict.fromkeys(names, (0, 0, 0))

    class _CArchive:
        def __init__(self, _path: str) -> None:
            self.toc = {
                "PYZ-00.pyz": (0, 0, 0, False, "z"),
                "base_library.zip": (0, 0, 0, False, "b"),
                "PYZ-01.pyz": (0, 0, 0, False, "z"),
            }

        def open_embedded_archive(self, name: str) -> _Pyz:
            return _Pyz(["AVFoundation"] if name == "PYZ-00.pyz" else ["Quartz"])

    readers = types.ModuleType("PyInstaller.archive.readers")
    readers.CArchiveReader = _CArchive  # type: ignore[attr-defined]
    for name, module in (
        ("PyInstaller", types.ModuleType("PyInstaller")),
        ("PyInstaller.archive", types.ModuleType("PyInstaller.archive")),
        ("PyInstaller.archive.readers", readers),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    assert probe.frozen_module_names(Path("anything")) == {"AVFoundation", "Quartz"}
