"""AXTreeSource — pruned macOS Accessibility (AX) tree via ``pyobjc`` (Wave 2.1, AD-10).

The macOS sibling of ``jarvis.vision.uia_tree.UIATreeSource``. It satisfies the
same ``VisionSource`` Protocol (``protocols.py:419``), produces the same
``Observation`` carrying a tuple of ``UIANode`` with the identical field layout,
and reuses the identical pruning ladder — so downstream pruning, serialization,
and the model prompt are platform-agnostic. The only macOS-specific work is:

1. Resolving the frontmost application via ``NSWorkspace`` and building its
   ``AXUIElement`` root.
2. Walking the AX tree depth-first, reading ``kAXRoleAttribute`` /
   ``kAXTitleAttribute`` / ``kAXValueAttribute`` / position+size / enabled /
   children, and flattening into ``RawNode``.
3. Normalizing each native ``AX*`` role onto the canonical UIA vocabulary via
   ``jarvis.vision.role_map.normalize_role`` so the emitted ``UIANode.role`` is
   exactly what the Windows source would have produced.

Permission gate (AD-13): before walking, ``observe`` checks
``AXIsProcessTrusted()``. If accessibility access is not granted it logs an
English onboarding message and returns an ``Observation`` with empty ``nodes``
and ``source="screenshot_only"`` — never raises. That empty-tree path self-gates
the consumers back to the already-working pixel-click loop, exactly like
``_foreground_clickable_labels`` returning ``[]`` (AD-6 / AD-OE6).

Import-cleanliness contract (HN-7): the pyobjc frameworks
(``Quartz`` / ``ApplicationServices`` / ``HIServices`` / ``AppKit``) are imported
**lazily inside ``observe``**, never at module scope. ``import
jarvis.vision.ax_tree`` therefore succeeds on a Windows or headless-Linux box
with no pyobjc installed, and ``isinstance(AXTreeSource(), VisionSource)`` holds
on every OS.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any, Literal
from uuid import uuid4

from jarvis.core.protocols import CancelToken, Observation, UIANode

from .pruning import (
    DEFAULT_INTERESTING_ROLES,
    DEFAULT_MAX_NODES,
    RawNode,
    prune_tree,
)
from .role_map import normalize_role

logger = logging.getLogger(__name__)

# Same retry ladder as the Windows source: shrink the traversal depth on
# node-overflow rather than ship a 5000-node tree to the model.
_DEPTH_RETRY_LADDER: tuple[int, ...] = (6, 5, 4)

# Onboarding message surfaced once when the macOS Accessibility permission is
# missing — English-only, per the Output Language Policy + AD-13.
_AX_PERMISSION_MSG = (
    "macOS Accessibility permission not granted — grant it in "
    "System Settings > Privacy & Security > Accessibility to enable named UI "
    "clicks; falling back to pixel clicks."
)


class AXTreeSource:
    """Reads the frontmost app's macOS Accessibility tree and prunes it."""

    name: str = "ax-tree"
    kind: Literal["screenshot", "ui_tree", "composite"] = "ui_tree"

    def __init__(
        self,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        interesting_roles: tuple[str, ...] = DEFAULT_INTERESTING_ROLES,
        min_on_screen_overlap: float = 0.5,
        monitor_bounds: tuple[int, int, int, int] | None = None,
        # Dependency-injection for tests: a callable that accepts the depth and
        # window-title filter and returns (root_title, pid, list[RawNode]). When
        # provided, the real pyobjc traversal is bypassed entirely.
        traverser: Callable[..., tuple[str, int, list[RawNode]]] | None = None,
        # Test seam for the permission gate: returns True when AX access is
        # granted. Defaults to the real ``AXIsProcessTrusted()`` lazy probe.
        permission_check: Callable[[], bool] | None = None,
    ) -> None:
        self._max_nodes = max_nodes
        self._interesting_roles = interesting_roles
        self._min_overlap = min_on_screen_overlap
        self._monitor_bounds = monitor_bounds
        self._traverser = traverser
        self._permission_check = permission_check
        self._closed = False

    # ---- Public API --------------------------------------------------------

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,
    ) -> Observation:
        if self._closed:
            raise RuntimeError("AXTreeSource is closed")
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        # Permission gate (AD-13): never raise — degrade to the pixel path.
        permission_check = self._permission_check or self._ax_is_process_trusted
        if not await asyncio.to_thread(permission_check):
            logger.warning(_AX_PERMISSION_MSG)
            return self._empty_observation()

        monitor_bounds = self._monitor_bounds or await asyncio.to_thread(
            self._detect_primary_monitor_bounds
        )

        nodes_before = 0
        depth_used = _DEPTH_RETRY_LADDER[0]
        pruned: list[RawNode] = []
        window_title = ""
        active_pid = 0
        for depth in _DEPTH_RETRY_LADDER:
            if cancel_token is not None and cancel_token.is_cancelled():
                raise RuntimeError(f"cancelled: {cancel_token.reason}")
            traverse_fn = self._traverser or self._traverse_via_pyobjc
            window_title, active_pid, raw_nodes = await asyncio.to_thread(
                traverse_fn,
                depth,
                window_title_filter,
            )
            nodes_before = len(raw_nodes)
            pruned = prune_tree(
                raw_nodes,
                max_depth=depth,
                interesting_roles=self._interesting_roles,
                monitor_bounds=monitor_bounds,
                min_overlap=self._min_overlap,
            )
            depth_used = depth
            if len(pruned) <= self._max_nodes:
                break

        overflow = len(pruned) > self._max_nodes
        if overflow:
            logger.warning(
                "AX-Tree pruning overflow: %d nodes after depth-%d retry ladder",
                len(pruned),
                depth_used,
            )
            nodes: tuple[UIANode, ...] = ()
            source: Literal["full", "screenshot_only", "ui_tree_only"] = "screenshot_only"
        else:
            nodes = tuple(self._to_uia_nodes(pruned))
            source = "ui_tree_only"

        tree_hash = self._hash_tree(window_title, active_pid, nodes)

        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash=tree_hash,
            nodes=nodes,
            window_title=window_title,
            active_pid=active_pid,
            source=source,
            pruning_stats={
                "nodes_before": nodes_before,
                "nodes_after": len(nodes),
                "depth_used": depth_used,
            },
        )

    async def close(self) -> None:
        self._closed = True

    # ---- Permission gate ----------------------------------------------------

    @staticmethod
    def _ax_is_process_trusted() -> bool:
        """Real ``AXIsProcessTrusted()`` probe (lazy pyobjc import, HN-7).

        Returns ``False`` when pyobjc is absent or the call raises — both mean
        "cannot read the AX tree", which routes the caller to the pixel path.
        """
        try:
            from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
                AXIsProcessTrusted,
            )
        except (ImportError, ModuleNotFoundError):
            logger.warning("pyobjc (ApplicationServices) not installed — AX tree empty")
            return False
        try:
            return bool(AXIsProcessTrusted())
        except Exception:  # noqa: BLE001
            logger.debug("AXIsProcessTrusted() raised", exc_info=True)
            return False

    def _empty_observation(self) -> Observation:
        """The AD-13 degrade: empty tree, ``source="screenshot_only"``."""
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash=self._hash_tree("", 0, ()),
            nodes=(),
            window_title="",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={"nodes_before": 0, "nodes_after": 0, "depth_used": 0},
        )

    # ---- Traversal (pyobjc) -------------------------------------------------

    @staticmethod
    def _detect_primary_monitor_bounds() -> tuple[int, int, int, int]:
        """Primary-monitor bounds via ``Quartz`` (lazy import, HN-7).

        Best-effort: if Quartz is unavailable the FullHD default only makes the
        on-screen overlap filter conservative, which is acceptable.
        """
        try:
            from Quartz import (  # type: ignore[import-not-found] # noqa: PLC0415
                CGDisplayBounds,
                CGMainDisplayID,
            )
        except (ImportError, ModuleNotFoundError):
            return (0, 0, 1920, 1080)
        try:
            rect = CGDisplayBounds(CGMainDisplayID())
            return (
                int(rect.origin.x),
                int(rect.origin.y),
                int(rect.size.width),
                int(rect.size.height),
            )
        except Exception:  # noqa: BLE001
            logger.debug("CGDisplayBounds unavailable", exc_info=True)
            return (0, 0, 1920, 1080)

    @staticmethod
    def _traverse_via_pyobjc(
        max_depth: int,
        window_title_filter: str | None,
    ) -> tuple[str, int, list[RawNode]]:
        """Flatten the frontmost app's AX tree to ``max_depth`` (lazy import).

        Returns ``(window_title, pid, nodes)``. On any failure (no frontmost
        app, pyobjc missing, AX call raises) it returns an empty result — never
        an exception — so the pipeline stays robust (mirrors the Windows source).
        """
        try:
            from AppKit import (  # type: ignore[import-not-found] # noqa: PLC0415
                NSWorkspace,
            )
            from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
                AXUIElementCreateApplication,
            )
        except (ImportError, ModuleNotFoundError):
            logger.warning("pyobjc not installed — AX tree empty")
            return ("", 0, [])

        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return ("", 0, [])
            pid = int(app.processIdentifier())
            window_title = str(app.localizedName() or "")
        except Exception:  # noqa: BLE001
            logger.debug("frontmost-application lookup failed", exc_info=True)
            return ("", 0, [])

        try:
            root = AXUIElementCreateApplication(pid)
        except Exception:  # noqa: BLE001
            logger.debug("AXUIElementCreateApplication failed", exc_info=True)
            return (window_title, pid, [])

        # The engine layer decides whether to discard a non-matching window —
        # hard-filtering here would be more brittle (mirrors the Windows source).
        _ = window_title_filter

        nodes: list[RawNode] = []
        try:
            _ax_flatten(root, depth=0, max_depth=max_depth, parent_index=-1, out=nodes)
        except Exception:  # noqa: BLE001
            logger.warning("AX traversal aborted", exc_info=True)

        return (window_title, pid, nodes)

    # ---- Pruning + serialization -------------------------------------------

    @staticmethod
    def _to_uia_nodes(raw: list[RawNode]) -> list[UIANode]:
        """Convert RawNode -> UIANode (parent_index already remapped by prune)."""
        return [
            UIANode(
                role=n.role,
                name=n.name,
                automation_id=n.automation_id,
                bounds=n.bounds,
                enabled=n.enabled,
                parent_index=n.parent_index,
                value=n.value,
            )
            for n in raw
        ]

    @staticmethod
    def _hash_tree(
        window_title: str,
        pid: int,
        nodes: tuple[UIANode, ...],
    ) -> str:
        """Stable hash over title + pid + tree structure (same scheme as UIA)."""
        h = hashlib.sha256()
        h.update(window_title.encode("utf-8", errors="replace"))
        h.update(pid.to_bytes(4, "little", signed=False))
        for n in nodes:
            h.update(n.role.encode("utf-8", errors="replace"))
            h.update(b"\x00")
            h.update(n.name.encode("utf-8", errors="replace"))
            h.update(b"\x00")
            h.update(n.automation_id.encode("utf-8", errors="replace"))
            h.update(b"\x00")
            for coord in n.bounds:
                h.update(int(coord).to_bytes(4, "little", signed=True))
        return h.hexdigest()


