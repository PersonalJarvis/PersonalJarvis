"""Semantic macOS Accessibility actions for Computer-Use.

The regular Computer-Use actuator can always fall back to verified pointer input,
but a labelled control should be activated through the Accessibility API when
macOS exposes a native action.  That keeps actions attached to the semantic UI
element instead of to pixels and mirrors the Accessibility-first rule used by
the vision stack.

All PyObjC imports stay lazy so importing this module is safe on Windows, Linux,
and headless test environments.
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

SemanticPressStatus = Literal["performed", "unsupported", "mismatch", "unavailable"]


@dataclass(frozen=True)
class SemanticPressResult:
    """Outcome of one best-effort native AX press attempt."""

    status: SemanticPressStatus
    detail: str

    @property
    def performed(self) -> bool:
        return self.status == "performed"


_AX_PARENT = "AXParent"
_AX_ROLE = "AXRole"
_AX_TITLE = "AXTitle"
_AX_VALUE = "AXValue"
_AX_DESCRIPTION = "AXDescription"
_AX_IDENTIFIER = "AXIdentifier"
_AX_PRESS = "AXPress"
_MAX_ANCESTORS = 4


def _permission_ready() -> bool:
    try:
        from jarvis.platform.permissions import (  # noqa: PLC0415
            PermissionId,
            get_system_permission_port,
        )

        return get_system_permission_port().runtime_access_granted(
            PermissionId.ACCESSIBILITY,
        )
    except Exception:  # noqa: BLE001
        logger.debug("macOS semantic Accessibility permission probe failed", exc_info=True)
        return False


def _copy_attr(element: Any, attribute: str) -> Any:
    getter = getattr(element, "copy_attribute_value", None)
    if callable(getter):
        try:
            return getter(attribute)
        except Exception:  # noqa: BLE001
            return None
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCopyAttributeValue,
        )
    except (ImportError, ModuleNotFoundError):
        return None
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
        return value if err == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _element_at_point(x: int, y: int) -> Any | None:
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCopyElementAtPosition,
            AXUIElementCreateSystemWide,
        )
    except (ImportError, ModuleNotFoundError):
        return None
    try:
        system = AXUIElementCreateSystemWide()
        err, element = AXUIElementCopyElementAtPosition(system, float(x), float(y), None)
        return element if err == 0 else None
    except Exception:  # noqa: BLE001
        logger.debug("AXUIElementCopyElementAtPosition failed", exc_info=True)
        return None


def _action_names(element: Any) -> tuple[str, ...] | None:
    getter = getattr(element, "copy_action_names", None)
    if callable(getter):
        try:
            value = getter()
            return tuple(str(item) for item in value)
        except Exception:  # noqa: BLE001
            return None
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCopyActionNames,
        )
    except (ImportError, ModuleNotFoundError):
        return None
    try:
        err, names = AXUIElementCopyActionNames(element, None)
        if err != 0 or names is None:
            return None
        return tuple(str(item) for item in names)
    except Exception:  # noqa: BLE001
        return None


def _perform_action(element: Any, action: str) -> bool:
    performer = getattr(element, "perform_action", None)
    if callable(performer):
        try:
            return bool(performer(action))
        except Exception:  # noqa: BLE001
            return False
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementPerformAction,
        )
    except (ImportError, ModuleNotFoundError):
        return False
    try:
        return AXUIElementPerformAction(element, action) == 0
    except Exception:  # noqa: BLE001
        logger.debug("AXUIElementPerformAction failed", exc_info=True)
        return False


def _label(element: Any, read_attr: Callable[[Any, str], Any]) -> str:
    for attr in (_AX_TITLE, _AX_DESCRIPTION, _AX_VALUE):
        value = read_attr(element, attr)
        if value not in (None, ""):
            return str(value)
    return ""


def _matches(
    element: Any,
    *,
    expected_name: str,
    expected_role: str,
    expected_automation_id: str,
    read_attr: Callable[[Any, str], Any],
) -> bool:
    if expected_automation_id:
        if str(read_attr(element, _AX_IDENTIFIER) or "") != expected_automation_id:
            return False
    elif expected_name:
        actual = _label(element, read_attr).casefold()
        if expected_name.casefold() not in actual:
            return False

    if expected_role:
        native_role = str(read_attr(element, _AX_ROLE) or "")
        try:
            from jarvis.vision.role_map import normalize_role  # noqa: PLC0415

            actual_role = normalize_role(native_role, "darwin") or native_role
        except Exception:  # noqa: BLE001
            actual_role = native_role.removeprefix("AX")
        if actual_role.casefold() != expected_role.casefold():
            return False
    return True


def try_press_at(
    x: int,
    y: int,
    *,
    expected_name: str = "",
    expected_role: str = "",
    expected_automation_id: str = "",
    pre_action_check: Callable[[], bool] | None = None,
    permission_check: Callable[[], bool] | None = None,
    element_at_point: Callable[[int, int], Any | None] | None = None,
    read_attr: Callable[[Any, str], Any] | None = None,
    action_names: Callable[[Any], tuple[str, ...] | None] | None = None,
    perform_action: Callable[[Any, str], bool] | None = None,
) -> SemanticPressResult:
    """Press the AX element at ``(x, y)`` when it matches the observed node.

    ``unsupported`` is the only status callers should use for a pointer
    fallback. ``mismatch`` and ``unavailable`` are fail-closed outcomes: the
    semantic identity can no longer be proven, so clicking the stale pixels
    would be unsafe.
    """
    if sys.platform != "darwin":
        return SemanticPressResult("unsupported", "semantic AX press is macOS-only")

    check_permission = permission_check or _permission_ready
    if not check_permission():
        return SemanticPressResult(
            "unavailable",
            "macOS Accessibility permission is not ready for a semantic UI action",
        )

    resolver = element_at_point or _element_at_point
    reader = read_attr or _copy_attr
    names_reader = action_names or _action_names
    performer = perform_action or _perform_action

    element = resolver(int(x), int(y))
    if element is None:
        return SemanticPressResult(
            "unavailable",
            "macOS could not resolve the Accessibility element at the selected point",
        )

    matched: Any | None = None
    current: Any | None = element
    for _ in range(_MAX_ANCESTORS + 1):
        if current is None:
            break
        if _matches(
            current,
            expected_name=expected_name,
            expected_role=expected_role,
            expected_automation_id=expected_automation_id,
            read_attr=reader,
        ):
            matched = current
            break
        current = reader(current, _AX_PARENT)

    if matched is None:
        return SemanticPressResult(
            "mismatch",
            "the Accessibility element at the click point no longer matches the observed target",
        )

    names = names_reader(matched)
    if names is None or _AX_PRESS not in names:
        return SemanticPressResult(
            "unsupported",
            "the matched Accessibility element does not expose AXPress",
        )

    if pre_action_check is not None and not pre_action_check():
        return SemanticPressResult(
            "mismatch",
            "foreground window changed before the semantic Accessibility action",
        )

    if not performer(matched, _AX_PRESS):
        return SemanticPressResult(
            "unavailable",
            "macOS rejected AXPress for the matched Accessibility element",
        )

    return SemanticPressResult(
        "performed",
        "performed native AXPress on the matched Accessibility element",
    )


__all__ = ["SemanticPressResult", "try_press_at"]
