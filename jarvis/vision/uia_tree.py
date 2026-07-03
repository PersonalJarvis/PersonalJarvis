"""UIATreeSource — pruned UIA tree via pywinauto.

The flow:

1. Determine the active window (GetForegroundWindow + pywinauto wrapper).
2. Traverse the tree recursively up to `max_depth` and flatten it into a `RawNode` list.
3. Pruning pipeline (ADR-0002): Depth -> Role -> OnScreen.
4. If still > 150 nodes after the pipeline: retry with depth 5, then 4.
5. If still > 150: `source="screenshot_only"`, nodes empty.

Why the UIA tree is not an AsyncIterator: the contract (`observe()`) returns
exactly one Observation — streaming makes no sense at the tree level, because
the structure is atomic per frame.

Performance note: the problem isn't the few hundred interesting
nodes, it's the 5,000-8,000-node trees from Chrome/VSCode/Slack that we MUST
traverse before we can filter. That's why the depth limit is critical —
it bounds the traversal itself, not just the result.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal
from uuid import uuid4

from jarvis.core.protocols import CancelToken, Observation, UIANode

from .pruning import (
    DEFAULT_INTERESTING_ROLES,
    DEFAULT_MAX_NODES,
    RawNode,
    prune_tree,
)

logger = logging.getLogger(__name__)

_DEPTH_RETRY_LADDER: tuple[int, ...] = (6, 5, 4)


class UIATreeSource:
    """Reads the UIAutomation tree of the active window and prunes it."""

    name: str = "ui-tree"
    kind: Literal["screenshot", "ui_tree", "composite"] = "ui_tree"

    def __init__(
        self,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        interesting_roles: tuple[str, ...] = DEFAULT_INTERESTING_ROLES,
        min_on_screen_overlap: float = 0.5,
        monitor_bounds: tuple[int, int, int, int] | None = None,
        # Dependency injection for tests: a function that accepts a depth
        # parameter and returns a tuple (root_title, pid, list[RawNode]).
        traverser: Callable[..., tuple[str, int, list[RawNode]]] | None = None,
    ) -> None:
        self._max_nodes = max_nodes
        self._interesting_roles = interesting_roles
        self._min_overlap = min_on_screen_overlap
        self._monitor_bounds = monitor_bounds  # None = detect at runtime
        self._traverser = traverser
        self._closed = False

    # ---- Public API --------------------------------------------------------

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,
    ) -> Observation:
        if self._closed:
            raise RuntimeError("UIATreeSource is closed")
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        # On-screen filter scope: the WHOLE virtual desktop, never one
        # monitor — a window on a secondary monitor must keep its tree
        # (2026-07-02 incident). Falls back to the legacy primary-monitor
        # rect only when the union cannot be determined.
        monitor_bounds = self._monitor_bounds
        if monitor_bounds is None:
            from jarvis.platform import monitors as _monitors  # noqa: PLC0415

            monitor_bounds = await asyncio.to_thread(
                _monitors.virtual_desktop_bounds,
            )
        if monitor_bounds is None:
            monitor_bounds = await asyncio.to_thread(
                self._detect_primary_monitor_bounds
            )

        # Retry ladder: on node overflow we shorten the depth.
        nodes_before = 0
        depth_used = _DEPTH_RETRY_LADDER[0]
        pruned: list[RawNode] = []
        window_title = ""
        active_pid = 0
        for depth in _DEPTH_RETRY_LADDER:
            if cancel_token is not None and cancel_token.is_cancelled():
                raise RuntimeError(f"cancelled: {cancel_token.reason}")
            traverse_fn = self._traverser or self._traverse_via_pywinauto
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
            # No usable UIA tree — fall back. The caller decides
            # whether to take a screenshot instead.
            logger.warning(
                "UIA tree pruning overflow: %d nodes after depth %d retry ladder",
                len(pruned),
                depth_used,
            )
            nodes: tuple[UIANode, ...] = ()
            source: Literal["full", "screenshot_only", "ui_tree_only"] = "screenshot_only"
        else:
            nodes = tuple(self._to_uia_nodes(pruned))
            source = "ui_tree_only"

        # Hash over the serialized tree structure, so the cache can also
        # deduplicate on pure UIA trees.
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

    # ---- Traversal (pywinauto) ---------------------------------------------

    @staticmethod
    def _detect_primary_monitor_bounds() -> tuple[int, int, int, int]:
        """Detect the primary monitor bounds.

        Best effort: without the Windows API there's a FullHD default. The
        ScreenshotSource has already set DPI awareness — we don't need to
        do that here again.
        """
        import os  # noqa: PLC0415
        if os.name == "nt":
            try:
                import ctypes  # noqa: PLC0415

                user32 = ctypes.windll.user32
                w = user32.GetSystemMetrics(0)
                h = user32.GetSystemMetrics(1)
                return (0, 0, int(w), int(h))
            except Exception:  # noqa: BLE001
                # Fallback: FullHD. The only effect is that the OnScreen filter
                # works conservatively — that's fine.
                logger.debug("GetSystemMetrics not available", exc_info=True)
        return (0, 0, 1920, 1080)

    @staticmethod
    def _traverse_via_pywinauto(
        max_depth: int,
        window_title_filter: str | None,
    ) -> tuple[str, int, list[RawNode]]:
        """Flatten traversal from the foreground window down to max_depth.

        Returns (window_title, pid, nodes). On errors (no active window,
        UIA unavailable) an empty result is returned —
        never an exception, so the pipeline stays robust.
        """
        try:
            from pywinauto.uia_element_info import UIAElementInfo  # noqa: PLC0415
        except ImportError:
            logger.warning("pywinauto not installed — UIA tree empty")
            return ("", 0, [])

        try:
            # Find the foreground window. UIAElementInfo() with no arguments is
            # the desktop root — we need the foreground window.
            import ctypes  # noqa: PLC0415

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return ("", 0, [])
            root = UIAElementInfo(hwnd)
        except Exception:  # noqa: BLE001
            logger.debug("Foreground UIA lookup failed", exc_info=True)
            return ("", 0, [])

        # Title + PID from the root window.
        try:
            window_title = str(root.name or "")
        except Exception:  # noqa: BLE001
            window_title = ""
        try:
            active_pid = int(root.process_id or 0)
        except Exception:  # noqa: BLE001
            active_pid = 0

        # If a filter is set and doesn't match, we still return it — the
        # engine layer decides whether to discard the result. That's
        # more robust than filtering hard here.
        _ = window_title_filter

        nodes: list[RawNode] = []
        try:
            _flatten(root, depth=0, max_depth=max_depth, parent_index=-1, out=nodes)
        except Exception:  # noqa: BLE001
            logger.warning("UIA traversal aborted", exc_info=True)

        return (window_title, active_pid, nodes)

    # ---- Pruning + Serialization ------------------------------------------

    @staticmethod
    def _to_uia_nodes(raw: list[RawNode]) -> list[UIANode]:
        """Converts RawNode -> UIANode.

        ``parent_index`` has already been remapped to the subset index in
        ``prune_tree`` (via ``_remap_parent_indices``) — here we just
        copy it over.
        """
        return [
            UIANode(
                role=n.role,
                name=n.name,
                automation_id=n.automation_id,
                bounds=n.bounds,
                enabled=n.enabled,
                parent_index=n.parent_index,
                value=n.value,
                is_password=n.is_password,
                focused=n.focused,
            )
            for n in raw
        ]

    @staticmethod
    def _hash_tree(
        window_title: str,
        pid: int,
        nodes: tuple[UIANode, ...],
    ) -> str:
        """Stable hash over title + PID + tree structure.

        We hash (role, name, automation_id, bounds) per node — that's
        enough to recognize two trees as "identical" when the user
        hasn't clicked anything.
        """
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
# Module-level flatten helper — hangs off pywinauto UIAElementInfo
# ---------------------------------------------------------------------------

def _flatten(
    element: Any,
    *,
    depth: int,
    max_depth: int,
    parent_index: int,
    out: list[RawNode],
) -> None:
    """Recursive flatten traversal. Strictly obeys `max_depth`."""
    if depth > max_depth:
        return
    try:
        role = str(getattr(element, "control_type", "") or "")
        name = str(getattr(element, "name", "") or "")
        automation_id = str(getattr(element, "automation_id", "") or "")
        rect = getattr(element, "rectangle", None)
        if rect is not None:
            # pywinauto rect has .left/.top/.right/.bottom
            left = int(getattr(rect, "left", 0))
            top = int(getattr(rect, "top", 0))
            right = int(getattr(rect, "right", 0))
            bottom = int(getattr(rect, "bottom", 0))
            bounds = (left, top, max(0, right - left), max(0, bottom - top))
        else:
            bounds = (0, 0, 0, 0)
        enabled = bool(getattr(element, "enabled", True))
        offscreen = bool(getattr(element, "is_offscreen", False))
    except Exception:  # noqa: BLE001
        return

    # L3 value-read: the current text inside an editable control (address bar,
    # search box) via the UIA ValuePattern. Best-effort -- most controls have no
    # ValuePattern -> "" (graceful; a value-read failure never skips the node).
    value = ""
    try:
        iface = getattr(element, "iface_value", None)
        if iface is not None:
            value = str(getattr(iface, "CurrentValue", "") or "")
    except Exception:  # noqa: BLE001
        value = ""

    # Accessibility state (audit #5/#16/#1B), best-effort via the underlying UIA
    # automation element. CurrentIsPassword marks a secure edit (redact before
    # upload, never read its value); CurrentHasKeyboardFocus proves a
    # click_element actually focused the control. Any access failure (non-Windows,
    # COM error, missing attribute) leaves both False — the safe default.
    is_password = False
    focused = False
    try:
        raw_el = getattr(element, "element", None)
        if raw_el is not None:
            is_password = bool(getattr(raw_el, "CurrentIsPassword", False))
            focused = bool(getattr(raw_el, "CurrentHasKeyboardFocus", False))
    except Exception:  # noqa: BLE001 — state read is best-effort, never skips the node
        is_password = False
        focused = False

    my_index = len(out)
    out.append(RawNode(
        role=role,
        name=name,
        automation_id=automation_id,
        bounds=bounds,
        enabled=enabled,
        is_offscreen=offscreen,
        depth=depth,
        parent_index=parent_index,
        value=value,
        is_password=is_password,
        focused=focused,
    ))

    if depth >= max_depth:
        return

    # Iterate children. On exception (rare COM errors) skip the subtree.
    try:
        children = list(element.children())
    except Exception:  # noqa: BLE001
        return
    for child in children:
        _flatten(
            child,
            depth=depth + 1,
            max_depth=max_depth,
            parent_index=my_index,
            out=out,
        )


# ---------------------------------------------------------------------------
# Subtree stable hash — foundation for Phase C (Multi-Action)
# ---------------------------------------------------------------------------

def subtree_stable_hash(nodes: Sequence[UIANode]) -> str:
    """Stable structural hash over a UIA subtree.

    Hashes only the structurally relevant fields:
    ``role``, ``name``, ``automation_id``, and ``parent_index``. This makes
    the hash:

    - **stable** across cursor movement, focus/selection/hover toggles, and
      small bound shifts (``bounds`` and ``enabled`` are
      deliberately ignored).
    - **sensitive** to structural changes: an element added
      or removed, window title/role/name changed, automation ID
      changed, hierarchy restructured, order changed.

    The order of the nodes is part of the hierarchy and flows in implicitly
    via ``parent_index`` (which points to tuple indices) plus the
    position in the iteration — so reordering two nodes also changes the
    hash.

    Returns: 64-character hex SHA256.
    """
    h = hashlib.sha256()
    # Version marker, so future hash schema changes don't
    # silently collide — whoever changes the schema bumps the marker.
    h.update(b"uia-subtree-v1\x00")
    # Node count as a separator before the body, so "empty tree"
    # stays distinguishable from "tree with one empty node".
    h.update(len(nodes).to_bytes(4, "little", signed=False))
    for idx, n in enumerate(nodes):
        h.update(idx.to_bytes(4, "little", signed=False))
        h.update(b"\x1f")  # unit separator between index and body
        h.update(n.role.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update(n.name.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update(n.automation_id.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update(int(n.parent_index).to_bytes(4, "little", signed=True))
        h.update(b"\x1e")  # record separator between nodes
    return h.hexdigest()
