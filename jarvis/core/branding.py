"""Stable product identity constants shared across runtime surfaces.

This module is intentionally standard-library-only and performs no filesystem or
network I/O. Persistent names are compatibility contracts: changing one without
an explicit migration would orphan an existing installation's files, credentials,
desktop entries, or update channel.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

PRODUCT_NAME = "Personal Jarvis"
PRODUCT_COMPACT_NAME = "PersonalJarvis"
PRODUCT_SLUG = "personal-jarvis"

CONFIG_FILE_NAME = "jarvis.toml"
KEYRING_SERVICE_NAME = PRODUCT_SLUG
CONTROL_KEY_PREFIX = "jctl_"
SESSION_COOKIE_NAME = "jarvis_session"
MANAGED_INSTALL_MARKER = ".jarvis-managed-install"
DEFAULT_INSTALL_DIR_NAME = ".personal-jarvis"

WINDOWS_USER_DATA_DIR_NAME = "Jarvis"
FALLBACK_USER_DATA_DIR_NAME = ".jarvis"
DESKTOP_OUTPUT_MIRROR_DIR_NAME = "Jarvis-Output"

WINDOWS_MUTEX_NAME = rf"Global\{PRODUCT_COMPACT_NAME}_v1"
WINDOWS_APP_USER_MODEL_ID = f"{PRODUCT_COMPACT_NAME}.{PRODUCT_COMPACT_NAME}"
WINDOWS_UNINSTALL_REGISTRY_SUBKEY = (
    rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{PRODUCT_COMPACT_NAME}"
)
WINDOWS_AUTOSTART_TASK_NAME = f"{PRODUCT_NAME} Autostart"
WINDOWS_SHORTCUT_FILE_NAME = f"{PRODUCT_NAME}.lnk"
WINDOWS_AUTOSTART_DESCRIPTION = f"{PRODUCT_NAME} (Autostart)"
WINDOWS_BRANDED_LAUNCHER_FILE_NAME = f"{PRODUCT_COMPACT_NAME}.exe"
WINDOWS_BRANDED_LAUNCHER_DIR_NAME = PRODUCT_COMPACT_NAME

MACOS_APP_NAME = PRODUCT_NAME
MACOS_APP_DIR_NAME = f"{MACOS_APP_NAME}.app"
MACOS_EXECUTABLE_NAME = PRODUCT_COMPACT_NAME
MACOS_BUNDLE_ID = f"com.{PRODUCT_SLUG}.desktop"
MACOS_AUTOSTART_LABEL = f"com.{PRODUCT_SLUG}.autostart"

LINUX_APP_NAME = PRODUCT_NAME
LINUX_DESKTOP_ENTRY_FILE_NAME = f"{PRODUCT_SLUG}.desktop"
LINUX_WM_CLASS = PRODUCT_SLUG

DEFAULT_OFFICIAL_REPO_SLUG = "PersonalJarvis/PersonalJarvis"
OFFICIAL_REPO_SLUG_ENV_VAR = "JARVIS_OFFICIAL_REPO_SLUG"


def resolve_official_repo_slug(environ: Mapping[str, str] | None = None) -> str:
    """Resolve one exact ``owner/repository`` slug from the process environment."""

    source = os.environ if environ is None else environ
    candidate = source.get(OFFICIAL_REPO_SLUG_ENV_VAR, "")
    if not candidate:
        return DEFAULT_OFFICIAL_REPO_SLUG
    parts = candidate.split("/")
    allowed = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if len(parts) != 2 or any(not part or not set(part) <= allowed for part in parts):
        raise ValueError(
            f"{OFFICIAL_REPO_SLUG_ENV_VAR} must be an exact owner/repository slug"
        )
    return candidate


OFFICIAL_REPO_SLUG = resolve_official_repo_slug()
OFFICIAL_REPO_URL = f"https://github.com/{OFFICIAL_REPO_SLUG}"
OFFICIAL_REPO_GIT_URL = f"{OFFICIAL_REPO_URL}.git"
OFFICIAL_RELEASES_LATEST_API_URL = (
    f"https://api.github.com/repos/{OFFICIAL_REPO_SLUG}/releases/latest"
)
UPDATER_USER_AGENT = f"{PRODUCT_COMPACT_NAME}-Updater"

__all__ = [
    "CONFIG_FILE_NAME",
    "CONTROL_KEY_PREFIX",
    "DEFAULT_OFFICIAL_REPO_SLUG",
    "DEFAULT_INSTALL_DIR_NAME",
    "DESKTOP_OUTPUT_MIRROR_DIR_NAME",
    "FALLBACK_USER_DATA_DIR_NAME",
    "KEYRING_SERVICE_NAME",
    "LINUX_APP_NAME",
    "LINUX_DESKTOP_ENTRY_FILE_NAME",
    "LINUX_WM_CLASS",
    "MACOS_APP_DIR_NAME",
    "MACOS_APP_NAME",
    "MACOS_AUTOSTART_LABEL",
    "MACOS_BUNDLE_ID",
    "MACOS_EXECUTABLE_NAME",
    "MANAGED_INSTALL_MARKER",
    "OFFICIAL_RELEASES_LATEST_API_URL",
    "OFFICIAL_REPO_GIT_URL",
    "OFFICIAL_REPO_SLUG",
    "OFFICIAL_REPO_SLUG_ENV_VAR",
    "OFFICIAL_REPO_URL",
    "PRODUCT_COMPACT_NAME",
    "PRODUCT_NAME",
    "PRODUCT_SLUG",
    "SESSION_COOKIE_NAME",
    "UPDATER_USER_AGENT",
    "WINDOWS_APP_USER_MODEL_ID",
    "WINDOWS_AUTOSTART_DESCRIPTION",
    "WINDOWS_AUTOSTART_TASK_NAME",
    "WINDOWS_BRANDED_LAUNCHER_DIR_NAME",
    "WINDOWS_BRANDED_LAUNCHER_FILE_NAME",
    "WINDOWS_MUTEX_NAME",
    "WINDOWS_SHORTCUT_FILE_NAME",
    "WINDOWS_UNINSTALL_REGISTRY_SUBKEY",
    "WINDOWS_USER_DATA_DIR_NAME",
    "resolve_official_repo_slug",
]
