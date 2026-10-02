"""Semantic macOS Accessibility actions for Computer-Use.

The regular Computer-Use actuator can always fall back to verified pointer input,
but a labelled control should be activated through the Accessibility API when
macOS exposes a native action. That keeps actions attached to the semantic UI
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
    """Outcome of one best-effort native AX action attempt."""

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
_AX_FOCUSED = "AXFocused"
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
            logger.debug("AX fake/wrapper attribute read failed: %s", attribute, exc_info=True)
            return None
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCopyAttributeValue,
        )
    except (ImportError, ModuleNotFoundError):  # optional PyObjC bridge may be unavailable
        return None
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
        return value if err == 0 else None
    except Exception:  # noqa: BLE001
        logger.debug("AXUIElementCopyAttributeValue failed: %s", attribute, exc_info=True)
        return None


def _attribute_settable(element: Any, attribute: str) -> bool | None:
    """Return whether ``attribute`` is writable, or ``None`` when AX could not tell us."""
    checker = getattr(element, "is_attribute_settable", None)
    if callable(checker):
        try:
            value = checker(attribute)
            return None if value is None else bool(value)
        except Exception:  # noqa: BLE001
            logger.debug("AX fake/wrapper settable probe failed: %s", attribute, exc_info=True)
            return None
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementIsAttributeSettable,
        )
    except (ImportError, ModuleNotFoundError):  # optional PyObjC bridge may be unavailable
        return None
    try:
        err, settable = AXUIElementIsAttributeSettable(element, attribute, None)
        return bool(settable) if err == 0 else None
    except Exception:  # noqa: BLE001
        logger.debug("AXUIElementIsAttributeSettable failed: %s", attribute, exc_info=True)
        return None


def _set_attr(element: Any, attribute: str, value: Any) -> bool:
    setter = getattr(element, "set_attribute_value", None)
    if callable(setter):
        try:
            return bool(setter(attribute, value))
        except Exception:  # noqa: BLE001
            logger.debug("AX fake/wrapper attribute write failed: %s", attribute, exc_info=True)
            return False
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementSetAttributeValue,
        )
    except (ImportError, ModuleNotFoundError):  # optional PyObjC bridge may be unavailable
        return False
    try:
        return AXUIElementSetAttributeValue(element, attribute, value) == 0
    except Exception:  # noqa: BLE001
        logger.debug("AXUIElementSetAttributeValue failed: %s", attribute, exc_info=True)
        return False


def _element_at_point(x: int, y: int) -> Any | None:
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCopyElementAtPosition,
            AXUIElementCreateSystemWide,
        )
    except (ImportError, ModuleNotFoundError):  # optional PyObjC bridge may be unavailable
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
            logger.debug("AX fake/wrapper action-list read failed", exc_info=True)
            return None
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCopyActionNames,
        )
    except (ImportError, ModuleNotFoundError):  # optional PyObjC bridge may be unavailable
        return None
    try:
        err, names = AXUIElementCopyActionNames(element, None)
        if err != 0 or names is None:
            return None
        return tuple(str(item) for item in names)
    except Exception:  # noqa: BLE001
        logger.debug("AXUIElementCopyActionNames failed", exc_info=True)
        return None


def _perform_action(element: Any, action: str) -> bool:
    performer = getattr(element, "perform_action", None)
    if callable(performer):
        try:
            return bool(performer(action))
        except Exception:  # noqa: BLE001
            logger.debug("AX fake/wrapper action failed: %s", action, exc_info=True)
            return False
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementPerformAction,
        )
    except (ImportError, ModuleNotFoundError):  # optional PyObjC bridge may be unavailable
        return False
    try:
        return AXUIElementPerformAction(element, action) == 0
    except Exception:  # noqa: BLE001
        logger.debug("AXUIElementPerformAction failed", exc_info=True)
        return False


def _label(element: Any, read_attr: Callable[[Any, str], Any]) -> str:
    native_role = str(read_attr(element, _AX_ROLE) or "").casefold()
    # Never inspect the current value of a secure text field. The vision tree
    # follows the same rule, so semantic re-identification cannot leak a secret.
    attributes = (_AX_TITLE, _AX_DESCRIPTION)
    if native_role != "axsecuretextfield":
        attributes += (_AX_VALUE,)
    for attr in attributes:
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
            logger.debug("macOS role normalization failed", exc_info=True)
            actual_role = native_role.removeprefix("AX")
        if actual_role.casefold() != expected_role.casefold():
            return False
    return True


def _resolve_match(
    x: int,
    y: int,
    *,
    expected_name: str,
    expected_role: str,
    expected_automation_id: str,
    permission_check: Callable[[], bool] | None,
    element_at_point: Callable[[int, int], Any | None] | None,
    read_attr: Callable[[Any, str], Any] | None,
) -> tuple[Any | None, Callable[[Any, str], Any], SemanticPressResult | None]:
    if sys.platform != "darwin":
        return None, read_attr or _copy_attr, SemanticPressResult(
            "unsupported", "semantic AX actions are macOS-only"
        )
    check_permission = permission_check or _permission_ready
    if not check_permission():
        return None, read_attr or _copy_attr, SemanticPressResult(
            "unavailable",
            "macOS Accessibility permission is not ready for a semantic UI action",
        )
    resolver = element_at_point or _element_at_point
    reader = read_attr or _copy_attr
    element = resolver(int(x), int(y))
    if element is None:
        return None, reader, SemanticPressResult(
            "unavailable",
            "macOS could not resolve the Accessibility element at the selected point",
        )
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
            return current, reader, None
        current = reader(current, _AX_PARENT)
    return None, reader, SemanticPressResult(
        "mismatch",
        "the Accessibility element at the click point no longer matches the observed target",
    )


def try_press_at(
    x: int,
    y: int,
    *,
    expected_name: str = "",
    expected_role: str = "",
    expected_automation_id: str = "",
    pre_action_check: Callable[[], bool] | None = None,
    ownership_check: Callable[[], None] | None = None,
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
    matched, _reader, failure = _resolve_match(
        x,
        y,
        expected_name=expected_name,
        expected_role=expected_role,
        expected_automation_id=expected_automation_id,
        permission_check=permission_check,
        element_at_point=element_at_point,
        read_attr=read_attr,
    )
    if failure is not None:
        return failure
    assert matched is not None
    names_reader = action_names or _action_names
    performer = perform_action or _perform_action
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
    if ownership_check is not None:
        ownership_check()
    if pre_action_check is not None and not pre_action_check():
        return SemanticPressResult(
            "mismatch",
            "foreground window changed immediately before the semantic Accessibility action",
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


def try_focus_at(
    x: int,
    y: int,
    *,
    expected_name: str = "",
    expected_role: str = "",
    expected_automation_id: str = "",
    pre_action_check: Callable[[], bool] | None = None,
    ownership_check: Callable[[], None] | None = None,
    permission_check: Callable[[], bool] | None = None,
    element_at_point: Callable[[int, int], Any | None] | None = None,
    read_attr: Callable[[Any, str], Any] | None = None,
    attribute_settable: Callable[[Any, str], bool | None] | None = None,
    set_attr: Callable[[Any, str, Any], bool] | None = None,
) -> SemanticPressResult:
    """Focus a matched editable AX element without moving the pointer.

    This is deliberately limited to the canonical ``Edit`` role. Buttons,
    menus and other controls retain their click semantics and use AXPress or
    the verified pointer fallback instead. A definitively non-settable focus
    attribute is an unsupported semantic operation and may fall back to a
    verified pointer click; an indeterminate probe or a failed write is treated
    as an unavailable semantic target and therefore fails closed.
    """
    if expected_role.casefold() != "edit":
        return SemanticPressResult(
            "unsupported", "semantic AX focus is reserved for editable controls"
        )
    matched, _reader, failure = _resolve_match(
        x,
        y,
        expected_name=expected_name,
        expected_role=expected_role,
        expected_automation_id=expected_automation_id,
        permission_check=permission_check,
        element_at_point=element_at_point,
        read_attr=read_attr,
    )
    if failure is not None:
        return failure
    assert matched is not None
    if pre_action_check is not None and not pre_action_check():
        return SemanticPressResult(
            "mismatch",
            "foreground window changed before the semantic Accessibility focus action",
        )

    settable_probe = attribute_settable or _attribute_settable
    settable = settable_probe(matched, _AX_FOCUSED)
    if settable is False:
        return SemanticPressResult(
            "unsupported",
            "the matched editable Accessibility element does not expose writable AXFocused",
        )
    if settable is None:
        return SemanticPressResult(
            "unavailable",
            "macOS could not verify that AXFocused is writable on the matched element",
        )

    if ownership_check is not None:
        ownership_check()
    if pre_action_check is not None and not pre_action_check():
        return SemanticPressResult(
            "mismatch",
            "foreground window changed immediately before the semantic Accessibility focus action",
        )

    setter = set_attr or _set_attr
    if not setter(matched, _AX_FOCUSED, True):
        return SemanticPressResult(
            "unavailable",
            "macOS rejected AXFocused for the matched editable Accessibility element",
        )
    return SemanticPressResult(
        "performed",
        "focused the matched editable Accessibility element with AXFocused",
    )


__all__ = ["SemanticPressResult", "try_focus_at", "try_press_at"]
