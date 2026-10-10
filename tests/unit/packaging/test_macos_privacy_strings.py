"""The macOS usage-description strings have exactly one source, and both bundles read it.

``jarvis/core/macos_privacy_strings.py`` holds the single table of
``NS...UsageDescription`` strings. The downloadable ``.dmg`` app (``jarvis.spec``,
evaluated by PyInstaller before the package is importable) and the managed
source-install app (``macos_app_bundle._bundle_plist``) both load THAT FILE BY
PATH. These tests pin the table's shape and prove that the spec's plist dict, the
managed bundle's plist and the table agree - including that neither carries a
private copy - and that ``entitlements.plist`` stays comment-free and
camera-free.

Everything here runs off macOS: the real ``jarvis.spec`` is executed against
stand-in PyInstaller names (it needs no Mac to describe its own ``Info.plist``).
What a real Mac does with these strings is not checked and stays unverified.
"""

from __future__ import annotations

import ast
import importlib.util
import plistlib
import re
import shutil
import sys
import types
from pathlib import Path

import pytest

import jarvis.setup.macos_app_bundle as mab
from jarvis.core import macos_privacy_strings as table_module
from jarvis.core.branding import PRODUCT_NAME

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "jarvis.spec"
TABLE_PATH = REPO_ROOT / "jarvis" / "core" / "macos_privacy_strings.py"
ENTITLEMENTS_PATH = REPO_ROOT / "packaging" / "macos" / "entitlements.plist"

EXPECTED_KEYS = (
    "NSMicrophoneUsageDescription",
    "NSScreenCaptureUsageDescription",
    "NSAppleEventsUsageDescription",
    "NSDesktopFolderUsageDescription",
    "NSDocumentsFolderUsageDescription",
    "NSDownloadsFolderUsageDescription",
    "NSRemovableVolumesUsageDescription",
    "NSNetworkVolumesUsageDescription",
    "NSLocalNetworkUsageDescription",
)
REMOVED_KEYS = (
    "NSCameraUsageDescription",
    "NSSpeechRecognitionUsageDescription",
    "NSSystemAdministrationUsageDescription",
)
SENTINEL = "SENTINEL usage string that only the edited table carries."


def _load_by_path(path: Path) -> types.ModuleType:
    """Exactly what ``jarvis.spec`` and ``macos_app_bundle`` do to read the table."""
    loader_spec = importlib.util.spec_from_file_location("_table_under_test", path)
    assert loader_spec is not None and loader_spec.loader is not None
    module = importlib.util.module_from_spec(loader_spec)
    loader_spec.loader.exec_module(module)
    return module


# --- the table itself ----------------------------------------------------------


def test_the_table_holds_exactly_the_keys_jarvis_has_a_caller_for() -> None:
    assert table_module.REQUIRED_USAGE_KEYS == EXPECTED_KEYS
    assert tuple(table_module.usage_descriptions()) == EXPECTED_KEYS
    assert table_module.REMOVED_USAGE_KEYS == REMOVED_KEYS
    assert not set(table_module.usage_descriptions()) & set(REMOVED_KEYS)


def test_every_string_is_one_plain_english_sentence_that_names_the_product() -> None:
    for key, text in table_module.usage_descriptions().items():
        assert text == text.strip(), key
        assert text.isascii(), f"{key} must stay plain ASCII"
        assert "\n" not in text, key
        assert text.startswith(PRODUCT_NAME), key
        assert text.endswith("."), f"{key} needs a closing period"
        assert text.count(". ") == 0 and text.count(".") == 1, f"{key} must be ONE sentence"
        assert 20 <= len(text.encode("utf-8")) < 4000, key


def test_the_product_name_equals_the_branding_constant() -> None:
    """The table may not import the package, so a test keeps its literal honest."""
    assert table_module.PRODUCT_NAME == PRODUCT_NAME


