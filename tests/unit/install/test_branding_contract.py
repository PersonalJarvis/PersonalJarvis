"""Keep standalone bootstrap projections aligned with core branding."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.core.branding import (
    CONFIG_FILE_NAME,
    DEFAULT_INSTALL_DIR_NAME,
    DEFAULT_OFFICIAL_REPO_SLUG,
    KEYRING_SERVICE_NAME,
    LINUX_DESKTOP_ENTRY_FILE_NAME,
    MACOS_APP_DIR_NAME,
    MACOS_AUTOSTART_LABEL,
    OFFICIAL_REPO_SLUG_ENV_VAR,
    WINDOWS_APP_USER_MODEL_ID,
    WINDOWS_SHORTCUT_FILE_NAME,
    WINDOWS_UNINSTALL_REGISTRY_SUBKEY,
)

_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("relative_path", "default_pattern"),
    [
        (
            "install/install.sh",
            r"JARVIS_OFFICIAL_REPO_SLUG:-(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
        ),
        (
            "install/install.ps1",
            r"OfficialRepoSlug = '(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)'",
        ),
        (
            "install/install-verify.sh",
            r'DEFAULT_OFFICIAL_REPO_SLUG="(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"',
        ),
        (
            "install/install-verify.ps1",
            r"EXPECTED_REPO = '(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)'",
        ),
    ],
)
def test_installer_slug_projection_matches_canonical_default(
    relative_path: str, default_pattern: str
) -> None:
    source = (_ROOT / relative_path).read_text(encoding="utf-8")
    match = re.search(default_pattern, source)
    assert match is not None
    assert match.group("slug") == DEFAULT_OFFICIAL_REPO_SLUG
    assert OFFICIAL_REPO_SLUG_ENV_VAR in source
    assert "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$" in source


def test_quick_install_urls_are_derived_from_resolved_slug() -> None:
    shell = (_ROOT / "install/install.sh").read_text(encoding="utf-8")
    powershell = (_ROOT / "install/install.ps1").read_text(encoding="utf-8")
    assert "https://github.com/${OFFICIAL_REPO_SLUG}.git" in shell
    assert '"https://github.com/$OfficialRepoSlug.git"' in powershell


def test_verifier_identity_regexes_are_derived_from_resolved_slug() -> None:
    shell = (_ROOT / "install/install-verify.sh").read_text(encoding="utf-8")
    powershell = (_ROOT / "install/install-verify.ps1").read_text(encoding="utf-8")
    assert "${_expected_repo_regex}" in shell
    assert "[regex]::Escape($EXPECTED_REPO)" in powershell


@pytest.mark.parametrize(
    ("relative_path", "variable", "quote", "expected"),
    [
        ("install/install.sh", "CONFIG_FILE_NAME", '"', CONFIG_FILE_NAME),
        (
            "install/install.sh",
            "MACOS_AUTOSTART_LABEL",
            '"',
            MACOS_AUTOSTART_LABEL,
        ),
        ("install/install.ps1", "$ConfigFileName", "'", CONFIG_FILE_NAME),
        (
            "install/uninstall.sh",
            "DEFAULT_INSTALL_DIR_NAME",
            '"',
            DEFAULT_INSTALL_DIR_NAME,
        ),
        ("install/uninstall.sh", "MACOS_APP_DIR_NAME", '"', MACOS_APP_DIR_NAME),
        (
            "install/uninstall.sh",
            "LINUX_DESKTOP_ENTRY_FILE_NAME",
            '"',
            LINUX_DESKTOP_ENTRY_FILE_NAME,
        ),
        (
            "install/uninstall.sh",
            "KEYRING_SERVICE_NAME",
            '"',
            KEYRING_SERVICE_NAME,
        ),
        (
            "install/uninstall.ps1",
            "$DefaultInstallDirName",
            "'",
            DEFAULT_INSTALL_DIR_NAME,
        ),
        (
            "install/uninstall.ps1",
            "$WindowsShortcutFileName",
            "'",
            WINDOWS_SHORTCUT_FILE_NAME,
        ),
        (
            "install/uninstall.ps1",
            "$WindowsUninstallRegistrySubkey",
            "'",
            WINDOWS_UNINSTALL_REGISTRY_SUBKEY,
        ),
        (
            "install/uninstall.ps1",
            "$WindowsAppUserModelId",
            "'",
            WINDOWS_APP_USER_MODEL_ID,
        ),
    ],
)
def test_standalone_identity_projection_matches_canonical_value(
    relative_path: str,
    variable: str,
    quote: str,
    expected: str,
) -> None:
    source = (_ROOT / relative_path).read_text(encoding="utf-8")
    assignment = f"{variable} = {quote}{expected}{quote}"
    compact_assignment = f"{variable}={quote}{expected}{quote}"
    assert assignment in source or compact_assignment in source
