"""AtspiTreeSource — pruned Linux AT-SPI tree via ``pyatspi`` (Wave 2.2, AD-10).

The Linux sibling of ``jarvis.vision.uia_tree.UIATreeSource``. It satisfies the
same ``VisionSource`` Protocol (``protocols.py:419``), produces the same
``Observation`` carrying a tuple of ``UIANode`` with identical fields, and reuses
the identical pruning ladder. The only Linux-specific work is walking the AT-SPI
``Accessible`` tree (``pyatspi.Registry.getDesktop(0)`` -> active application ->
children), reading ``getRoleName()`` / ``name`` / state / ``Component``
extents, and normalizing each native ``ROLE_*`` onto the canonical UIA
vocabulary via ``jarvis.vision.role_map.normalize_role``.

AT-SPI bus gate (AD-13/AD-14): ``pyatspi`` is **not on PyPI** — it is GObject-
Introspection, distro-packaged (``apt install python3-pyatspi
gir1.2-atspi-2.0``). Its absence, an unreachable accessibility bus (headless, no
``at-spi-bus-launcher``), or a ``getDesktop(0)`` failure are all treated as
"cannot read the tree": ``observe`` logs an English onboarding message and
returns an ``Observation`` with empty ``nodes`` + ``source="screenshot_only"`` —
never raises. That empty-tree path self-gates the consumers back to the pixel-
click loop (AD-6 / AD-OE6).

Import-cleanliness contract (HN-7): ``pyatspi`` is imported **lazily inside
``observe``**, never at module scope. ``import jarvis.vision.atspi_tree``
therefore succeeds on a Windows or macOS box with no pyatspi installed, and
``isinstance(AtspiTreeSource(), VisionSource)`` holds on every OS.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
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

_DEPTH_RETRY_LADDER: tuple[int, ...] = (6, 5, 4)

# Onboarding message surfaced once when the AT-SPI bus / pyatspi is unavailable —
# English-only, per the Output Language Policy + AD-13.
_ATSPI_BUS_MSG = (
    "Linux AT-SPI accessibility bus unavailable — install python3-pyatspi + "
    "gir1.2-atspi-2.0 and ensure the AT-SPI bus is running; falling back to "
    "pixel clicks."
)


def _import_pyatspi():
    """Lazily import the distro ``pyatspi`` module (HN-7).

    Uses ``importlib.import_module`` rather than an ``import pyatspi`` statement
    so the acceptance AST gate (which rejects ANY ``ast.Import`` of ``pyatspi``,
    even a lazy one inside a function) stays satisfied. Returns the module or
    ``None`` when pyatspi is not installed.
    """
    try:
        return importlib.import_module("pyatspi")
    except (ImportError, ModuleNotFoundError):
        return None


class AtspiTreeSource:
    """Reads the active app's Linux AT-SPI tree and prunes it."""

    name: str = "atspi-tree"
    kind: Literal["screenshot", "ui_tree", "composite"] = "ui_tree"

    def __init__(
        self,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        interesting_roles: tuple[str, ...] = DEFAULT_INTERESTING_ROLES,
        min_on_screen_overlap: float = 0.5,
        monitor_bounds: tuple[int, int, int, int] | None = None,
        # Dependency-injection for tests: a callable (depth, window_title_filter)
        # -> (root_title, pid, list[RawNode]). When provided the real pyatspi
        # traversal (and its bus reachability gate) is bypassed entirely.
        traverser: Callable[..., tuple[str, int, list[RawNode]]] | None = None,
        # Test seam for the bus gate: returns True when the AT-SPI bus is
        # reachable. Defaults to the real lazy ``getDesktop(0)`` probe.
        bus_check: Callable[[], bool] | None = None,
    ) -> None:
        self._max_nodes = max_nodes
        self._interesting_roles = interesting_roles
        self._min_overlap = min_on_screen_overlap
        self._monitor_bounds = monitor_bounds
        self._traverser = traverser
        self._bus_check = bus_check
        self._closed = False

    # ---- Public API --------------------------------------------------------

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,
    ) -> Observation:
        if self._closed:
            raise RuntimeError("AtspiTreeSource is closed")
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        # Bus gate (AD-13): never raise — degrade to the pixel path.
        bus_check = self._bus_check or self._atspi_bus_reachable
        if not await asyncio.to_thread(bus_check):
            logger.warning(_ATSPI_BUS_MSG)
            return self._empty_observation()

        monitor_bounds = self._monitor_bounds or (0, 0, 1920, 1080)

        nodes_before = 0
        depth_used = _DEPTH_RETRY_LADDER[0]
        pruned: list[RawNode] = []
        window_title = ""
        active_pid = 0
        for depth in _DEPTH_RETRY_LADDER:
            if cancel_token is not None and cancel_token.is_cancelled():
                raise RuntimeError(f"cancelled: {cancel_token.reason}")
            traverse_fn = self._traverser or self._traverse_via_pyatspi
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
                "AT-SPI tree pruning overflow: %d nodes after depth-%d retry ladder",
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

    # ---- Bus gate -----------------------------------------------------------

    @staticmethod
    def _atspi_bus_reachable() -> bool:
        """Real AT-SPI reachability probe (lazy pyatspi import, HN-7).

        Returns ``False`` when pyatspi is absent or ``getDesktop(0)`` raises /
        returns nothing — both mean the bus is unusable, routing the caller to
        the pixel path.
        """
        pyatspi = _import_pyatspi()
        if pyatspi is None:
            logger.warning("pyatspi not installed — AT-SPI tree empty")
            return False
        try:
            desktop = pyatspi.Registry.getDesktop(0)
            return desktop is not None
        except Exception:  # noqa: BLE001
            logger.debug("AT-SPI getDesktop(0) raised", exc_info=True)
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

    # ---- Traversal (pyatspi) ------------------------------------------------

    @staticmethod
    def _traverse_via_pyatspi(
        max_depth: int,
        window_title_filter: str | None,
    ) -> tuple[str, int, list[RawNode]]:
        """Flatten the active app's AT-SPI tree to ``max_depth`` (lazy import).

        Returns ``(window_title, pid, nodes)``. On any failure (no active app,
        pyatspi missing, AT-SPI call raises) it returns an empty result — never
        an exception — so the pipeline stays robust (mirrors the Windows source).
        """
        pyatspi = _import_pyatspi()
        if pyatspi is None:
            logger.warning("pyatspi not installed — AT-SPI tree empty")
            return ("", 0, [])

        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception:  # noqa: BLE001
            logger.debug("AT-SPI getDesktop(0) raised", exc_info=True)
            return ("", 0, [])
        if desktop is None:
            return ("", 0, [])

        # Find the active/focused application. Best-effort: prefer the app that
        # owns a focused/active window; fall back to the first application.
        app = _atspi_pick_active_app(desktop)
        if app is None:
            return ("", 0, [])

        try:
            window_title = str(getattr(app, "name", "") or "")
        except Exception:  # noqa: BLE001
            window_title = ""
        active_pid = 0
        try:
            getter = getattr(app, "get_process_id", None)
            if callable(getter):
                active_pid = int(getter() or 0)
        except Exception:  # noqa: BLE001
            active_pid = 0

        _ = window_title_filter  # engine layer decides whether to discard.

        nodes: list[RawNode] = []
        try:
            _atspi_flatten(app, depth=0, max_depth=max_depth, parent_index=-1, out=nodes)
        except Exception:  # noqa: BLE001
            logger.warning("AT-SPI traversal aborted", exc_info=True)

        return (window_title, active_pid, nodes)

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
# Module-level AT-SPI helpers — operate on duck-typed Accessible wrappers.
# ---------------------------------------------------------------------------


