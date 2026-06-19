"""Pruning-Helpers fuer den UIAutomation-Tree (ADR-0002).

Drei Filter-Stufen, die der `UIATreeSource` in Reihenfolge anwendet:

1. `filter_by_depth` — Depth-Limit (Default 6).
2. `filter_by_role` — Interesting-Role-Whitelist + Name-non-empty.
3. `filter_on_screen` — BoundingRect mindestens 50 % innerhalb Primary-Monitor.

Ziel: <= 150 Nodes. Bei Ueberschreitung iteriert `UIATreeSource` mit kleineren
Depths (6 -> 5 -> 4). Die Helpers selbst sind stateless und arbeiten auf einer
einfachen RawNode-Dataclass, damit sie ohne pywinauto testbar bleiben.

Designziel: reine Python-Funktionen, kein Windows-API-Call — Contract-Test
laeuft auf beliebigem OS.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# RawNode — intermediate Darstellung waehrend der Traversal.
# ---------------------------------------------------------------------------

@dataclass
class RawNode:
    """Zwischenmodell vor dem Pruning. Wird vor Konvertierung zu
    `jarvis.core.protocols.UIANode` verwendet.

    Felder entsprechen den Properties, die `UIAElementInfo` liefert — wir
    bilden bewusst nicht das gesamte Objekt ab, sondern nur, was fuer die
    Pruning-Filter und die spaetere Serialisierung relevant ist.
    """
    role: str = ""
    name: str = ""
    automation_id: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h
    enabled: bool = True
    is_offscreen: bool = False
    depth: int = 0
    parent_index: int = -1
    value: str = ""  # L3: current text of an editable control (address bar, ...)
    # Kinder-Indizes werden waehrend der Traversal im Tree-Builder verwaltet.
    children: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default-Konstanten (ADR-0002)
# ---------------------------------------------------------------------------

DEFAULT_MAX_DEPTH: int = 6
DEFAULT_MAX_NODES: int = 150
DEFAULT_INTERESTING_ROLES: tuple[str, ...] = (
    "Button",
    "Edit",
    "ComboBox",
    "List",
    "ListItem",
    "Tab",
    "MenuItem",
    "CheckBox",
    "RadioButton",
    "Hyperlink",
    "Text",
)


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def filter_by_depth(nodes: list[RawNode], *, max_depth: int) -> list[RawNode]:
    """Behaelt nur Nodes mit `depth <= max_depth`.

    Root hat depth=0. Ein max_depth von 6 erlaubt also 7 Ebenen inklusive
    Root — das entspricht der ADR-0002-Definition ("ab Root-Window").
    """
    return [n for n in nodes if n.depth <= max_depth]


def filter_by_role(
    nodes: list[RawNode],
    *,
    interesting_roles: tuple[str, ...] = DEFAULT_INTERESTING_ROLES,
) -> list[RawNode]:
    """Behaelt nur Nodes, deren Role in der Whitelist steht und die einen
    nicht-leeren Namen haben.

    Ausnahme: Nodes mit nicht-leerer AutomationId werden ebenfalls behalten,
    auch wenn der Name leer ist — die AutomationId ist oft der stabilere
    Anker fuer Clicks.
    """
    kept: list[RawNode] = []
    for node in nodes:
        if node.role not in interesting_roles:
            continue
        if not node.name and not node.automation_id:
            continue
        kept.append(node)
    return kept


def rect_overlap_fraction(
    bounds: tuple[int, int, int, int],
    monitor_bounds: tuple[int, int, int, int],
) -> float:
    """Anteil von `bounds` innerhalb `monitor_bounds` (0.0–1.0).

    Bounds-Format (x, y, w, h). Wenn das Node-Rect 0 Flaeche hat, return 0.0.
    """
    bx, by, bw, bh = bounds
    if bw <= 0 or bh <= 0:
        return 0.0
    mx, my, mw, mh = monitor_bounds
    # Schnittflaeche berechnen
    ix1 = max(bx, mx)
    iy1 = max(by, my)
    ix2 = min(bx + bw, mx + mw)
    iy2 = min(by + bh, my + mh)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    intersection = iw * ih
    area = bw * bh
    return intersection / area if area > 0 else 0.0


def filter_on_screen(
    nodes: list[RawNode],
    *,
    monitor_bounds: tuple[int, int, int, int],
    min_overlap: float = 0.5,
) -> list[RawNode]:
    """Behaelt nur Nodes, deren BoundingRect mindestens `min_overlap` (Default
    0.5) innerhalb des Primary-Monitors liegt UND die nicht als `IsOffscreen`
    markiert sind.

    `monitor_bounds` im selben Format wie `bounds`: (x, y, w, h).
    """
    kept: list[RawNode] = []
    for node in nodes:
        if node.is_offscreen:
            continue
        frac = rect_overlap_fraction(node.bounds, monitor_bounds)
        if frac >= min_overlap:
            kept.append(node)
    return kept


def _remap_parent_indices(
    kept: list[RawNode],
    original: list[RawNode],
) -> None:
    """H1-Fix: Nach Pruning muss ``parent_index`` auf den Index des
    naechsten erhaltenen Vorfahren **im gepruenten Subset** zeigen —
    nicht mehr auf den Index in der Original-Liste.

    Mutiert ``kept`` in-place. Wenn kein Vorfahre ueberlebt hat, wird
    ``parent_index = -1`` gesetzt (Root bzw. disconnected).
    """
    kept_to_new_idx: dict[int, int] = {id(n): i for i, n in enumerate(kept)}
    for n in kept:
        original_parent = n.parent_index
        n.parent_index = -1                          # Default falls kein Vorfahre
        while 0 <= original_parent < len(original):
            parent_node = original[original_parent]
            new_idx = kept_to_new_idx.get(id(parent_node))
            if new_idx is not None:
                n.parent_index = new_idx
                break
            # Vorfahre wurde wegge-prunt → eine Ebene hoch suchen.
            original_parent = parent_node.parent_index


def prune_tree(
    nodes: list[RawNode],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    interesting_roles: tuple[str, ...] = DEFAULT_INTERESTING_ROLES,
    monitor_bounds: tuple[int, int, int, int] = (0, 0, 1920, 1080),
    min_overlap: float = 0.5,
) -> list[RawNode]:
    """Wendet die drei Filter in kanonischer Reihenfolge an (depth -> role
    -> on-screen). Returned eine neue Liste, Eingabe bleibt unveraendert.

    Root bleibt immer erhalten (Index 0) — auch wenn er selbst nicht in die
    Interesting-Roles-Liste faellt. Andernfalls verlieren wir die parent-
    Hierarchie vollstaendig.

    H1-Fix: parent_index der verbleibenden Nodes wird auf den Subset-Index
    remapped (via ``_remap_parent_indices``).
    """
    if not nodes:
        return []
    # Filter 1: Depth
    after_depth = filter_by_depth(nodes, max_depth=max_depth)
    # Filter 2: Rolle — Root (depth=0) ist davon ausgenommen.
    root = after_depth[0] if after_depth else None
    after_role = [
        n for n in after_depth
        if n is root
        or (n.role in interesting_roles and (n.name or n.automation_id))
    ]
    # Filter 3: On-Screen — Root bleibt immer erhalten.
    after_screen = [
        n for n in after_role
        if n is root or (
            not n.is_offscreen
            and rect_overlap_fraction(n.bounds, monitor_bounds) >= min_overlap
        )
    ]
    # Kept-Liste sind Referenzen auf dieselben Objekte wie in ``nodes`` —
    # damit wir den Original-Vorfahrencheck via ``is``/``id()`` machen koennen.
    # Wir machen eine Shallow-Copy, damit der Caller die Original-Liste
    # behaelt (parent_index-Mutation wirkt sich auf beide aus; das ist
    # Pragma-Kompromiss — Caller sollte nicht nach prune_tree die Original
    # erneut nutzen).
    _remap_parent_indices(after_screen, nodes)
    return after_screen
