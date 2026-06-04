"""Native accessibility role -> canonical UIA role normalization (Wave 2.3, AD-10).

The Windows ``UIATreeSource`` emits roles drawn straight from UI Automation
(``Button``, ``Edit``, ``MenuItem`` …). The macOS AX tree (``AXButton``,
``AXTextField`` …) and the Linux AT-SPI tree (``ROLE_PUSH_BUTTON``,
``ROLE_ENTRY`` …) speak entirely different vocabularies. If those leaked into an
``Observation`` the model prompt, the role-whitelist pruning, and every test
would have to learn three role languages.

``normalize_role(native_role, platform)`` collapses all three onto the single
**canonical UIA vocabulary** the rest of the pipeline already uses — the union
of ``pruning.DEFAULT_INTERESTING_ROLES`` and
``screenshot_only_loop._CLICKABLE_UIA_ROLES``. After normalization the
``AXTreeSource`` / ``AtspiTreeSource`` write a ``UIANode.role`` that is byte-for-
byte identical to what the Windows source would have produced, so downstream
pruning, serialization, and the model prompt stay platform-agnostic (AD-10).

Unknown roles map to ``"Text"`` — visible to the model but not a pixel-guess
target — unless they are explicitly dropped (mapped to ``None``) for noise
roles (an AX/AT-SPI window, group, or scroll-area carries no clickable value).
``filter_by_role`` then prunes anything that is not in the interesting set.

Pure-Python, no platform-only import — runs and is tested on any OS.
"""

from __future__ import annotations

from typing import Literal

from jarvis.harness.screenshot_only_loop import _CLICKABLE_UIA_ROLES
from jarvis.vision.pruning import DEFAULT_INTERESTING_ROLES

RoleMapPlatform = Literal["darwin", "linux"]

# The canonical role vocabulary the model + pruning understand. Frozen union of
# the two authoritative sets already in the codebase, so a normalized role is by
# construction a member of one of them. Used by the acceptance test to prove no
# emitted role escapes the vocabulary.
CANONICAL_UIA_ROLES: frozenset[str] = frozenset(DEFAULT_INTERESTING_ROLES) | _CLICKABLE_UIA_ROLES

# Fallback for an unknown-but-present role: surfaced as plain text, never a
# pixel-guess click target.
_UNKNOWN_ROLE = "Text"

# A native role mapped to ``None`` is intentionally dropped before it ever
# reaches the canonical set (structural containers carry no clickable value and
# would only add noise / blow the node budget).
_DROP = None


# ---------------------------------------------------------------------------
# macOS AX role table (AXUIElement ``kAXRoleAttribute`` values).
# ---------------------------------------------------------------------------

_AX_ROLE_MAP: dict[str, str | None] = {
    "AXButton": "Button",
    "AXMenuButton": "Button",
    "AXTextField": "Edit",
    "AXTextArea": "Edit",
    "AXSearchField": "Edit",
    "AXComboBox": "ComboBox",
    "AXPopUpButton": "ComboBox",
    "AXMenuItem": "MenuItem",
    "AXMenuBarItem": "MenuItem",
    "AXCheckBox": "CheckBox",
    "AXRadioButton": "RadioButton",
    "AXLink": "Hyperlink",
    "AXStaticText": "Text",
    "AXTabGroup": "Tab",
    "AXRadioGroup": "Tab",
    "AXList": "List",
    "AXRow": "ListItem",
    "AXCell": "ListItem",
    "AXOutlineRow": "TreeItem",
    "AXDisclosureTriangle": "Button",
    "AXSlider": "Edit",
    "AXStepper": "Button",
    # Structural containers — dropped (no clickable value).
    "AXWindow": _DROP,
    "AXApplication": _DROP,
    "AXGroup": _DROP,
    "AXSplitGroup": _DROP,
    "AXScrollArea": _DROP,
    "AXScrollBar": _DROP,
    "AXToolbar": _DROP,
    "AXGenericElement": _DROP,
    "AXUnknown": _DROP,
}


# ---------------------------------------------------------------------------
# Linux AT-SPI role table (``pyatspi.ROLE_*`` names).
# ---------------------------------------------------------------------------

_ATSPI_ROLE_MAP: dict[str, str | None] = {
    "ROLE_PUSH_BUTTON": "Button",
    "ROLE_BUTTON": "Button",
    "ROLE_TOGGLE_BUTTON": "Button",
    "ROLE_TEXT": "Edit",
    "ROLE_ENTRY": "Edit",
    "ROLE_PASSWORD_TEXT": "Edit",
    "ROLE_SPIN_BUTTON": "Edit",
    "ROLE_COMBO_BOX": "ComboBox",
    "ROLE_MENU_ITEM": "MenuItem",
    "ROLE_CHECK_MENU_ITEM": "MenuItem",
    "ROLE_RADIO_MENU_ITEM": "MenuItem",
    "ROLE_CHECK_BOX": "CheckBox",
    "ROLE_RADIO_BUTTON": "RadioButton",
    "ROLE_LINK": "Hyperlink",
    "ROLE_LABEL": "Text",
    "ROLE_STATIC": "Text",
    "ROLE_TEXT_LABEL": "Text",
    "ROLE_PAGE_TAB": "TabItem",
    "ROLE_PAGE_TAB_LIST": "Tab",
    "ROLE_LIST": "List",
    "ROLE_LIST_BOX": "List",
    "ROLE_LIST_ITEM": "ListItem",
    "ROLE_TABLE_CELL": "ListItem",
    "ROLE_TABLE_ROW": "ListItem",
    "ROLE_TREE_ITEM": "TreeItem",
    "ROLE_MENU": "MenuItem",
    "ROLE_SLIDER": "Edit",
    # Structural containers — dropped.
    "ROLE_FRAME": _DROP,
    "ROLE_WINDOW": _DROP,
    "ROLE_APPLICATION": _DROP,
    "ROLE_PANEL": _DROP,
    "ROLE_FILLER": _DROP,
    "ROLE_SCROLL_PANE": _DROP,
    "ROLE_SCROLL_BAR": _DROP,
    "ROLE_TOOL_BAR": _DROP,
    "ROLE_SEPARATOR": _DROP,
    "ROLE_UNKNOWN": _DROP,
    "ROLE_REDUNDANT_OBJECT": _DROP,
    "ROLE_INVALID": _DROP,
}

_PLATFORM_TABLES: dict[str, dict[str, str | None]] = {
    "darwin": _AX_ROLE_MAP,
    "linux": _ATSPI_ROLE_MAP,
}


def normalize_role(native_role: str, platform: str) -> str | None:
    """Map a native AX/AT-SPI role onto the canonical UIA role vocabulary.

    * ``platform="darwin"`` consults the macOS AX table.
    * ``platform="linux"`` consults the Linux AT-SPI table.

    Returns a canonical UIA role string (guaranteed a member of
    ``CANONICAL_UIA_ROLES``), or ``None`` when the native role is an explicit
    structural-container drop. An *unknown* role (not in the table at all) maps
    to ``"Text"`` so it stays visible to the model without becoming a pixel
    target. Never raises (AD-6): an unrecognized ``platform`` also degrades to
    ``"Text"``.
    """
    table = _PLATFORM_TABLES.get(platform)
    if table is None:
        return _UNKNOWN_ROLE
    role = (native_role or "").strip()
    if role in table:
        return table[role]  # may be a canonical role or None (explicit drop)
    return _UNKNOWN_ROLE


__all__ = ["normalize_role", "CANONICAL_UIA_ROLES", "RoleMapPlatform"]