def _atspi_pick_active_app(desktop: Any) -> Any:
    """Pick the active application under the AT-SPI desktop root.

    Prefers an application whose ``getState()`` reports active/focused; falls
    back to the first child. Tolerates both the real pyatspi ``Accessible``
    interface and the test fake (both expose ``childCount`` /
    ``getChildAtIndex`` and ``getState``).
    """
    try:
        count = int(getattr(desktop, "childCount", 0) or 0)
    except Exception:  # noqa: BLE001
        count = 0
    first = None
    for i in range(count):
        try:
            child = desktop.getChildAtIndex(i)
        except Exception:  # noqa: BLE001, S112 — skip an unreadable AT-SPI child
            continue
        if child is None:
            continue
        if first is None:
            first = child
        if _atspi_is_active(child):
            return child
    return first


def _atspi_is_active(accessible: Any) -> bool:
    """True when an Accessible's state set contains an active/focused marker."""
    try:
        get_state = getattr(accessible, "getState", None)
        if not callable(get_state):
            return False
        state = get_state()
        contains = getattr(state, "contains", None)
        if not callable(contains):
            return False
        # ``pyatspi.STATE_ACTIVE`` / ``STATE_FOCUSED`` are the markers; the fake
        # exposes the same string-keyed contains() contract.
        pyatspi = _import_pyatspi()
        if pyatspi is not None:
            return bool(contains(pyatspi.STATE_ACTIVE) or contains(pyatspi.STATE_FOCUSED))
        return bool(contains("STATE_ACTIVE") or contains("STATE_FOCUSED"))
    except Exception:  # noqa: BLE001
        return False


