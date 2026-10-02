"""``scripts/ci/check_frozen_macos_app.py`` — the gate that stops a broken .dmg.

The v2.5.0 image shipped without the pyobjc ``AVFoundation`` module (the
microphone permission then reads "unavailable" for good) and with
``LSBackgroundOnly`` in its ``Info.plist`` (LaunchServices runs it as a faceless
background process). The probe was run against that very image by hand and
reports exactly those two problems; these tests pin its decisions.

It also asserts the privacy declarations of the bundle: the single table of
usage-description strings (``jarvis/core/macos_privacy_strings.py``) and, on a
signed build, the entitlements ``codesign`` reports. No test here runs
``codesign`` (it exists on macOS only): a stand-in runner returns recorded
output shapes, and what a real ``codesign`` prints on a real Mac is unverified.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
import types
from collections.abc import Sequence
from pathlib import Path

import pytest

from jarvis.core import macos_privacy_strings
from jarvis.core.branding import MACOS_DMG_BUNDLE_ID
from scripts.ci import check_frozen_macos_app as probe

ALL_MODULES = set(probe.REQUIRED_FROZEN_MODULES) | {"jarvis", "jarvis.platform.permissions"}


def _info(**overrides: object) -> dict:
    info: dict = {
        "CFBundleIdentifier": MACOS_DMG_BUNDLE_ID,
        "CFBundleExecutable": "PersonalJarvis",
        "LSBackgroundOnly": False,
        **macos_privacy_strings.usage_descriptions(),
        **macos_privacy_strings.localization_plist_keys(),
    }
    info.update(overrides)
    return info


def _bundle(tmp_path: Path, **overrides: object) -> Path:
    app = tmp_path / "Personal Jarvis.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "PersonalJarvis").write_bytes(b"\xcf\xfa\xed\xfe")
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(_info(**overrides), stream)
    macos_privacy_strings.write_localizations(app / "Contents" / "Resources")
    return app


# What `codesign -dvv <app>` prints (to stderr) for the three kinds of build. The
# shapes follow Apple's tool as documented and as seen in public logs; none was
# captured from this project's app on a Mac (unverified).
ADHOC_REPORT = (
    "Executable=/Applications/Personal Jarvis.app/Contents/MacOS/PersonalJarvis\n"
    "Identifier=ai.personaljarvis.desktop\n"
    "Format=app bundle with Mach-O thin (arm64)\n"
    "CodeDirectory v=20400 size=1234 flags=0x2(adhoc) hashes=30+7 location=embedded\n"
    "Signature=adhoc\n"
    "TeamIdentifier=not set\n"
)
IDENTITY_REPORT = (
    "Executable=/Applications/Personal Jarvis.app/Contents/MacOS/PersonalJarvis\n"
    "Identifier=ai.personaljarvis.desktop\n"
    "CodeDirectory v=20500 size=1234 flags=0x10000(runtime) hashes=30+7 location=embedded\n"
    "Authority=Developer ID Application: Example GmbH (ABCDE12345)\n"
    "Authority=Developer ID Certification Authority\n"
    "Authority=Apple Root CA\n"
    "TeamIdentifier=ABCDE12345\n"
)
UNSIGNED_REPORT = "/Applications/Personal Jarvis.app: code object is not signed at all\n"


def _entitlements_xml(entitlements: dict) -> bytes:
    return plistlib.dumps(entitlements)


def _codesign(report: str, *, entitlements: bytes = b"", header: bytes = b"", returncode: int = 0):
    """A stand-in ``codesign``: ``-dvv`` answers ``report``; ``--entitlements`` the blob."""

    def run(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        if "--entitlements" in args:
            return subprocess.CompletedProcess(
                ["codesign", *args],
                returncode,
                stdout=header + entitlements,
                stderr=b"Executable=/Applications/Personal Jarvis.app/Contents/MacOS/X\n",
            )
        return subprocess.CompletedProcess(
            ["codesign", *args], returncode, stdout=b"", stderr=report.encode("utf-8")
        )

    return run


def _shipped_entitlements() -> dict:
    return plistlib.loads(probe.ENTITLEMENTS_FILE.read_bytes())


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
    assert "ends the process" in problems[0]


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

    problems = probe.check_app(
        app,
        read_modules=lambda _exe: ALL_MODULES - {"AVFoundation"},
        run_codesign=_codesign(ADHOC_REPORT),
    )

    assert len(problems) == 2
    assert any("LSBackgroundOnly" in problem for problem in problems)
    assert any("AVFoundation" in problem for problem in problems)


def test_check_app_does_not_read_an_archive_that_cannot_exist(tmp_path: Path) -> None:
    app = _bundle(tmp_path, CFBundleExecutable="Missing")

    def explode(_exe: Path):
        raise AssertionError("the archive of a missing executable must not be read")

    assert probe.check_app(app, read_modules=explode, run_codesign=_codesign(ADHOC_REPORT))


def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    good = _bundle(tmp_path / "good")
    broken = _bundle(tmp_path / "broken", LSBackgroundOnly=True)
    monkeypatch.setattr(probe, "frozen_module_names", lambda _exe: ALL_MODULES)
    monkeypatch.setattr(probe, "_run_codesign", _codesign(ADHOC_REPORT))

    assert probe.main(["--app", str(good)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "NOTE" in out and "ad-hoc" in out  # the skipped half is said out loud
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


# --- the usage-description table ---------------------------------------------


def test_every_key_of_the_table_is_asserted_on_the_built_bundle() -> None:
    assert probe.check_usage_strings(_info()) == []
    for key in macos_privacy_strings.REQUIRED_USAGE_KEYS:
        info = _info()
        del info[key]

        problems = probe.check_usage_strings(info)

        assert len(problems) == 1, key
        assert key in problems[0] and "missing" in problems[0]


def test_a_non_microphone_string_that_is_missing_is_not_called_process_ending() -> None:
    """Only the microphone case is documented as fatal; the rest is a third-party report."""
    problems = probe.check_usage_strings(_info(NSScreenCaptureUsageDescription=""))

    assert len(problems) == 1
    assert "ends the process" not in problems[0]
    assert "without ever showing a permission prompt" in problems[0]


def test_a_string_that_differs_from_the_table_means_the_spec_kept_its_own_copy() -> None:
    problems = probe.check_usage_strings(_info(NSDesktopFolderUsageDescription="Old wording."))

    assert len(problems) == 1
    assert "NSDesktopFolderUsageDescription" in problems[0]
    assert "macos_privacy_strings.py" in problems[0]


@pytest.mark.parametrize("key", macos_privacy_strings.REMOVED_USAGE_KEYS)
def test_a_removed_key_that_comes_back_is_refused(key: str) -> None:
    problems = probe.check_usage_strings(_info(**{key: "A string for an API Jarvis never calls."}))

    assert len(problems) == 1
    assert key in problems[0] and "least privilege" in problems[0]


def test_the_plist_check_includes_the_usage_table(tmp_path: Path) -> None:
    app = _bundle(tmp_path)
    info = _info()
    del info["NSLocalNetworkUsageDescription"]

    problems = probe.check_info_plist(info, app / "Contents" / "MacOS")

    assert len(problems) == 1
    assert "NSLocalNetworkUsageDescription" in problems[0]


# --- German and Spanish localisation --------------------------------------------


def test_a_bundle_that_declares_no_localisation_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path)
    info = _info()
    del info["CFBundleLocalizations"]

    problems = probe.check_info_plist(info, app / "Contents" / "MacOS")

    assert len(problems) == 1
    assert "CFBundleLocalizations" in problems[0] and "localization_plist_keys" in problems[0]


def test_a_wrong_development_region_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path)

    problems = probe.check_info_plist(
        _info(CFBundleDevelopmentRegion="de"), app / "Contents" / "MacOS"
    )

    assert len(problems) == 1
    assert "CFBundleDevelopmentRegion" in problems[0]


def test_a_localisation_list_without_spanish_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path)

    problems = probe.check_info_plist(
        _info(CFBundleLocalizations=["en", "de"]), app / "Contents" / "MacOS"
    )

    assert len(problems) == 1 and "CFBundleLocalizations" in problems[0]


def test_the_shipped_lproj_files_pass(tmp_path: Path) -> None:
    app = _bundle(tmp_path)

    assert probe.check_localization_files(app / "Contents" / "Resources") == []


@pytest.mark.parametrize("language", macos_privacy_strings.LOCALIZED_LANGUAGES)
def test_a_missing_strings_file_names_the_build_step(tmp_path: Path, language: str) -> None:
    app = _bundle(tmp_path)
    (app / "Contents" / "Resources" / f"{language}.lproj" / "InfoPlist.strings").unlink()

    problems = probe.check_localization_files(app / "Contents" / "Resources")

    assert len(problems) == 1
    assert f"{language}.lproj/InfoPlist.strings is missing" in problems[0]
    assert "add_localizations.py" in problems[0]


def test_a_strings_file_that_is_not_the_table_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path)
    target = app / "Contents" / "Resources" / "de.lproj" / "InfoPlist.strings"
    target.write_text('"NSMicrophoneUsageDescription" = "Alter Text.";\n', encoding="utf-8")

    problems = probe.check_localization_files(app / "Contents" / "Resources")

    assert len(problems) == 1
    assert "de.lproj" in problems[0] and "single usage-string table" in problems[0]


def test_a_strings_file_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path)
    target = app / "Contents" / "Resources" / "es.lproj" / "InfoPlist.strings"
    target.write_bytes("\ufeff".encode("utf-16-le") + b"\xff\xfe\x00")

    problems = probe.check_localization_files(app / "Contents" / "Resources")

    assert len(problems) == 1 and "es.lproj" in problems[0] and "UTF-8" in problems[0]


def test_check_app_reports_a_bundle_built_without_the_localisation_step(tmp_path: Path) -> None:
    app = _bundle(tmp_path)
    for language in macos_privacy_strings.LOCALIZED_LANGUAGES:
        (app / "Contents" / "Resources" / f"{language}.lproj" / "InfoPlist.strings").unlink()

    problems = probe.check_app(
        app, read_modules=lambda _exe: ALL_MODULES, run_codesign=_codesign(ADHOC_REPORT)
    )

    assert len(problems) == 2
    assert all("InfoPlist.strings is missing" in problem for problem in problems)


# --- entitlements of a signed build ---------------------------------------------


def test_an_adhoc_build_skips_the_entitlement_check_and_says_so(tmp_path: Path) -> None:
    notes: list[str] = []

    problems = probe.check_signature(tmp_path / "A.app", run=_codesign(ADHOC_REPORT), notes=notes)

    assert problems == []
    assert len(notes) == 1
    assert "NOT checked" in notes[0] and "ad-hoc" in notes[0]
    assert "not a pass" in notes[0]


def test_an_unsigned_build_skips_the_entitlement_check_and_says_so(tmp_path: Path) -> None:
    notes: list[str] = []

    problems = probe.check_signature(
        tmp_path / "A.app", run=_codesign(UNSIGNED_REPORT, returncode=1), notes=notes
    )

    assert problems == []
    assert "unsigned" in notes[0] and "NOT checked" in notes[0]


def test_a_host_without_codesign_skips_with_a_clear_message(tmp_path: Path) -> None:
    def no_codesign(_args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("codesign")

    notes: list[str] = []

    assert probe.check_signature(tmp_path / "A.app", run=no_codesign, notes=notes) == []
    assert "codesign is not available on this host" in notes[0]


def test_a_signed_build_with_the_shipped_entitlements_passes(tmp_path: Path) -> None:
    notes: list[str] = []
    runner = _codesign(IDENTITY_REPORT, entitlements=_entitlements_xml(_shipped_entitlements()))

    assert probe.check_signature(tmp_path / "A.app", run=runner, notes=notes) == []
    assert notes == []


def test_the_entitlement_plist_is_cut_out_of_surrounding_codesign_noise(tmp_path: Path) -> None:
    """A blob header before the XML, and trailing text after it, must not break parsing."""
    xml = _entitlements_xml(_shipped_entitlements())
    runner = _codesign(
        IDENTITY_REPORT,
        entitlements=xml + b"\n[trailing tool chatter]\n",
        header=b"\xfa\xde\x71\x71\x00\x00\x01\x00",
    )

    assert probe.check_signature(tmp_path / "A.app", run=runner, notes=[]) == []


def test_a_signed_build_that_embedded_no_entitlements_is_refused(tmp_path: Path) -> None:
    runner = _codesign(IDENTITY_REPORT, entitlements=b"")

    problems = probe.check_signature(tmp_path / "A.app", run=runner, notes=[])

    expected = _shipped_entitlements()
    assert len(problems) == len(expected)
    for key in expected:
        assert any(key in problem for problem in problems)


def test_a_signed_build_missing_one_entitlement_names_it(tmp_path: Path) -> None:
    entitlements = _shipped_entitlements()
    del entitlements["com.apple.security.device.audio-input"]
    runner = _codesign(IDENTITY_REPORT, entitlements=_entitlements_xml(entitlements))

    problems = probe.check_signature(tmp_path / "A.app", run=runner, notes=[])

    assert len(problems) == 1
    assert "com.apple.security.device.audio-input" in problems[0]


def test_an_entitlement_set_to_false_counts_as_missing(tmp_path: Path) -> None:
    entitlements = _shipped_entitlements()
    entitlements["com.apple.security.automation.apple-events"] = False
    runner = _codesign(IDENTITY_REPORT, entitlements=_entitlements_xml(entitlements))

    problems = probe.check_signature(tmp_path / "A.app", run=runner, notes=[])

    assert len(problems) == 1
    assert "apple-events" in problems[0]


def test_the_camera_entitlement_coming_back_on_a_signed_build_is_refused(tmp_path: Path) -> None:
    entitlements = {**_shipped_entitlements(), "com.apple.security.device.camera": True}
    runner = _codesign(IDENTITY_REPORT, entitlements=_entitlements_xml(entitlements))

    problems = probe.check_signature(tmp_path / "A.app", run=runner, notes=[])

    assert len(problems) == 1
    assert "device.camera" in problems[0] and "least privilege" in problems[0]


def test_a_signature_codesign_output_i_cannot_classify_is_cannot_check(tmp_path: Path) -> None:
    runner = _codesign("something unexpected\n", returncode=1)

    with pytest.raises(RuntimeError, match="no signature kind"):
        probe.check_signature(tmp_path / "A.app", run=runner, notes=[])


def test_the_shipped_entitlements_file_has_nothing_the_gate_forbids() -> None:
    shipped = _shipped_entitlements()

    assert not set(shipped) & set(probe.REMOVED_ENTITLEMENTS)
    assert probe.check_entitlements(shipped, shipped) == []


def test_main_reports_a_signed_build_with_a_missing_entitlement_as_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    entitlements = _shipped_entitlements()
    del entitlements["com.apple.security.cs.disable-library-validation"]
    monkeypatch.setattr(probe, "frozen_module_names", lambda _exe: ALL_MODULES)
    monkeypatch.setattr(
        probe,
        "_run_codesign",
        _codesign(IDENTITY_REPORT, entitlements=_entitlements_xml(entitlements)),
    )

    assert probe.main(["--app", str(_bundle(tmp_path))]) == 1
    out = capsys.readouterr().out
    assert "disable-library-validation" in out
    assert "NOTE" not in out


def test_main_on_a_host_without_codesign_passes_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def no_codesign(_args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("codesign")

    monkeypatch.setattr(probe, "frozen_module_names", lambda _exe: ALL_MODULES)
    monkeypatch.setattr(probe, "_run_codesign", no_codesign)

    assert probe.main(["--app", str(_bundle(tmp_path))]) == 0
    assert "NOTE - entitlements NOT checked" in capsys.readouterr().out


def test_help_lists_the_one_required_argument(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        probe.main(["--help"])

    assert raised.value.code == 0
    assert "--app" in capsys.readouterr().out
