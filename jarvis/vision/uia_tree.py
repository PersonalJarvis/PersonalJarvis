"""UIATreeSource — gepruneder UIA-Tree via pywinauto.

Der Ablauf:

1. Aktives Fenster ermitteln (GetForegroundWindow + pywinauto-Wrapper).
2. Tree rekursiv bis `max_depth` traversieren und in `RawNode`-Liste flachlegen.
3. Pruning-Pipeline (ADR-0002): Depth -> Role -> OnScreen.
4. Wenn nach Pipeline > 150 Nodes: Retry mit Depth 5, dann 4.
5. Wenn immer noch > 150: `source="screenshot_only"`, Nodes leer.

Warum UIA-Tree kein AsyncIterator ist: Der Contract (`observe()`) gibt
genau eine Observation zurueck — Streaming ergibt auf Tree-Ebene keinen
Sinn, weil die Struktur pro Frame atomar ist.

Performance-Hinweis: Das Problem sind nicht die paar hundert interessanten
Nodes, sondern die 5.000-8.000-Node-Trees von Chrome/VSCode/Slack, durch
die wir traversieren MUESSEN, bevor wir filtern koennen. Deshalb ist die
Depth-Grenze kritisch — sie begrenzt die Traversal selbst, nicht nur das
Ergebnis.
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
    """Liest den UIAutomation-Tree des aktiven Fensters und prunet ihn."""

    name: str = "ui-tree"
    kind: Literal["screenshot", "ui_tree", "composite"] = "ui_tree"

    def __init__(
        self,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
        interesting_roles: tuple[str, ...] = DEFAULT_INTERESTING_ROLES,
        min_on_screen_overlap: float = 0.5,
        monitor_bounds: tuple[int, int, int, int] | None = None,
        # Dependency-Injection fuer Tests: eine Funktion, die Depth-Parameter
        # akzeptiert und einen Tuple (root_title, pid, list[RawNode]) liefert.
        traverser: Callable[..., tuple[str, int, list[RawNode]]] | None = None,
    ) -> None:
        self._max_nodes = max_nodes
        self._interesting_roles = interesting_roles
        self._min_overlap = min_on_screen_overlap
        self._monitor_bounds = monitor_bounds  # None = zur Laufzeit ermitteln
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
            raise RuntimeError("UIATreeSource ist geschlossen")
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        monitor_bounds = self._monitor_bounds or await asyncio.to_thread(
            self._detect_primary_monitor_bounds
        )

        # Retry-Ladder: bei Node-Overflow kuerzen wir die Depth.
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
            # Kein brauchbarer UIA-Tree — fallback. Der Aufrufer entscheidet,
            # ob er stattdessen einen Screenshot nimmt.
            logger.warning(
                "UIA-Tree Pruning Overflow: %d Nodes nach Depth %d Retry-Ladder",
                len(pruned),
                depth_used,
            )
            nodes: tuple[UIANode, ...] = ()
            source: Literal["full", "screenshot_only", "ui_tree_only"] = "screenshot_only"
        else:
            nodes = tuple(self._to_uia_nodes(pruned))
            source = "ui_tree_only"

        # Hash ueber die serialisierte Tree-Struktur, damit der Cache auch
        # auf reinen UIA-Trees deduplizieren kann.
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
        """Primary-Monitor-Bounds ermitteln.

        Best effort: ohne Windows-API gibt's einen FullHD-Default. Der
        ScreenshotSource hat DPI-Awareness bereits gesetzt — wir muessen
        das hier nicht mehr.
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
                # Fallback: FullHD. Der einzige Effekt ist, dass OnScreen-Filter
                # konservativ arbeitet — das ist ok.
                logger.debug("GetSystemMetrics nicht verfuegbar", exc_info=True)
        return (0, 0, 1920, 1080)

    @staticmethod
    def _traverse_via_pywinauto(
        max_depth: int,
        window_title_filter: str | None,
    ) -> tuple[str, int, list[RawNode]]:
        """Flatten-Traversal ab Foreground-Window bis max_depth.

        Liefert (window_title, pid, nodes). Bei Fehlern (kein aktives Fenster,
        UIA nicht verfuegbar) wird ein leeres Ergebnis zurueckgegeben —
        niemals eine Exception, damit die Pipeline robust bleibt.
        """
        try:
            from pywinauto.uia_element_info import UIAElementInfo  # noqa: PLC0415
        except ImportError:
            logger.warning("pywinauto nicht installiert — UIA-Tree leer")
            return ("", 0, [])

        try:
            # Foreground-Window finden. UIAElementInfo() ohne Argumente ist
            # das Desktop-Root — wir brauchen das Foreground-Fenster.
            import ctypes  # noqa: PLC0415

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return ("", 0, [])
            root = UIAElementInfo(hwnd)
        except Exception:  # noqa: BLE001
            logger.debug("Foreground-UIA-Lookup gescheitert", exc_info=True)
            return ("", 0, [])

        # Titel + PID vom Root-Window.
        try:
            window_title = str(root.name or "")
        except Exception:  # noqa: BLE001
            window_title = ""
        try:
            active_pid = int(root.process_id or 0)
        except Exception:  # noqa: BLE001
            active_pid = 0

        # Wenn Filter gesetzt und nicht passt, liefern wir trotzdem aus — der
        # Engine-Layer entscheidet, ob er das Ergebnis verwerfen will. Das
        # ist robuster als hart zu filtern.
        _ = window_title_filter

        nodes: list[RawNode] = []
        try:
            _flatten(root, depth=0, max_depth=max_depth, parent_index=-1, out=nodes)
        except Exception:  # noqa: BLE001
            logger.warning("UIA-Traversal abgebrochen", exc_info=True)

        return (window_title, active_pid, nodes)

    # ---- Pruning + Serialisierung ------------------------------------------

    @staticmethod
    def _to_uia_nodes(raw: list[RawNode]) -> list[UIANode]:
        """Konvertiert RawNode -> UIANode.

        ``parent_index`` wurde bereits in ``prune_tree`` (via
        ``_remap_parent_indices``) auf den Subset-Index remapped — hier nur
        noch kopieren.
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
        """Stabiler Hash ueber Titel + PID + Tree-Struktur.

        Wir hashen (role, name, automation_id, bounds) pro Node — das
        reicht, um zwei Trees als "identisch" zu erkennen, wenn User
        nichts angeklickt hat.
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
# Module-level Flatten-Helper — haengt an pywinauto UIAElementInfo
# ---------------------------------------------------------------------------

