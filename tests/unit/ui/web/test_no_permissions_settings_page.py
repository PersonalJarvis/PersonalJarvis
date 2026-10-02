"""Settings has no Privacy / Permissions page on any OS (the UI reset, ADR-0037 amendment).

macOS shows its own dialog when a feature first needs a permission, and the app adds
one toast for the silent failure after it. A Settings page for permissions (and its
nav entry, search entries and reset button) was deleted on purpose; this guard keeps
it from coming back unnoticed. It reads the frontend source the way the other AP-4
parity tests do, so it runs without node.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_FRONTEND = Path(__file__).resolve().parents[4] / "jarvis" / "ui" / "web" / "frontend" / "src"
_LOCALES = ("en", "de", "es")
_FORBIDDEN_SECTION_IDS = {"permissions", "privacy"}
_DELETED_PAGE_FILES = (
    "views/settings/PermissionsPanel.tsx",
    "components/permissions/PermissionPromptLayer.tsx",
    "components/permissions/PermissionPromptHost.tsx",
    "components/permissions/InlinePermissionNote.tsx",
)


def _settings_section_ids() -> list[str]:
    source = (_FRONTEND / "views" / "SettingsView.tsx").read_text(encoding="utf-8")
    block = source[source.index("const SECTIONS") :]
    block = block[: block.index("];")]
    return re.findall(r'\{\s*id:\s*"([^"]+)"', block)


def test_settings_sections_are_found() -> None:
    """The regex reads real sections (else the guard below would pass vacuously)."""
    ids = _settings_section_ids()
    assert "languages" in ids and "wake-word" in ids


def test_settings_has_no_permissions_or_privacy_section() -> None:
    assert not (set(_settings_section_ids()) & _FORBIDDEN_SECTION_IDS)


@pytest.mark.parametrize("locale", _LOCALES)
def test_no_locale_carries_a_permissions_nav_label(locale: str) -> None:
    data = json.loads((_FRONTEND / "i18n" / "locales" / f"{locale}.json").read_text("utf-8"))
    nav = data["settings_view"]["nav"]
    assert not (set(nav) & _FORBIDDEN_SECTION_IDS)


def test_settings_search_has_no_permissions_group() -> None:
    source = (_FRONTEND / "views" / "settings" / "settingsSearch.ts").read_text(encoding="utf-8")
    assert "nav.permissions" not in source
    ids = set(re.findall(r'\{\s*id:\s*"([^"]+)"', source))
    assert {"languages", "wake-word"} <= ids  # the regex reads the real groups
    assert not (ids & _FORBIDDEN_SECTION_IDS)


@pytest.mark.parametrize("relative", _DELETED_PAGE_FILES)
def test_the_deleted_permission_ui_stays_deleted(relative: str) -> None:
    assert not (_FRONTEND / relative).exists()
