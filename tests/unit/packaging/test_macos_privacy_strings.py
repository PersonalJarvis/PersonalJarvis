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
    assert "Music" in texts["NSAppleEventsUsageDescription"]
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
    assert imported <= {"__future__"}, imported
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
