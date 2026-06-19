"""Unit tests for native-role -> canonical-UIA-role normalization (Wave 2.3)."""

from __future__ import annotations

import pytest

from jarvis.vision.role_map import CANONICAL_UIA_ROLES, normalize_role

# Representative macOS AX roles and their expected canonical UIA role.
_AX_EXPECTED = {
    "AXButton": "Button",
    "AXTextField": "Edit",
    "AXTextArea": "Edit",
    "AXPopUpButton": "ComboBox",
    "AXComboBox": "ComboBox",
    "AXMenuItem": "MenuItem",
    "AXCheckBox": "CheckBox",
    "AXRadioButton": "RadioButton",
    "AXLink": "Hyperlink",
    "AXStaticText": "Text",
    "AXRow": "ListItem",
    "AXCell": "ListItem",
    "AXOutlineRow": "TreeItem",
    "AXTabGroup": "Tab",
}

# Representative Linux AT-SPI roles and their expected canonical UIA role.
_ATSPI_EXPECTED = {
    "ROLE_PUSH_BUTTON": "Button",
    "ROLE_TEXT": "Edit",
    "ROLE_ENTRY": "Edit",
    "ROLE_COMBO_BOX": "ComboBox",
    "ROLE_MENU_ITEM": "MenuItem",
    "ROLE_CHECK_BOX": "CheckBox",
    "ROLE_RADIO_BUTTON": "RadioButton",
    "ROLE_LINK": "Hyperlink",
    "ROLE_LABEL": "Text",
    "ROLE_PAGE_TAB": "TabItem",
    "ROLE_LIST_ITEM": "ListItem",
    "ROLE_TABLE_CELL": "ListItem",
    "ROLE_TREE_ITEM": "TreeItem",
}


@pytest.mark.parametrize(("native", "expected"), sorted(_AX_EXPECTED.items()))
def test_macos_ax_roles_normalize(native: str, expected: str) -> None:
    assert normalize_role(native, "darwin") == expected


@pytest.mark.parametrize(("native", "expected"), sorted(_ATSPI_EXPECTED.items()))
def test_linux_atspi_roles_normalize(native: str, expected: str) -> None:
    assert normalize_role(native, "linux") == expected


def test_smoke_assertions_from_spec() -> None:
    # Exactly the two probes the WELLE-2 acceptance criterion runs.
    assert normalize_role("AXButton", "darwin") == "Button"
    assert normalize_role("ROLE_PUSH_BUTTON", "linux") == "Button"


def test_every_emitted_role_is_canonical() -> None:
    """No mapped role escapes the canonical UIA vocabulary."""
    for native, expected in {**_AX_EXPECTED}.items():
        assert expected in CANONICAL_UIA_ROLES, native
    for native, expected in {**_ATSPI_EXPECTED}.items():
        assert expected in CANONICAL_UIA_ROLES, native


def test_unknown_role_maps_to_text() -> None:
    assert normalize_role("AXSomethingNovel", "darwin") == "Text"
    assert normalize_role("ROLE_NOVEL_THING", "linux") == "Text"
    assert "Text" in CANONICAL_UIA_ROLES


def test_unknown_platform_degrades_to_text() -> None:
    # AD-6: never raise; an unrecognized platform degrades to Text.
    assert normalize_role("AXButton", "haiku-os") == "Text"


def test_structural_containers_are_dropped() -> None:
    # Containers map to None so the role-whitelist prune removes them.
    for native in ("AXWindow", "AXGroup", "AXScrollArea"):
        assert normalize_role(native, "darwin") is None
    for native in ("ROLE_FRAME", "ROLE_PANEL", "ROLE_FILLER"):
        assert normalize_role(native, "linux") is None


def test_blank_role_maps_to_text() -> None:
    assert normalize_role("", "darwin") == "Text"
    assert normalize_role("   ", "linux") == "Text"