# ---------------------------------------------------------------------------
# Module-level AX flatten helper — operates on duck-typed AX element wrappers.
# ---------------------------------------------------------------------------

# AX attribute constants. Kept as plain string literals so the module imports
# clean without pyobjc (HN-7); the real ``kAX*Attribute`` constants are these
# exact strings.
_AX_ROLE = "AXRole"
_AX_TITLE = "AXTitle"
_AX_VALUE = "AXValue"
_AX_DESCRIPTION = "AXDescription"
_AX_IDENTIFIER = "AXIdentifier"
_AX_POSITION = "AXPosition"
_AX_SIZE = "AXSize"
_AX_ENABLED = "AXEnabled"
_AX_CHILDREN = "AXChildren"


def _ax_copy_attr(element: Any, attribute: str) -> Any:
    """Read one AX attribute, returning ``None`` on any failure.

    Two shapes are supported so the same flatten works against the real pyobjc
    API and against the test fake:

    * The real ``HIServices.AXUIElementCopyAttributeValue(element, attr, None)``
      returns ``(error_code, value)``; ``error_code == 0`` means success.
    * A fake/wrapper that exposes ``element.copy_attribute_value(attr)`` and
      returns the value directly.

    The pyobjc import is lazy (HN-7).
    """
    getter = getattr(element, "copy_attribute_value", None)
    if callable(getter):
        try:
            return getter(attribute)
        except Exception:  # noqa: BLE001
            return None
    try:
        from HIServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCopyAttributeValue,
        )
    except (ImportError, ModuleNotFoundError):
        return None
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
        if err != 0:
            return None
        return value
    except Exception:  # noqa: BLE001
        return None


