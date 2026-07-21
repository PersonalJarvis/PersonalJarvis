"""Resolve the accessibility element under a screen point (AI Pointer; AD-6 seam).

The "not just screenshots" core: instead of guessing from pixels, ask the OS
"which UI element is at (x, y)?" via the native point query —
``IUIAutomation.ElementFromPoint`` (Windows), ``AXUIElementCopyElementAtPosition``
(macOS), ``Component.getAccessibleAtPoint`` (Linux AT-SPI) — and return a
:class:`PointerElement` (name, role, value, bounds, app/window).

Each per-OS resolver takes an injectable ``query`` callable so the wrapper logic
is unit-testable with fakes; the native query is the only OS-specific part. All
backends degrade to ``None`` and never raise (AD-6). The factory returns
:class:`NullPointerResolver` when no accessibility tree is available.

Import-cleanliness (HN-7): no platform-only package at module scope — pywinauto /
pyobjc / pyatspi are imported lazily inside the native query functions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities
from jarvis.vision.pointer_types import PointerElement

log = logging.getLogger(__name__)

# A native point query: (x, y) -> resolved element, or None when nothing matches.
PointQuery = Callable[[int, int], PointerElement | None]


@runtime_checkable
class PointerResolver(Protocol):
    """The seam every per-OS point resolver satisfies (AD-6)."""

    name: str

    def at(self, x: int, y: int) -> PointerElement | None:
        """Resolve the element at screen ``(x, y)`` or ``None``. Never raises."""
        ...


class _BaseResolver:
    """Shared try/except wrapper around an injectable native point query."""

    name: str = "base-pointer"
    _native: PointQuery | None = None

    def __init__(self, query: PointQuery | None = None) -> None:
        self._query = query

    def at(self, x: int, y: int) -> PointerElement | None:
        query = self._query or self._native
        if query is None:
            return None
        try:
            return query(int(x), int(y))
        except Exception:
            log.debug("%s point query failed at (%s, %s)", self.name, x, y, exc_info=True)
            return None


class WindowsPointerResolver(_BaseResolver):
    name = "windows-pointer"
    _native = staticmethod(lambda x, y: _win_query_element_at_point(x, y))


class AXPointerResolver(_BaseResolver):
    name = "macos-pointer"
    _native = staticmethod(lambda x, y: _ax_query_element_at_point(x, y))

    def at(self, x: int, y: int) -> PointerElement | None:
        """Query AX only for the canonical, currently authorized app process.

        This check deliberately runs for every point lookup: TCC grants can be
        revoked while Jarvis is running, and an ad-hoc Python/Terminal launch
        must never inherit a different executable's Accessibility grant.
        """
        try:
            from jarvis.platform.permissions import (  # noqa: PLC0415
                PermissionId,
                get_system_permission_port,
            )

            if not get_system_permission_port().runtime_access_granted(
                PermissionId.ACCESSIBILITY,
            ):
                return None
        except Exception:  # noqa: BLE001 - native permission failures fail closed
            log.debug("macOS Accessibility permission gate failed", exc_info=True)
            return None
        return super().at(x, y)


class AtspiPointerResolver(_BaseResolver):
    name = "linux-pointer"
    _native = staticmethod(lambda x, y: _atspi_query_element_at_point(x, y))


class NullPointerResolver:
    """AD-6 graceful fallback: no accessibility tree on this host."""

    name = "null-pointer"

    def at(self, x: int, y: int) -> PointerElement | None:
        return None


def make_pointer_resolver() -> PointerResolver:
    """Select the element-at-point resolver for this host (AD-6).

    Returns :class:`NullPointerResolver` when ``capabilities.has_ax_tree`` is
    ``False`` (headless VPS, or the OS accessibility backend is not installed).
    """
    caps = detect_capabilities()
    if not caps.has_ax_tree:
        return NullPointerResolver()
    plat = detect_platform()
    if plat == "win32":
        return WindowsPointerResolver()
    if plat == "darwin":
        return AXPointerResolver()
    if plat == "linux":
        return AtspiPointerResolver()
    return NullPointerResolver()


# ---------------------------------------------------------------------------
# Native point queries — lazy imports, defensive, OS-specific.
# ---------------------------------------------------------------------------

def _rect_to_bounds(rect: object) -> tuple[int, int, int, int]:
    """pywinauto rectangle (.left/.top/.right/.bottom) -> (x, y, w, h)."""
    if rect is None:
        return (0, 0, 0, 0)
    left = int(getattr(rect, "left", 0))
    top = int(getattr(rect, "top", 0))
    right = int(getattr(rect, "right", 0))
    bottom = int(getattr(rect, "bottom", 0))
    return (left, top, max(0, right - left), max(0, bottom - top))


def _win_element_from_raw(raw: Any) -> PointerElement | None:
    """Build a :class:`PointerElement` from a raw IUIAutomationElement."""
    from pywinauto.uia_element_info import UIAElementInfo  # noqa: PLC0415

    if raw is None:
        return None
    info = UIAElementInfo(raw)

    name = str(getattr(info, "name", "") or "").strip()
    role = str(getattr(info, "control_type", "") or "").strip()
    bounds = _rect_to_bounds(getattr(info, "rectangle", None))
    # Keyboard-focus state of the LEAF element itself (never an ancestor):
    # the CU click verification uses it as already-in-desired-state
    # evidence. Best-effort tri-state.
    try:
        focused: bool | None = bool(raw.CurrentHasKeyboardFocus)
    except Exception:  # noqa: BLE001 — property read is best-effort
        focused = None

    # Walk up to the nearest named ancestor (bounded) when the leaf is unnamed —
    # a deep custom-drawn leaf often has no Name, but its container does.
    cur = info
    hops = 0
    while not name and hops < 4:
        try:
            parent = cur.parent
        except Exception:
            break
        if parent is None:
            break
        cur = parent
        name = str(getattr(cur, "name", "") or "").strip()
        if not role:
            role = str(getattr(cur, "control_type", "") or "").strip()
        hops += 1

    value = _win_value(raw)
    window_title, app_name = _win_top_window(info)
    return PointerElement(
        name=name,
        role=role,
        value=value,
        bounds=bounds,
        app_name=app_name,
        window_title=window_title,
        source="ax_tree",
        focused=focused,
    )


def _win_query_element_at_point(x: int, y: int) -> PointerElement | None:
    """Windows: ``IUIAutomation.ElementFromPoint`` via pywinauto's comtypes singleton."""
    from ctypes import wintypes  # noqa: PLC0415

    from pywinauto.uia_defines import IUIA  # noqa: PLC0415

    iuia = IUIA().iuia
    point = wintypes.POINT(int(x), int(y))
    return _win_element_from_raw(iuia.ElementFromPoint(point))