def _flatten(
    element: Any,
    *,
    depth: int,
    max_depth: int,
    parent_index: int,
    out: list[RawNode],
) -> None:
    """Rekursiver Flatten-Traverse. Haelt sich strikt an `max_depth`."""
    if depth > max_depth:
        return
    try:
        role = str(getattr(element, "control_type", "") or "")
        name = str(getattr(element, "name", "") or "")
        automation_id = str(getattr(element, "automation_id", "") or "")
        rect = getattr(element, "rectangle", None)
        if rect is not None:
            # pywinauto rect hat .left/.top/.right/.bottom
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

    # Children iterieren. Bei Exception (seltene COM-Fehler) Teilbaum skippen.
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
# Subtree-Stable-Hash — Foundation fuer Phase C (Multi-Action)
# ---------------------------------------------------------------------------

def subtree_stable_hash(nodes: Sequence[UIANode]) -> str:
    """Stabiler Strukturhash ueber einen UIA-Subtree.

    Hasht ausschliesslich die strukturell relevanten Felder:
    ``role``, ``name``, ``automation_id`` und ``parent_index``. Damit ist
    der Hash:

    - **stabil** bei Cursor-Bewegung, Focus-/Selection-/Hover-Toggle und
      kleinen Bound-Verschiebungen (``bounds`` und ``enabled`` werden
      bewusst ignoriert).
    - **sensitiv** bei strukturellen Aenderungen: Element hinzugefuegt
      oder entfernt, Window-Title/Role/Name geaendert, AutomationId
      geaendert, Hierarchie umgebaut, Reihenfolge veraendert.

    Reihenfolge der Nodes ist Teil der Hierarchie und fliesst implizit
    ueber ``parent_index`` (das auf Tuple-Indizes zeigt) plus die
    Position im Iteriervorgang ein — Reorder zweier Nodes aendert den
    Hash also.

    Returns: 64-Zeichen Hex-SHA256.
    """
    h = hashlib.sha256()
    # Versionsmarker, damit zukuenftige Hash-Schema-Aenderungen nicht
    # heimlich kollidieren — wer das Schema aendert, bumpt den Marker.
    h.update(b"uia-subtree-v1\x00")
    # Anzahl Nodes als Trennzeichen vor dem Body, damit "leerer Tree"
    # vom "Tree mit einem leeren Node" unterscheidbar bleibt.
    h.update(len(nodes).to_bytes(4, "little", signed=False))
    for idx, n in enumerate(nodes):
        h.update(idx.to_bytes(4, "little", signed=False))
        h.update(b"\x1f")  # Unit-Separator zwischen Index und Body
        h.update(n.role.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update(n.name.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update(n.automation_id.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update(int(n.parent_index).to_bytes(4, "little", signed=True))
        h.update(b"\x1e")  # Record-Separator zwischen Nodes
    return h.hexdigest()