def _ax_point(value: Any) -> tuple[int, int]:
    """Extract an (x, y) from an AX position value (CGPoint-like or dict)."""
    if value is None:
        return (0, 0)
    if isinstance(value, dict):
        return (int(value.get("x", 0)), int(value.get("y", 0)))
    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    if x is not None and y is not None:
        return (int(x), int(y))
    return (0, 0)


def _ax_size(value: Any) -> tuple[int, int]:
    """Extract a (w, h) from an AX size value (CGSize-like or dict)."""
    if value is None:
        return (0, 0)
    if isinstance(value, dict):
        return (int(value.get("w", value.get("width", 0))),
                int(value.get("h", value.get("height", 0))))
    w = getattr(value, "width", None)
    h = getattr(value, "height", None)
    if w is not None and h is not None:
        return (int(w), int(h))
    return (0, 0)


def _ax_flatten(
    element: Any,
    *,
    depth: int,
    max_depth: int,
    parent_index: int,
    out: list[RawNode],
) -> None:
    """Recursive depth-first flatten of an AX element into ``RawNode``.

    Roles are normalized to the canonical UIA vocabulary at flatten time
    (AD-10); a role that ``normalize_role`` drops (``None``) is still recorded
    as a structural node so the parent hierarchy survives, but with an empty
    role string — the role-whitelist prune then removes it (it is never the
    interesting-roots root, which prune always keeps).
    """
    if depth > max_depth:
        return
    try:
        native_role = str(_ax_copy_attr(element, _AX_ROLE) or "")
        canonical = normalize_role(native_role, "darwin")
        role = canonical or ""

        name = _ax_copy_attr(element, _AX_TITLE)
        if not name:
            name = _ax_copy_attr(element, _AX_VALUE)
        if not name:
            name = _ax_copy_attr(element, _AX_DESCRIPTION)
        name = str(name or "")

        automation_id = str(_ax_copy_attr(element, _AX_IDENTIFIER) or "")

        x, y = _ax_point(_ax_copy_attr(element, _AX_POSITION))
        w, h = _ax_size(_ax_copy_attr(element, _AX_SIZE))
        bounds = (x, y, max(0, w), max(0, h))

        enabled_raw = _ax_copy_attr(element, _AX_ENABLED)
        enabled = True if enabled_raw is None else bool(enabled_raw)

        # L3 value-read: AXValue is the current text of an editable control
        # (search box, text field). Read separately from the name-fallback above
        # so the loop can see what a field already holds.
        value = str(_ax_copy_attr(element, _AX_VALUE) or "")
    except Exception:  # noqa: BLE001
        return

    my_index = len(out)
    out.append(RawNode(
        role=role,
        name=name,
        automation_id=automation_id,
        bounds=bounds,
        enabled=enabled,
        is_offscreen=False,
        depth=depth,
        parent_index=parent_index,
        value=value,
    ))

    if depth >= max_depth:
        return

    children = _ax_copy_attr(element, _AX_CHILDREN)
    if not children:
        return
    try:
        child_list = list(children)
    except Exception:  # noqa: BLE001
        return
    for child in child_list:
        _ax_flatten(
            child,
            depth=depth + 1,
            max_depth=max_depth,
            parent_index=my_index,
            out=out,
        )


__all__ = ["AXTreeSource"]