def _win_query_focused_element() -> PointerElement | None:
    """Windows: ``IUIAutomation.GetFocusedElement`` — the control that holds
    keyboard focus, resolved natively and therefore DEPTH-INDEPENDENT (a
    Chrome omnibox nested below any tree-walk depth still resolves)."""
    from pywinauto.uia_defines import IUIA  # noqa: PLC0415

    element = _win_element_from_raw(IUIA().iuia.GetFocusedElement())
    if element is None:
        return None
    # GetFocusedElement IS the focus authority — stamp the flag even when
    # the per-element property read degraded to None.
    if element.focused is None:
        element = PointerElement(
            name=element.name,
            role=element.role,
            value=element.value,
            bounds=element.bounds,
            app_name=element.app_name,
            window_title=element.window_title,
            source=element.source,
            focused=True,
        )
    return element


def _win_value(raw: Any) -> str:
    """Best-effort read of the UIA Value.Value property (id 30045)."""
    try:
        val = raw.GetCurrentPropertyValue(30045)  # UIA_ValueValuePropertyId
        if val is None:
            return ""
        return str(val).strip()
    except Exception:
        return ""


def _win_top_window(info: object) -> tuple[str, str]:
    """Return (window_title, app_name) by walking up to the top-level window."""
    try:
        top = getattr(info, "top_level_parent", None)
        top = top() if callable(top) else top
    except Exception:
        top = None
    title = ""
    app = ""
    try:
        if top is not None:
            title = str(getattr(top, "name", "") or "").strip()
    except Exception:
        title = ""
    try:
        proc = int(getattr(info, "process_id", 0) or 0)
        if proc:
            import psutil  # noqa: PLC0415 - optional; used only when present

            app = psutil.Process(proc).name()
    except Exception:
        app = ""
    return title, app


