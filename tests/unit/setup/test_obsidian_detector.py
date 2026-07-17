"""Unit tests for ``jarvis.setup.obsidian`` (Phase B9.1 + B9.2 detector).

These tests are pure-Python and never touch the real ``%APPDATA%`` —
every JSON probe uses pytest's ``tmp_path``. The install-detection
tests monkeypatch ``Path.exists`` and the lazy ``winreg`` / ``win32api``
imports so we can simulate every install scenario deterministically.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from jarvis.setup.obsidian import (
    ObsidianDetection,
    ObsidianVaultsState,
    VaultEntry,
    detect_obsidian,
    find_registered_vault,
    is_vault_registered,
    read_obsidian_vaults,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _force_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the candidate-path-building env vars to deterministic values.

    Avoids accidentally matching whatever the test runner's actual
    ``%LOCALAPPDATA%`` etc. happen to point to.
    """
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\TestUser\AppData\Local")
    monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
    monkeypatch.setenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")


def _patch_exists(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> None:
    """Stub ``Path.exists`` to only report ``True`` for paths in ``present``.

    ``present`` holds the lowercased string form of paths we want to
    pretend exist; everything else returns ``False``.
    """
    present_lower = {p.lower() for p in present}

    def fake_exists(self: Path) -> bool:  # noqa: D401
        return str(self).lower() in present_lower

    monkeypatch.setattr(Path, "exists", fake_exists)


def _kill_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the lazy ``winreg`` import inside the module to fail.

    We swap a fake ``winreg`` whose ``OpenKey`` always raises ``OSError``
    so the registry-probe path returns ``None`` cleanly.
    """
    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER=0,
        HKEY_LOCAL_MACHINE=1,
        OpenKey=lambda *_a, **_k: (_ for _ in ()).throw(OSError("absent")),
        QueryValueEx=lambda *_a, **_k: (_ for _ in ()).throw(OSError("absent")),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)


def _kill_win32api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``win32api`` with a stub whose ``GetFileVersionInfo`` raises."""
    fake = types.SimpleNamespace(
        GetFileVersionInfo=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setitem(sys.modules, "win32api", fake)


def _supply_win32api_version(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    """Install a ``win32api`` stub that returns a fake FileVersionInfo dict.

    Encodes the version string into the ms/ls 32-bit pair so the
    module's bit-shift extraction yields ``version``.
    """
    parts = [int(p) for p in version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    a, b, c, d = parts[:4]
    info = {
        "FileVersionMS": (a << 16) | b,
        "FileVersionLS": (c << 16) | d,
    }
    fake = types.SimpleNamespace(GetFileVersionInfo=lambda *_a, **_k: info)
    monkeypatch.setitem(sys.modules, "win32api", fake)


# ---------------------------------------------------------------------------
# detect_obsidian()
# ---------------------------------------------------------------------------
def test_detect_obsidian_finds_default_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-user LocalAppData install is the first candidate and wins."""
    _force_env(monkeypatch)
    _kill_registry(monkeypatch)
    _kill_win32api(monkeypatch)

    local_path = r"C:\Users\TestUser\AppData\Local\Programs\Obsidian\Obsidian.exe"
    _patch_exists(monkeypatch, {local_path})

    result = detect_obsidian(platform="win32")

    assert isinstance(result, ObsidianDetection)
    assert result.installed is True
    assert result.exe_path is not None
    assert str(result.exe_path).lower() == local_path.lower()
    assert result.version is None  # win32api stub raises


def test_detect_obsidian_finds_system_wide_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live-user case: only ``%PROGRAMFILES%\\Obsidian\\Obsidian.exe`` exists.

    This is the scenario reported by the orchestrator's live probe.
    """
    _force_env(monkeypatch)
    _kill_registry(monkeypatch)
    _kill_win32api(monkeypatch)

    pf_path = r"C:\Program Files\Obsidian\Obsidian.exe"
    _patch_exists(monkeypatch, {pf_path})

    result = detect_obsidian(platform="win32")

    assert result.installed is True
    assert result.exe_path is not None
    assert str(result.exe_path).lower() == pf_path.lower()


def test_detect_obsidian_no_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing on disk, registry empty → installed=False everywhere None."""
    _force_env(monkeypatch)
    _kill_registry(monkeypatch)
    _kill_win32api(monkeypatch)
    _patch_exists(monkeypatch, set())

    result = detect_obsidian(platform="win32")

    assert result.installed is False
    assert result.exe_path is None
    assert result.version is None


def test_detect_obsidian_version_extraction_fails_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Obsidian is found but win32api raises, version is None and we
    do NOT crash. The detection itself still succeeds.
    """
    _force_env(monkeypatch)
    _kill_registry(monkeypatch)
    _kill_win32api(monkeypatch)  # GetFileVersionInfo raises

    pf_path = r"C:\Program Files\Obsidian\Obsidian.exe"
    _patch_exists(monkeypatch, {pf_path})

    result = detect_obsidian(platform="win32")

    assert result.installed is True
    assert result.exe_path is not None
    assert result.version is None


def test_detect_obsidian_extracts_version_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bonus: confirms the version-decoding bit math is correct."""
    _force_env(monkeypatch)
    _kill_registry(monkeypatch)
    _supply_win32api_version(monkeypatch, "1.5.12.0")

    pf_path = r"C:\Program Files\Obsidian\Obsidian.exe"
    _patch_exists(monkeypatch, {pf_path})

    result = detect_obsidian(platform="win32")

    assert result.installed is True
    assert result.version == "1.5.12.0"


def test_detect_obsidian_linux_uses_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux installs exposed on PATH are detected without Windows APIs."""
    monkeypatch.setattr(
        "jarvis.setup.obsidian.shutil.which",
        lambda _name: "/usr/bin/obsidian",
    )

    result = detect_obsidian(platform="linux")

    assert result.installed is True
    assert result.exe_path == Path("/usr/bin/obsidian")
    assert result.version is None


def test_detect_obsidian_macos_app_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The standard macOS app bundle is detected when no CLI is on PATH."""
    monkeypatch.setattr("jarvis.setup.obsidian.shutil.which", lambda _name: None)
    app_binary = "/Applications/Obsidian.app/Contents/MacOS/Obsidian"
    _patch_exists(monkeypatch, {str(Path(app_binary))})

    result = detect_obsidian(platform="darwin")

    assert result.installed is True
    assert result.exe_path == Path(app_binary)
    assert result.version is None


# ---------------------------------------------------------------------------
# read_obsidian_vaults()
# ---------------------------------------------------------------------------
def test_read_obsidian_vaults_no_config_file(tmp_path: Path) -> None:
    """Missing obsidian.json → config_exists=False, no vaults, no raise."""
    fake_cfg = tmp_path / "obsidian" / "obsidian.json"  # does not exist
    state = read_obsidian_vaults(config_path=fake_cfg)

    assert isinstance(state, ObsidianVaultsState)
    assert state.config_exists is False
    assert state.vaults == []
    assert state.config_path == fake_cfg


def test_read_obsidian_vaults_two_vaults(tmp_path: Path) -> None:
    """A well-formed file with two vaults yields two parsed entries; ids preserved."""
    cfg = tmp_path / "obsidian.json"
    payload = {
        "vaults": {
            "abc123id": {
                "path": r"C:\Users\Test\Documents\First Vault",
                "ts": 1778597706074,
                "open": True,
            },
            "def456id": {
                "path": r"D:\Workspace\Second Vault",
                "ts": 1778597700000,
                "open": False,
            },
        }
    }
    cfg.write_text(json.dumps(payload), encoding="utf-8")

    state = read_obsidian_vaults(config_path=cfg)

    assert state.config_exists is True
    assert len(state.vaults) == 2
    ids = {v.id for v in state.vaults}
    assert ids == {"abc123id", "def456id"}

    first = next(v for v in state.vaults if v.id == "abc123id")
    assert first.ts == 1778597706074
    assert first.is_open is True
    assert str(first.path).endswith("First Vault")

    second = next(v for v in state.vaults if v.id == "def456id")
    assert second.is_open is False


def test_read_obsidian_vaults_corrupt_json_raises(tmp_path: Path) -> None:
    """Garbage JSON surfaces as ValueError mentioning the filename."""
    cfg = tmp_path / "obsidian.json"
    cfg.write_text("{not valid json}", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        read_obsidian_vaults(config_path=cfg)

    assert "obsidian.json" in str(exc_info.value)


def test_read_obsidian_vaults_empty_vaults_dict(tmp_path: Path) -> None:
    """Valid JSON without any vaults yields an empty list, no crash."""
    cfg = tmp_path / "obsidian.json"
    cfg.write_text(json.dumps({"vaults": {}}), encoding="utf-8")

    state = read_obsidian_vaults(config_path=cfg)

    assert state.config_exists is True
    assert state.vaults == []


def test_read_obsidian_vaults_skips_malformed_entries(tmp_path: Path) -> None:
    """Defensive: bogus inner entries are skipped, not raised on."""
    cfg = tmp_path / "obsidian.json"
    cfg.write_text(
        json.dumps(
            {
                "vaults": {
                    "good": {"path": r"C:\good", "ts": 1, "open": True},
                    "bad-no-path": {"ts": 1, "open": True},
                    "bad-not-dict": "literally a string",
                }
            }
        ),
        encoding="utf-8",
    )

    state = read_obsidian_vaults(config_path=cfg)

    assert state.config_exists is True
    assert {v.id for v in state.vaults} == {"good"}


# ---------------------------------------------------------------------------
# is_vault_registered()
# ---------------------------------------------------------------------------
def test_is_vault_registered_match() -> None:
    """Case-insensitive comparison: differing case still matches."""
    vaults = [
        VaultEntry(id="x", path=Path(r"C:\Users\Admin\Documents\Obsidian Vault")),
    ]
    expected = Path(r"c:\users\admin\documents\obsidian vault")

    assert is_vault_registered(vaults, expected) is True


def test_is_vault_registered_no_match() -> None:
    """A path that simply isn't registered returns False."""
    vaults = [
        VaultEntry(id="x", path=Path(r"C:\Foo\Bar")),
        VaultEntry(id="y", path=Path(r"C:\Baz\Qux")),
    ]
    expected = Path(r"C:\Totally\Different")

    assert is_vault_registered(vaults, expected) is False


def test_is_vault_registered_trailing_slash_robust() -> None:
    """One path with trailing backslash, the other without, still match."""
    vaults = [
        VaultEntry(id="x", path=Path(r"C:\Users\Admin\Vaults\jarvis\\")),
    ]
    expected = Path(r"C:\Users\Admin\Vaults\jarvis")

    assert is_vault_registered(vaults, expected) is True


def test_is_vault_registered_empty_vault_list() -> None:
    """An empty registry is trivially a no-match."""
    assert is_vault_registered([], Path(r"C:\anything")) is False


def test_normalize_for_compare_case_insensitive_on_macos() -> None:
    """macOS default APFS volumes are case-insensitive: differing case must
    normalise identically on darwin, but stay distinct on Linux."""
    from jarvis.setup.obsidian import _normalize_for_compare

    upper = Path("/Users/Casey/.personal-jarvis/wiki/obsidian-vault")
    lower = Path("/users/casey/.personal-jarvis/wiki/obsidian-vault")

    assert _normalize_for_compare(upper, platform="darwin") == (
        _normalize_for_compare(lower, platform="darwin")
    )
    assert _normalize_for_compare(upper, platform="linux") != (
        _normalize_for_compare(lower, platform="linux")
    )


def test_is_vault_registered_for_subdirectory_of_registered_vault(
    tmp_path: Path,
) -> None:
    """Existing-vault mode keeps Jarvis in a reachable child directory."""
    parent = tmp_path / "Notes"
    expected = parent / "Jarvis"
    vaults = [VaultEntry(id="parent", path=parent)]

    assert is_vault_registered(vaults, expected) is True
    assert find_registered_vault(vaults, expected) == vaults[0]


def test_find_registered_vault_prefers_most_specific_container(tmp_path: Path) -> None:
    root = tmp_path / "Notes"
    nested = root / "Team"
    expected = nested / "Jarvis"
    vaults = [
        VaultEntry(id="root", path=root),
        VaultEntry(id="nested", path=nested),
    ]

    assert find_registered_vault(vaults, expected) == vaults[1]


# ---------------------------------------------------------------------------
# _default_obsidian_config_path()
# ---------------------------------------------------------------------------
class TestDefaultConfigPathCrossPlatform:
    """obsidian.json lives in a platform-specific config dir (spec A6)."""

    def test_windows_uses_appdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        from jarvis.setup.obsidian import _default_obsidian_config_path
        p = _default_obsidian_config_path(platform="win32")
        assert p == tmp_path / "obsidian" / "obsidian.json"

    def test_macos_uses_application_support(self, monkeypatch):
        from jarvis.setup.obsidian import _default_obsidian_config_path
        p = _default_obsidian_config_path(platform="darwin")
        assert p == (
            Path.home() / "Library" / "Application Support"
            / "obsidian" / "obsidian.json"
        )

    def test_linux_prefers_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from jarvis.setup.obsidian import _default_obsidian_config_path
        p = _default_obsidian_config_path(platform="linux")
        assert p == tmp_path / "obsidian" / "obsidian.json"

    def test_linux_falls_back_to_dot_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        from jarvis.setup.obsidian import _default_obsidian_config_path
        p = _default_obsidian_config_path(platform="linux")
        assert p == Path.home() / ".config" / "obsidian" / "obsidian.json"