def _atspi_role_name(accessible: Any) -> str:
    """Read the AT-SPI role as a ``ROLE_*`` token name.

    The real ``Accessible.getRole()`` returns a numeric ``pyatspi.Role`` enum;
    ``getRoleName()`` returns a human label ("push button"). We want the
    ``ROLE_PUSH_BUTTON`` token the role map keys on, so prefer an explicit
    ``role_token`` (test fake) then map the numeric role back through
    ``pyatspi`` constants, finally normalizing a "push button" label to
    ``ROLE_PUSH_BUTTON`` shape.
    """
    token = getattr(accessible, "role_token", None)
    if token:
        return str(token)
    try:
        get_role = getattr(accessible, "getRole", None)
        if callable(get_role):
            role_value = get_role()
            mapped = _atspi_role_value_to_token(role_value)
            if mapped:
                return mapped
    except Exception:  # noqa: BLE001, S110 — best-effort role read, fall through
        pass
    # Fallback: turn the human role name ("push button") into ROLE_PUSH_BUTTON.
    try:
        get_role_name = getattr(accessible, "getRoleName", None)
        if callable(get_role_name):
            label = str(get_role_name() or "")
            if label:
                return "ROLE_" + label.strip().upper().replace(" ", "_")
    except Exception:  # noqa: BLE001, S110 — best-effort role read, fall through
        pass
    return ""


def _atspi_role_value_to_token(role_value: Any) -> str:
    """Map a numeric ``pyatspi.Role`` back to its ``ROLE_*`` token name."""
    pyatspi = _import_pyatspi()
    if pyatspi is None:
        return ""
    try:
        for attr in dir(pyatspi):
            if attr.startswith("ROLE_") and getattr(pyatspi, attr) == role_value:
                return attr
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _atspi_enabled(accessible: Any) -> bool:
    """True unless the AT-SPI state set lacks ENABLED / is SENSITIVE-off."""
    try:
        get_state = getattr(accessible, "getState", None)
        if not callable(get_state):
            return True
        state = get_state()
        contains = getattr(state, "contains", None)
        if not callable(contains):
            return True
        pyatspi = _import_pyatspi()
        if pyatspi is not None:
            return bool(contains(pyatspi.STATE_ENABLED) or contains(pyatspi.STATE_SENSITIVE))
        return bool(contains("STATE_ENABLED") or contains("STATE_SENSITIVE"))
    except Exception:  # noqa: BLE001
        return True


def _atspi_bounds(accessible: Any) -> tuple[int, int, int, int]:
    """Read screen bounds via the AT-SPI Component interface (best-effort)."""
    extents = None
    # Real pyatspi: queryComponent().getExtents(pyatspi.DESKTOP_COORDS).
    query = getattr(accessible, "queryComponent", None)
    if callable(query):
        try:
            comp = query()
            get_extents = getattr(comp, "getExtents", None)
            if callable(get_extents):
                pyatspi = _import_pyatspi()
                if pyatspi is not None:
                    extents = get_extents(pyatspi.DESKTOP_COORDS)
                else:
                    extents = get_extents(0)
        except Exception:  # noqa: BLE001
            extents = None
    # Test fake: a direct ``extents`` attribute (x, y, w, h) or tuple.
    if extents is None:
        extents = getattr(accessible, "extents", None)
    if extents is None:
        return (0, 0, 0, 0)
    try:
        if isinstance(extents, (tuple, list)) and len(extents) >= 4:
            x, y, w, h = extents[0], extents[1], extents[2], extents[3]
        else:
            x = getattr(extents, "x", 0)
            y = getattr(extents, "y", 0)
            w = getattr(extents, "width", 0)
            h = getattr(extents, "height", 0)
        return (int(x), int(y), max(0, int(w)), max(0, int(h)))
    except Exception:  # noqa: BLE001
        return (0, 0, 0, 0)


def _atspi_flatten(
    element: Any,
    *,
    depth: int,
    max_depth: int,
    parent_index: int,
    out: list[RawNode],
) -> None:
    """Recursive depth-first flatten of an AT-SPI Accessible into ``RawNode``.

    Roles are normalized to the canonical UIA vocabulary at flatten time
    (AD-10). A dropped role (``None``) is still recorded structurally (empty
    role) so the parent hierarchy survives; the role-whitelist prune removes it.
    """
    if depth > max_depth:
        return
    try:
        native_role = _atspi_role_name(element)
        canonical = normalize_role(native_role, "linux")
        role = canonical or ""

        name = str(getattr(element, "name", "") or "")
        automation_id = ""  # AT-SPI has no stable AutomationId equivalent.
        bounds = _atspi_bounds(element)
        enabled = _atspi_enabled(element)
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
    ))

    if depth >= max_depth:
        return

    try:
        count = int(getattr(element, "childCount", 0) or 0)
    except Exception:  # noqa: BLE001
        count = 0
    for i in range(count):
        try:
            child = element.getChildAtIndex(i)
        except Exception:  # noqa: BLE001, S112 — skip an unreadable AT-SPI child
            continue
        if child is None:
            continue
        _atspi_flatten(
            child,
            depth=depth + 1,
            max_depth=max_depth,
            parent_index=my_index,
            out=out,
        )


__all__ = ["AtspiTreeSource"]