def _ax_query_element_at_point(x: int, y: int) -> PointerElement | None:
    """macOS: ``AXUIElementCopyElementAtPosition`` on the system-wide element.

    Implemented per Apple's Accessibility API; unverified-on-real-desktop until
    an operator runs scripts/crossplatform on a Mac (see SIGNOFF-LOG).
    """
    from ApplicationServices import (  # noqa: PLC0415
        AXUIElementCopyElementAtPosition,
        AXUIElementCreateSystemWide,
    )

    system = AXUIElementCreateSystemWide()
    err, elem = AXUIElementCopyElementAtPosition(system, float(x), float(y), None)
    if err != 0 or elem is None:
        return None
    return _ax_element_to_pointer(elem)


def _ax_query_focused_element() -> PointerElement | None:
    """macOS: the system-wide ``kAXFocusedUIElement`` — depth-independent
    keyboard-focus resolution, mirroring the Windows ``GetFocusedElement``
    path. Unverified-on-real-desktop until an operator runs
    scripts/crossplatform on a Mac (see SIGNOFF-LOG)."""
    from ApplicationServices import (  # noqa: PLC0415
        AXUIElementCopyAttributeValue,
        AXUIElementCreateSystemWide,
    )

    system = AXUIElementCreateSystemWide()
    err, elem = AXUIElementCopyAttributeValue(
        system, "AXFocusedUIElement", None,
    )
    if err != 0 or elem is None:
        return None
    element = _ax_element_to_pointer(elem)
    if element is None or element.focused is not None:
        return element
    # The focused-element attribute IS the focus authority.
    return PointerElement(
        name=element.name,
        role=element.role,
        value=element.value,
        bounds=element.bounds,
        app_name=element.app_name,
        window_title=element.window_title,
        source=element.source,
        focused=True,
    )


def _ax_element_to_pointer(elem: Any) -> PointerElement | None:
    """Read the standard AX attributes off an ``AXUIElementRef``."""
    from ApplicationServices import (  # noqa: PLC0415
        AXUIElementCopyAttributeValue,
    )

    def _attr(name: str) -> str:
        try:
            e, val = AXUIElementCopyAttributeValue(elem, name, None)
            return str(val).strip() if e == 0 and val is not None else ""
        except Exception:
            return ""

    role = _attr("AXRole")
    name = _attr("AXTitle") or _attr("AXDescription")
    value = _attr("AXValue")
    try:
        err_f, val_f = AXUIElementCopyAttributeValue(elem, "AXFocused", None)
        focused: bool | None = bool(val_f) if err_f == 0 else None
    except Exception:  # noqa: BLE001 — attribute read is best-effort
        focused = None
    # kAXWindowAttribute returns the containing window ELEMENT, not a string
    # — str() on it would inject an object repr like '<AXUIElementRef 0x…>'
    # into the model-facing context (2026-07-02 review finding). Resolve the
    # window's AXTitle instead; any failure degrades to "".
    window_title = ""
    try:
        err_w, win_elem = AXUIElementCopyAttributeValue(elem, "AXWindow", None)
        if err_w == 0 and win_elem is not None:
            err_t, title_val = AXUIElementCopyAttributeValue(
                win_elem, "AXTitle", None,
            )
            if err_t == 0 and title_val is not None:
                window_title = str(title_val).strip()
    except Exception:  # noqa: BLE001 — window-title read is best-effort
        window_title = ""
    return PointerElement(
        name=name,
        role=role,
        value=value,
        bounds=(0, 0, 0, 0),
        app_name="",
        window_title=window_title,
        source="ax_tree",
        focused=focused,
    )