def test_each_string_names_the_feature_that_triggers_the_request() -> None:
    texts = table_module.usage_descriptions()
    assert "dictate" in texts["NSMicrophoneUsageDescription"]
    assert "wake word" in texts["NSMicrophoneUsageDescription"]
    assert "screen" in texts["NSScreenCaptureUsageDescription"]
    # Target-neutral: macOS names the controlled app in the dialog itself, and the
    # same string serves Music, Spotify and Terminal, so it names none of them.
    apple_events = texts["NSAppleEventsUsageDescription"]
    assert "other apps" in apple_events
    for target in ("Music", "Spotify", "Terminal"):
        assert target not in apple_events
    assert "Desktop" in texts["NSDesktopFolderUsageDescription"]
    assert "Documents" in texts["NSDocumentsFolderUsageDescription"]
    assert "Downloads" in texts["NSDownloadsFolderUsageDescription"]
    assert "external drive" in texts["NSRemovableVolumesUsageDescription"]
    assert "network drive" in texts["NSNetworkVolumesUsageDescription"]
    # The old text claimed the app "serves its own interface to your browser";
    # the local-network prompt is about the hosts the person configures.
    assert "local network" in texts["NSLocalNetworkUsageDescription"]
    assert "serves" not in texts["NSLocalNetworkUsageDescription"]


def test_usage_descriptions_returns_a_fresh_dict_each_call() -> None:
    first = table_module.usage_descriptions()
    first["NSMicrophoneUsageDescription"] = "changed"
    first["NSCameraUsageDescription"] = "added"

    assert table_module.usage_descriptions()["NSMicrophoneUsageDescription"] != "changed"
    assert "NSCameraUsageDescription" not in table_module.usage_descriptions()


