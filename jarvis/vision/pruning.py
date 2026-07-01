"""Pruning helpers for the UIAutomation tree (ADR-0002).

Three filter stages that `UIATreeSource` applies in order:

1. `filter_by_depth` — depth limit (default 6).
2. `filter_by_role` — interesting-role whitelist + non-empty name.
3. `filter_on_screen` — bounding rect at least 50% inside the primary monitor.

Goal: <= 150 nodes. On overflow, `UIATreeSource` iterates with smaller
depths (6 -> 5 -> 4). The helpers themselves are stateless and operate on a
simple RawNode dataclass, so they stay testable without pywinauto.

Design goal: pure Python functions, no Windows API calls — the contract
test runs on any OS.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# RawNode — intermediate representation during the traversal.
# ---------------------------------------------------------------------------

@dataclass
class RawNode:
    """Intermediate model before pruning. Used before conversion to
    `jarvis.core.protocols.UIANode`.

    Fields correspond to the properties `UIAElementInfo` provides — we
    deliberately do not mirror the whole object, only what's relevant for
    the pruning filters and the later serialization.
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
    # Accessibility state (audit #5/#16/#1B). is_password: a secure/password edit
    # field (redact before screenshot upload, never read its value). focused: the
    # control currently holds keyboard focus (a click_element that gives a field
    # focus is verifiable post-hoc). Best-effort per OS; default False.
    is_password: bool = False
    focused: bool = False
    # Child indices are managed by the tree builder during the traversal.
    children: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default constants (ADR-0002)
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
# Filters
# ---------------------------------------------------------------------------

def filter_by_depth(nodes: list[RawNode], *, max_depth: int) -> list[RawNode]:
    """Keeps only nodes with `depth <= max_depth`.

    Root has depth=0. A max_depth of 6 thus allows 7 levels including
    root — that matches the ADR-0002 definition ("starting from the root
    window").
    """
    return [n for n in nodes if n.depth <= max_depth]


def filter_by_role(
    nodes: list[RawNode],
    *,
    interesting_roles: tuple[str, ...] = DEFAULT_INTERESTING_ROLES,
) -> list[RawNode]:
    """Keeps only nodes whose role is on the whitelist and that have a
    non-empty name.

    Exception: nodes with a non-empty AutomationId are also kept even if
    the name is empty — the AutomationId is often the more stable anchor
    for clicks.
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
    """Fraction of `bounds` inside `monitor_bounds` (0.0–1.0).

    Bounds format (x, y, w, h). If the node rect has 0 area, returns 0.0.
    """
    bx, by, bw, bh = bounds
    if bw <= 0 or bh <= 0:
        return 0.0
    mx, my, mw, mh = monitor_bounds
    # Compute the intersection area
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
    """Keeps only nodes whose bounding rect lies at least `min_overlap`
    (default 0.5) inside the primary monitor AND that are not marked as
    `IsOffscreen`.

    `monitor_bounds` uses the same format as `bounds`: (x, y, w, h).
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
    """H1 fix: after pruning, ``parent_index`` must point to the index of the
    nearest surviving ancestor **in the pruned subset** — no longer to the
    index in the original list.

    Mutates ``kept`` in place. If no ancestor survived, ``parent_index = -1``
    is set (root or disconnected).
    """
    kept_to_new_idx: dict[int, int] = {id(n): i for i, n in enumerate(kept)}
    for n in kept:
        original_parent = n.parent_index
        n.parent_index = -1                          # Default if no ancestor survives
        while 0 <= original_parent < len(original):
            parent_node = original[original_parent]
            new_idx = kept_to_new_idx.get(id(parent_node))
            if new_idx is not None:
                n.parent_index = new_idx
                break
            # Ancestor was pruned away → search one level up.
            original_parent = parent_node.parent_index


def prune_tree(
    nodes: list[RawNode],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    interesting_roles: tuple[str, ...] = DEFAULT_INTERESTING_ROLES,
    monitor_bounds: tuple[int, int, int, int] = (0, 0, 1920, 1080),
    min_overlap: float = 0.5,
) -> list[RawNode]:
    """Applies the three filters in canonical order (depth -> role ->
    on-screen). Returns a new list; the input is left unchanged.

    Root is always kept (index 0) — even if it doesn't itself fall into the
    interesting-roles list. Otherwise we'd lose the parent hierarchy
    entirely.

    H1 fix: the remaining nodes' parent_index is remapped to the subset
    index (via ``_remap_parent_indices``).
    """
    if not nodes:
        return []
    # Filter 1: depth
    after_depth = filter_by_depth(nodes, max_depth=max_depth)
    # Filter 2: role — root (depth=0) is exempt from this.
    root = after_depth[0] if after_depth else None
    after_role = [
        n for n in after_depth
        if n is root
        or (n.role in interesting_roles and (n.name or n.automation_id))
    ]
    # Filter 3: on-screen — root is always kept.
    after_screen = [
        n for n in after_role
        if n is root or (
            not n.is_offscreen
            and rect_overlap_fraction(n.bounds, monitor_bounds) >= min_overlap
        )
    ]
    # The kept list holds references to the same objects as in ``nodes`` —
    # so we can do the original-ancestor check via ``is``/``id()``.
    # We make a shallow copy so the caller keeps the original list
    # (parent_index mutation affects both; that's a pragmatic compromise —
    # the caller shouldn't reuse the original list after prune_tree).
    _remap_parent_indices(after_screen, nodes)
    return after_screen