def _atspi_query_element_at_point(x: int, y: int) -> PointerElement | None:
    """Linux: descend from the AT-SPI desktop via ``getAccessibleAtPoint``.

    Implemented per the AT-SPI2 Component interface; unverified-on-real-desktop
    until an operator runs scripts/crossplatform on Linux (see SIGNOFF-LOG).
    """
    import pyatspi  # noqa: PLC0415

    coord = pyatspi.DESKTOP_COORDS
    desktop = pyatspi.Registry.getDesktop(0)

    def _component(acc: Any) -> Any | None:
        try:
            return acc.queryComponent()
        except Exception:
            return None

    def _hit(acc: Any) -> Any | None:
        comp = _component(acc)
        if comp is None:
            return None
        try:
            return comp.getAccessibleAtPoint(int(x), int(y), coord)
        except Exception:  # noqa: BLE001
            return None

    # Find the top-level surface under the point. The desktop's direct
    # children are AT-SPI APPLICATION objects, which do NOT implement the
    # Component interface — only their child frames/windows do (2026-07-02
    # review finding: hit-testing the applications directly returned None
    # for every query). Try the application first anyway (cheap, and some
    # toolkits do expose Component there), then descend one level into its
    # frames.
    node: Any = None
    for i in range(desktop.childCount):
        try:
            app = desktop.getChildAtIndex(i)
        except Exception:  # noqa: S112 - best-effort scan; the resolver wraps all errors
            continue
        if app is None:
            continue
        node = _hit(app)
        if node is not None:
            break
        try:
            frame_count = int(getattr(app, "childCount", 0) or 0)
        except Exception:  # noqa: BLE001
            frame_count = 0
        for j in range(frame_count):
            try:
                frame = app.getChildAtIndex(j)
            except Exception:  # noqa: S112 - skip an unreadable frame
                continue
            if frame is None:
                continue
            node = _hit(frame)
            if node is not None:
                break
        if node is not None:
            break

    if node is None:
        return None

    # Descend through containers to the deepest element under the point.
    for _ in range(12):
        comp = _component(node)
        if comp is None:
            break
        try:
            child = comp.getAccessibleAtPoint(int(x), int(y), coord)
        except Exception:
            child = None
        if child is None or child == node:
            break
        node = child

    try:
        name = str(node.name or "").strip()
    except Exception:
        name = ""
    try:
        role = str(node.getRoleName() or "").strip()
    except Exception:
        role = ""
    try:
        focused: bool | None = bool(
            node.getState().contains(pyatspi.STATE_FOCUSED),
        )
    except Exception:  # noqa: BLE001 — state read is best-effort
        focused = None
    return PointerElement(
        name=name,
        role=role,
        value="",
        bounds=(0, 0, 0, 0),
        app_name="",
        window_title="",
        source="ax_tree",
        focused=focused,
    )


def query_focused_element() -> PointerElement | None:
    """The control holding keyboard focus, resolved NATIVELY (depth-free).

    Windows: UIA ``GetFocusedElement``; macOS: system-wide
    ``AXFocusedUIElement`` (behind the same Accessibility permission gate as
    the point resolver); Linux: no depth-free focus probe exists yet —
    returns ``None`` (parity gap tracked in docs/os-parity.md). Degrades to
    ``None`` on any failure and never raises (AD-6).
    """
    try:
        if not detect_capabilities().has_ax_tree:
            return None
        plat = detect_platform()
        if plat == "win32":
            return _win_query_focused_element()
        if plat == "darwin":
            from jarvis.platform.permissions import (  # noqa: PLC0415
                PermissionId,
                get_system_permission_port,
            )

            if not get_system_permission_port().runtime_access_granted(
                PermissionId.ACCESSIBILITY,
            ):
                return None
            return _ax_query_focused_element()
        return None
    except Exception:  # noqa: BLE001 — focus probe is best-effort evidence
        log.debug("focused-element query failed", exc_info=True)
        return None


__all__ = [
    "PointerResolver",
    "PointQuery",
    "WindowsPointerResolver",
    "AXPointerResolver",
    "AtspiPointerResolver",
    "NullPointerResolver",
    "make_pointer_resolver",
    "query_focused_element",
]