def test_the_module_is_standard_library_only_and_does_nothing_at_import() -> None:
    """Loadable by path before the package exists (AP-26: no import-time work)."""
    tree = ast.parse(TABLE_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported <= {"__future__", "pathlib"}, imported
    top_level_calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    assert top_level_calls == []


def test_loading_by_path_and_importing_give_the_same_table() -> None:
    by_path = _load_by_path(TABLE_PATH)

    assert by_path.usage_descriptions() == table_module.usage_descriptions()
    assert by_path.REQUIRED_USAGE_KEYS == table_module.REQUIRED_USAGE_KEYS
    assert Path(table_module.__file__).resolve() == TABLE_PATH.resolve()


# --- the .dmg app: jarvis.spec -------------------------------------------------


def _exec_spec(monkeypatch: pytest.MonkeyPatch, root: Path) -> dict:
    """Run the real ``jarvis.spec`` with stand-in PyInstaller names; return BUNDLE's kwargs."""
    calls: dict[str, dict] = {}

    def recorder(name: str):
        def _call(*args: object, **kwargs: object) -> types.SimpleNamespace:
            calls[name] = kwargs
            return types.SimpleNamespace(
                pure=[], zipped_data=[], scripts=[], binaries=[], zipfiles=[], datas=[]
            )

        return _call

    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = lambda *a, **k: []  # type: ignore[attr-defined]
    hooks.collect_submodules = lambda *a, **k: []  # type: ignore[attr-defined]
    hooks.copy_metadata = lambda *a, **k: []  # type: ignore[attr-defined]
    for name, module in (
        ("PyInstaller", types.ModuleType("PyInstaller")),
        ("PyInstaller.utils", types.ModuleType("PyInstaller.utils")),
        ("PyInstaller.utils.hooks", hooks),
        # The spec probes these optional packages with __import__; stand-ins keep
        # a real (possibly platform-specific) import from running under a fake OS.
        ("webview", types.ModuleType("webview")),
        ("pystray", types.ModuleType("pystray")),
        ("PIL", types.ModuleType("PIL")),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "platform", "darwin")

    namespace: dict[str, object] = {
        "SPECPATH": str(root),
        "__file__": str(root / "jarvis.spec"),
    }
    for builtin in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE"):
        namespace[builtin] = recorder(builtin)
    source = (root / "jarvis.spec").read_text(encoding="utf-8")
    exec(compile(source, str(root / "jarvis.spec"), "exec"), namespace)  # noqa: S102
    assert "BUNDLE" in calls, "the spec must build a BUNDLE on darwin"
    return calls["BUNDLE"]


@pytest.fixture
def spec_root(tmp_path: Path) -> Path:
    """A minimal checkout: the real spec, the real table, a version file."""
    root = tmp_path / "checkout"
    (root / "jarvis" / "core").mkdir(parents=True)
    (root / "jarvis" / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    shutil.copy2(TABLE_PATH, root / "jarvis" / "core" / "macos_privacy_strings.py")
    shutil.copy2(SPEC_PATH, root / "jarvis.spec")
    return root


def test_the_spec_plist_carries_every_string_of_the_table_and_no_removed_key(
    monkeypatch: pytest.MonkeyPatch, spec_root: Path
) -> None:
    bundle = _exec_spec(monkeypatch, spec_root)
    info = bundle["info_plist"]

    expected = table_module.usage_descriptions()
    assert {key: info[key] for key in EXPECTED_KEYS} == expected
    for key in REMOVED_KEYS:
        assert key not in info, f"{key} has no caller and must not ship"
    assert {
        key for key in info if key.startswith("NS") and key.endswith("UsageDescription")
    } == set(EXPECTED_KEYS)
    # The rest of the plist is the app's identity and is unchanged by the move.
    assert info["CFBundleIdentifier"] == "ai.personaljarvis.desktop"
    assert info["LSBackgroundOnly"] is False
    assert info["CFBundleVersion"] == "9.9.9"
    # PyInstaller writes the dict with plistlib, so it must serialise.
    assert plistlib.loads(plistlib.dumps(info)) == info


def test_the_spec_reads_the_table_by_path_and_keeps_no_copy(
    monkeypatch: pytest.MonkeyPatch, spec_root: Path
) -> None:
    """Edit the table beside the spec: the spec's plist follows, so it is not a copy."""
    table = spec_root / "jarvis" / "core" / "macos_privacy_strings.py"
    text = table.read_text(encoding="utf-8")
    table.write_text(
        text + f'\n_USAGE_DESCRIPTIONS["NSMicrophoneUsageDescription"] = "{SENTINEL}"\n',
        encoding="utf-8",
    )

    info = _exec_spec(monkeypatch, spec_root)["info_plist"]

    assert info["NSMicrophoneUsageDescription"] == SENTINEL


def test_neither_the_spec_nor_the_managed_bundle_spells_a_usage_key_itself() -> None:
    """A quoted ``"NS...UsageDescription"`` literal outside the table is a private copy."""
    literal = re.compile(r"""["']NS\w+UsageDescription["']""")
    for path in (SPEC_PATH, Path(mab.__file__)):
        found = literal.findall(path.read_text(encoding="utf-8"))
        assert found == [], f"{path.name} carries its own usage keys: {found}"


# --- the managed app: macos_app_bundle ----------------------------------------


def test_the_managed_bundle_plist_carries_the_same_table() -> None:
    info = mab._bundle_plist()

    assert {key: info[key] for key in EXPECTED_KEYS} == table_module.usage_descriptions()
    for key in REMOVED_KEYS:
        assert key not in info
    assert info["CFBundleIdentifier"] == "com.personal-jarvis.desktop"


def test_the_managed_bundle_reads_the_table_by_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    edited = tmp_path / "macos_privacy_strings.py"
    edited.write_text(
        TABLE_PATH.read_text(encoding="utf-8")
        + f'\n_USAGE_DESCRIPTIONS["NSNetworkVolumesUsageDescription"] = "{SENTINEL}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(mab, "_PRIVACY_STRINGS_PATH", edited)

    assert mab._bundle_plist()["NSNetworkVolumesUsageDescription"] == SENTINEL


def test_a_missing_table_file_is_loud_not_an_empty_plist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mab, "_PRIVACY_STRINGS_PATH", tmp_path / "nowhere.py")

    with pytest.raises(FileNotFoundError):
        mab._bundle_plist()


def test_both_bundles_agree_on_every_usage_string(
    monkeypatch: pytest.MonkeyPatch, spec_root: Path
) -> None:
    """The headline parity: the .dmg app and the managed app show the same dialogs."""
    dmg = _exec_spec(monkeypatch, spec_root)["info_plist"]
    managed = mab._bundle_plist()

    def usage(info: dict) -> dict:
        return {k: v for k, v in info.items() if k.endswith("UsageDescription")}

    assert usage(dmg) == usage(managed) == table_module.usage_descriptions()


# --- entitlements.plist --------------------------------------------------------


def test_entitlements_plist_has_no_xml_comments() -> None:
    """Apple documents no comment syntax for entitlements; the reasoning is in the README."""
    assert "<!--" not in ENTITLEMENTS_PATH.read_text(encoding="utf-8")


def test_entitlements_plist_parses_and_requests_exactly_the_four_justified_rights() -> None:
    entitlements = plistlib.loads(ENTITLEMENTS_PATH.read_bytes())

    assert entitlements == {
        "com.apple.security.device.audio-input": True,
        "com.apple.security.automation.apple-events": True,
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-library-validation": True,
    }


def test_the_camera_entitlement_is_gone_and_the_readme_says_why() -> None:
    assert "device.camera" not in ENTITLEMENTS_PATH.read_text(encoding="utf-8")
    readme = (REPO_ROOT / "packaging" / "macos" / "README.md").read_text(encoding="utf-8")
    assert "com.apple.security.device.camera" in readme  # the "Removed:" paragraph
    for entitlement in plistlib.loads(ENTITLEMENTS_PATH.read_bytes()):
        assert f"`{entitlement}`" in readme, f"README must keep the reasoning for {entitlement}"


# --- the English folder sentences stay short -----------------------------------


def test_the_folder_strings_do_not_promise_saving_or_creating() -> None:
    """The system dialog already names the folder; the sentence only says "works with"."""
    texts = table_module.usage_descriptions()
    for key in (
        "NSDesktopFolderUsageDescription",
        "NSDocumentsFolderUsageDescription",
        "NSDownloadsFolderUsageDescription",
        "NSRemovableVolumesUsageDescription",
        "NSNetworkVolumesUsageDescription",
    ):
        lowered = texts[key].lower()
        assert "saves" not in lowered and "creates" not in lowered, key
        assert "works with files" in lowered, key


# --- German, Spanish, Portuguese: both bundles ship the same InfoPlist.strings --


def _parse_strings(text: str) -> dict[str, str]:
    """Read back an ``InfoPlist.strings`` file (``"key" = "value";`` lines, escapes undone)."""
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("/*"):
            continue
        match = re.fullmatch(r'"(?P<key>[A-Za-z]+)" = "(?P<value>(?:[^"\\]|\\.)*)";', line)
        assert match is not None, f"not a .strings line: {line!r}"
        parsed[match["key"]] = (
            match["value"].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        )
    return parsed


def test_the_localised_tables_cover_exactly_the_required_keys() -> None:
    assert table_module.LOCALIZATIONS == ("en", "de", "es", "pt-PT")
    assert table_module.DEVELOPMENT_REGION == "en"
    assert table_module.LOCALIZED_LANGUAGES == ("de", "es", "pt-PT")
    english = table_module.usage_descriptions()
    for language in table_module.LOCALIZED_LANGUAGES:
        localised = table_module.localized_usage_descriptions(language)
        assert tuple(localised) == table_module.REQUIRED_USAGE_KEYS, language
        for key, text in localised.items():
            assert text == text.strip() and "\n" not in text, (language, key)
            assert text.startswith(PRODUCT_NAME), (language, key)
            assert text.endswith("."), (language, key)
            assert text.count(".") == 1, f"{language} {key} must be ONE sentence"
            assert text != english[key], f"{language} {key} is still English"


def test_the_apple_events_string_is_target_neutral_in_every_language() -> None:
    for language in table_module.LOCALIZED_LANGUAGES:
        text = table_module.localized_usage_descriptions(language)["NSAppleEventsUsageDescription"]
        for target in ("Music", "Spotify", "Terminal"):
            assert target not in text, (language, target)


def test_localised_dicts_are_fresh_copies() -> None:
    first = table_module.localized_usage_descriptions("de")
    first["NSMicrophoneUsageDescription"] = "changed"
    assert table_module.localized_usage_descriptions("de")["NSMicrophoneUsageDescription"] != (
        "changed"
    )
    keys = table_module.localization_plist_keys()
    keys["CFBundleLocalizations"].append("fr")
    assert table_module.localization_plist_keys()["CFBundleLocalizations"] == [
        "en",
        "de",
        "es",
        "pt-PT",
    ]


@pytest.mark.parametrize("language", ["de", "es", "pt-PT"])
def test_the_strings_file_round_trips_the_table(language: str) -> None:
    text = table_module.info_plist_strings_text(language)

    assert text.endswith("\n") and "\r" not in text
    assert _parse_strings(text) == table_module.localized_usage_descriptions(language)


def test_the_strings_file_escapes_quotes_backslashes_and_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tricky = 'Say "hi" \\ there\nnext'
    monkeypatch.setitem(
        table_module._LOCALIZED_USAGE_DESCRIPTIONS["de"], "NSMicrophoneUsageDescription", tricky
    )

    parsed = _parse_strings(table_module.info_plist_strings_text("de"))

    assert parsed["NSMicrophoneUsageDescription"] == tricky


def test_write_localizations_creates_one_lproj_per_language(tmp_path: Path) -> None:
    resources = tmp_path / "Contents" / "Resources"

    written = table_module.write_localizations(resources)

    assert sorted(path.relative_to(resources).as_posix() for path in written) == [
        "de.lproj/InfoPlist.strings",
        "es.lproj/InfoPlist.strings",
        "pt-PT.lproj/InfoPlist.strings",
    ]
    for language in table_module.LOCALIZED_LANGUAGES:
        raw = (resources / f"{language}.lproj" / "InfoPlist.strings").read_bytes()
        assert raw.decode("utf-8") == table_module.info_plist_strings_text(language)
    # English is the base Info.plist text: there is deliberately no en.lproj.
    assert not (resources / "en.lproj").exists()


def test_write_localizations_is_repeatable(tmp_path: Path) -> None:
    table_module.write_localizations(tmp_path)
    table_module.write_localizations(tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "de.lproj",
        "es.lproj",
        "pt-PT.lproj",
    ]


def test_the_spec_declares_the_localisations(
    monkeypatch: pytest.MonkeyPatch, spec_root: Path
) -> None:
    info = _exec_spec(monkeypatch, spec_root)["info_plist"]

    assert info["CFBundleLocalizations"] == ["en", "de", "es", "pt-PT"]
    assert info["CFBundleDevelopmentRegion"] == "en"
    assert plistlib.loads(plistlib.dumps(info)) == info


def test_the_managed_bundle_declares_the_same_localisations() -> None:
    info = mab._bundle_plist()

    assert info["CFBundleLocalizations"] == ["en", "de", "es", "pt-PT"]
    assert info["CFBundleDevelopmentRegion"] == "en"
    # No format bump: a bump would rebuild every installed bundle and re-ask every
    # permission after an ad-hoc rebuild (see the comment above _BUNDLE_FORMAT_VERSION).
    assert info["JarvisBundleFormatVersion"] == 3


def test_the_managed_bundle_layout_carries_the_lproj_files(tmp_path: Path) -> None:
    bundle = mab._write_cross_platform_fixture_bundle(tmp_path / mab.APP_DIR_NAME)

    for language in table_module.LOCALIZED_LANGUAGES:
        strings = bundle / "Contents" / "Resources" / f"{language}.lproj" / "InfoPlist.strings"
        assert _parse_strings(strings.read_text(encoding="utf-8")) == (
            table_module.localized_usage_descriptions(language)
        )


def test_both_bundles_write_the_same_strings_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The .dmg build step and the managed layout read ONE table, so the files agree."""
    helper_path = REPO_ROOT / "packaging" / "macos" / "add_localizations.py"
    loader_spec = importlib.util.spec_from_file_location("_add_localizations", helper_path)
    assert loader_spec is not None and loader_spec.loader is not None
    helper = importlib.util.module_from_spec(loader_spec)
    loader_spec.loader.exec_module(helper)

    dmg_app = tmp_path / "dmg" / "Personal Jarvis.app"
    (dmg_app / "Contents").mkdir(parents=True)
    (dmg_app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({}))
    helper.add_localizations(dmg_app)
    managed = mab._write_cross_platform_fixture_bundle(tmp_path / "managed" / mab.APP_DIR_NAME)

    for language in table_module.LOCALIZED_LANGUAGES:
        relative = Path("Contents") / "Resources" / f"{language}.lproj" / "InfoPlist.strings"
        assert (dmg_app / relative).read_bytes() == (managed / relative).read_bytes()


def test_the_build_helper_refuses_a_directory_that_is_not_an_app(tmp_path: Path) -> None:
    helper_path = REPO_ROOT / "packaging" / "macos" / "add_localizations.py"
    loader_spec = importlib.util.spec_from_file_location("_add_localizations2", helper_path)
    assert loader_spec is not None and loader_spec.loader is not None
    helper = importlib.util.module_from_spec(loader_spec)
    loader_spec.loader.exec_module(helper)

    assert helper.main(["--app", str(tmp_path / "Nothing.app")]) == 1
    assert not (tmp_path / "Nothing.app").exists()
