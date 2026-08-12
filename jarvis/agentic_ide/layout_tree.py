"""The workspace's split tree — where every pane sits and how much room it has.

The workspace used to be a flat "columns × slots" grid: every pane carried two
integers, a column was full-height by definition, and so "split right" could
only mean "open a new full-height column". Splitting the top pane of a stack
therefore restructured the WHOLE workspace — the new pane arrived beside
everything instead of beside the pane that was clicked (reported with a
drawing on 2026-08-12).

A split tree is the model that makes splits local, and it is the one every
tiling surface converges on (tmux, VS Code): a workspace is a nesting of
containers, a ``row`` lays its children side by side, a ``column`` stacks
them, and splitting a pane replaces its leaf with a two-child container. The
operation touches one branch and nothing else, so the pane that was clicked is
the only pane that changes size — in BOTH directions, at any depth.

This module is deliberately pure: nodes in, nodes out, no session, no I/O.
The session owns one tree per workspace and calls these functions; keeping
them free of session state is what makes the tricky part — the structural
edits — exhaustively testable on plain values.

## Invariants

Every function returns a tree in canonical form:

* A ``Split`` holds at least two children — a container with one child is
  spliced out and its child takes its place.
* A child never repeats its parent's direction — a row inside a row is merged
  into the parent (weights scaled so nothing moves on screen). Without this,
  repeated split/close cycles grow chains of degenerate containers that render
  identically but make every later edit harder to reason about.
* ``weights`` has exactly one positive finite entry per child. A weight is a
  plain multiplier against its siblings (two children at 1 and 1 are equal),
  never pixels — pixels do not survive a window resize, a weight does.

## Room accounting

* **A split halves its anchor.** The new pane takes half of the clicked
  pane's room and every other pane keeps exactly what it had. That promise
  already existed for the flat grid (`paneLayout.ts`, `weightsAfterSplit`)
  and the tree keeps it by construction.
* **A close gives the freed room to the siblings, proportionally.** The
  closed pane's weight is simply dropped; the remaining weights renormalise
  against each other, so a 2:1:1 stack losing its tail becomes 2:1.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

Direction = Literal["row", "column"]

#: Where ``move_pane`` may put a pane, relative to its drop target. "swap" keeps
#: the tree's shape and exchanges the two panes; the four sides carve the
#: TARGET's own rectangle, exactly as the split buttons would.
MOVE_POSITIONS = ("swap", "left", "right", "above", "below")


@dataclass(slots=True)
class Leaf:
    """One pane, referenced by its terminal KEY (``"t1"``), never its name.

    The key is the pane's stable identity for its whole life; the visible
    call-sign can be renamed without the tree noticing.
    """

    pane: str


@dataclass(slots=True)
class Split:
    """A container: children side by side (``row``) or stacked (``column``)."""

    direction: Direction
    children: list[LayoutNode] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)


LayoutNode = Leaf | Split


def _clean_weight(value: Any) -> float:
    """A usable sibling multiplier, or the default share for anything else."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        # A malformed persisted weight degrades to the default share by
        # design — the same quiet treatment the non-positive and infinite
        # numbers below get; the layout must render regardless.
        return 1.0
    if not (number > 0) or number == float("inf"):
        return 1.0
    return number


def normalize(node: LayoutNode) -> LayoutNode:
    """Return ``node`` in canonical form (see the module header).

    Every structural edit in this module funnels through here, so the
    invariants hold after ANY sequence of splits, closes and moves — not just
    the sequences someone thought to handle inline.
    """
    if isinstance(node, Leaf):
        return node

    children: list[LayoutNode] = []
    weights: list[float] = []
    raw_weights = list(node.weights)
    for index, child in enumerate(node.children):
        cleaned = normalize(child)
        weight = _clean_weight(raw_weights[index] if index < len(raw_weights) else 1.0)
        if isinstance(cleaned, Split) and cleaned.direction == node.direction:
            # A row inside a row: inline the grandchildren, scaling their
            # weights so together they still occupy exactly the room the
            # child had. Nothing moves on screen — only the bookkeeping
            # flattens.
            total = sum(cleaned.weights) or 1.0
            for grandchild, sub in zip(cleaned.children, cleaned.weights, strict=True):
                children.append(grandchild)
                weights.append(weight * sub / total)
        else:
            children.append(cleaned)
            weights.append(weight)

    if len(children) == 1:
        return children[0]
    return Split(direction=node.direction, children=children, weights=weights)


def leaves(node: LayoutNode | None) -> list[str]:
    """Every pane key in READING order — the order `_renumber` sorts by.

    Depth-first, children left to right / top to bottom. For the plain
    "columns of stacks" shape this is exactly the old ``(column, slot)`` sort,
    so call-sign numbering keeps meaning what it always meant.
    """
    if node is None:
        return []
    if isinstance(node, Leaf):
        return [node.pane]
    found: list[str] = []
    for child in node.children:
        found.extend(leaves(child))
    return found


def contains(node: LayoutNode | None, pane: str) -> bool:
    return pane in leaves(node)


def wizard_tree(panes: Iterable[str], depth: int) -> LayoutNode | None:
    """The tree a wizard-opened workspace starts with.

    Columns of ``depth``, each filled top to bottom before the next is opened
    — the same arithmetic the wizard preview draws with (frontend
    ``layout.ts``), so the workspace that appears is the one that was shown.
    """
    keys = list(panes)
    if not keys:
        return None
    step = max(1, int(depth))
    columns: list[LayoutNode] = []
    for start in range(0, len(keys), step):
        chunk = [Leaf(pane=key) for key in keys[start : start + step]]
        if len(chunk) == 1:
            columns.append(chunk[0])
        else:
            columns.append(
                Split(direction="column", children=list(chunk), weights=[1.0] * len(chunk))
            )
    if len(columns) == 1:
        return columns[0]
    return Split(direction="row", children=columns, weights=[1.0] * len(columns))


def from_grid(entries: Iterable[tuple[str, int, int]]) -> LayoutNode | None:
    """A tree for panes that only know their legacy ``(column, slot)`` place.

    This is the migration path: resume snapshots written before the tree
    existed carry the two integers per pane, and the columns-of-stacks shape
    they describe is exactly representable — a row of column stacks.
    """
    by_column: dict[int, list[tuple[int, str]]] = {}
    for key, column, slot in entries:
        by_column.setdefault(column, []).append((slot, key))
    if not by_column:
        return None
    columns: list[LayoutNode] = []
    for column in sorted(by_column):
        stack = [key for _, key in sorted(by_column[column])]
        if len(stack) == 1:
            columns.append(Leaf(pane=stack[0]))
        else:
            columns.append(
                Split(
                    direction="column",
                    children=[Leaf(pane=key) for key in stack],
                    weights=[1.0] * len(stack),
                )
            )
    if len(columns) == 1:
        return columns[0]
    return Split(direction="row", children=columns, weights=[1.0] * len(columns))


def _insert_beside(
    node: LayoutNode,
    anchor: str,
    added: str,
    direction: Direction,
    after: bool,
) -> tuple[LayoutNode, bool]:
    """Place ``added`` on one side of ``anchor``, halving the anchor's room.

    Returns the rewritten node and whether the anchor was found beneath it.
    The two cases are the whole trick of a split tree:

    * The anchor's parent already runs in ``direction`` — the new leaf joins
      that container right beside the anchor, taking half the anchor's weight.
    * It does not — the anchor's leaf alone is replaced by a two-child
      container. The anchor keeps its weight in the parent, so the pair
      together occupy exactly the room the anchor had.
    """
    if isinstance(node, Leaf):
        if node.pane != anchor:
            return node, False
        pair = [node, Leaf(pane=added)] if after else [Leaf(pane=added), node]
        return Split(direction=direction, children=pair, weights=[1.0, 1.0]), True

    for index, child in enumerate(node.children):
        if isinstance(child, Leaf) and child.pane == anchor:
            if node.direction == direction:
                share = _clean_weight(node.weights[index] if index < len(node.weights) else 1.0)
                node.weights[index] = share / 2
                at = index + 1 if after else index
                node.children.insert(at, Leaf(pane=added))
                node.weights.insert(at, share / 2)
            else:
                pair = [child, Leaf(pane=added)] if after else [Leaf(pane=added), child]
                node.children[index] = Split(direction=direction, children=pair, weights=[1.0, 1.0])
            return node, True
        if isinstance(child, Split):
            rewritten, found = _insert_beside(child, anchor, added, direction, after)
            if found:
                node.children[index] = rewritten
                return node, True
    return node, False


def split_pane(
    root: LayoutNode | None,
    anchor: str | None,
    added: str,
    direction: Literal["right", "down"],
) -> LayoutNode:
    """The tree after ``added`` was split off ``anchor``.

    ``"right"`` puts the new pane beside the anchor, ``"down"`` beneath it —
    and in both cases the pair shares the room the anchor had, because that is
    what splitting A pane means. Nothing outside the anchor's rectangle moves.

    Without an anchor (an empty tree, or a caller that named none) the pane
    joins the ROOT as a new full-height column on the far right — the shape
    "open one more terminal" has always produced, and the only honest reading
    of a request that named no pane to split.
    """
    grown: Direction = "row" if direction == "right" else "column"
    if root is None:
        return Leaf(pane=added)
    if anchor is not None:
        rewritten, found = _insert_beside(root, anchor, added, grown, after=True)
        if found:
            return normalize(rewritten)
    return append_pane(root, added)


def append_pane(root: LayoutNode | None, added: str) -> LayoutNode:
    """``added`` as a new full-height column at the workspace's right edge.

    The anchor-less add — the batch behind "open five more", the empty grid's
    button, a voice request that names nothing. The new column takes an even
    share: appending at the MEAN of the existing weights is what "an even
    share" means when the neighbours have been dragged to 2:1.
    """
    if root is None:
        return Leaf(pane=added)
    if isinstance(root, Split) and root.direction == "row":
        share = sum(root.weights) / len(root.weights) if root.weights else 1.0
        root.children.append(Leaf(pane=added))
        root.weights.append(_clean_weight(share))
        return normalize(root)
    return Split(direction="row", children=[root, Leaf(pane=added)], weights=[1.0, 1.0])


def remove_pane(root: LayoutNode | None, pane: str) -> LayoutNode | None:
    """The tree without ``pane``; ``None`` when it held nothing else.

    The freed room goes to the closed pane's SIBLINGS, proportionally — its
    weight is dropped and the rest renormalise. A container left with one
    child dissolves, which is how a workspace that was split apart folds back
    to simple shapes instead of accumulating scar tissue.
    """
    if root is None:
        return None
    if isinstance(root, Leaf):
        return None if root.pane == pane else root

    children: list[LayoutNode] = []
    weights: list[float] = []
    for index, child in enumerate(root.children):
        weight = _clean_weight(root.weights[index] if index < len(root.weights) else 1.0)
        if isinstance(child, Leaf) and child.pane == pane:
            continue
        if isinstance(child, Split):
            slimmed = remove_pane(child, pane)
            if slimmed is None:
                continue
            children.append(slimmed)
            weights.append(weight)
            continue
        children.append(child)
        weights.append(weight)

    if not children:
        return None
    if len(children) == 1:
        return normalize(children[0])
    return normalize(Split(direction=root.direction, children=children, weights=weights))


def swap_panes(root: LayoutNode | None, first: str, second: str) -> LayoutNode | None:
    """Exchange two panes' places; the tree's shape and every weight stay put."""
    if root is None:
        return None

    def walk(node: LayoutNode) -> LayoutNode:
        if isinstance(node, Leaf):
            if node.pane == first:
                return Leaf(pane=second)
            if node.pane == second:
                return Leaf(pane=first)
            return node
        node.children = [walk(child) for child in node.children]
        return node

    return walk(root)


def move_pane(
    root: LayoutNode | None,
    pane: str,
    target: str,
    position: str,
) -> LayoutNode | None:
    """The tree after ``pane`` was dropped on ``target``.

    ``"swap"`` exchanges the two and keeps the shape. The four sides carve the
    TARGET's own rectangle — dropping left of a pane means "take that pane's
    left half", exactly the local meaning the split buttons have. The moved
    pane's old room dissolves to its former siblings on the way out.
    """
    if root is None or pane == target:
        return root
    if position == "swap":
        return swap_panes(root, pane, target)

    direction: Direction = "row" if position in ("left", "right") else "column"
    after = position in ("right", "below")
    slimmed = remove_pane(root, pane)
    if slimmed is None:
        return Leaf(pane=pane) if contains(root, pane) else root
    rewritten, found = _insert_beside(slimmed, target, pane, direction, after)
    if not found:
        # The target vanished mid-flight (a concurrent close). Losing the
        # DROP is recoverable; losing the PANE is not — put it back at the
        # edge rather than dropping it from the tree.
        return append_pane(slimmed, pane)
    return normalize(rewritten)


# ------------------------------------------------------------- serialization


def to_dict(node: LayoutNode) -> dict[str, Any]:
    """The wire/persistence form: plain dicts, safe to JSON-encode."""
    if isinstance(node, Leaf):
        return {"pane": node.pane}
    return {
        "direction": node.direction,
        "children": [to_dict(child) for child in node.children],
        "weights": [_clean_weight(weight) for weight in node.weights],
    }


def from_dict(data: Any) -> LayoutNode:
    """Parse a stored tree, refusing anything malformed.

    Raises ``ValueError`` rather than guessing: a resume snapshot is a file on
    disk and files get truncated and hand-edited. The caller's fallback — the
    legacy ``(column, slot)`` migration — is honest; a half-parsed tree that
    silently drops panes is not.
    """
    if not isinstance(data, dict):
        raise ValueError("A layout node must be an object.")
    if "pane" in data:
        pane = data["pane"]
        if not isinstance(pane, str) or not pane:
            raise ValueError("A pane leaf needs a non-empty key.")
        return Leaf(pane=pane)
    direction = data.get("direction")
    if direction not in ("row", "column"):
        raise ValueError(f"Unknown layout direction: {direction!r}")
    children = data.get("children")
    if not isinstance(children, list) or len(children) < 2:
        raise ValueError("A split needs at least two children.")
    parsed = [from_dict(child) for child in children]
    raw_weights = data.get("weights")
    weights = [
        _clean_weight(raw_weights[index])
        if isinstance(raw_weights, list) and index < len(raw_weights)
        else 1.0
        for index in range(len(parsed))
    ]
    node = Split(direction=direction, children=parsed, weights=weights)
    seen: set[str] = set()
    for pane in leaves(node):
        if pane in seen:
            raise ValueError(f"Pane {pane!r} appears twice in the layout.")
        seen.add(pane)
    return normalize(node)


def same_shape(a: LayoutNode | None, b: LayoutNode | None) -> bool:
    """Same structure, same panes in the same places — weights ignored.

    This is the guard for adopting a client's dragged weights: a drag is only
    meaningful against the tree the client was LOOKING at, and a workspace
    reshaped in between (a voice-opened pane, a second client) makes the drag
    stale, not wrong enough to corrupt anything.
    """
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, Leaf) or isinstance(b, Leaf):
        return isinstance(a, Leaf) and isinstance(b, Leaf) and a.pane == b.pane
    if a.direction != b.direction or len(a.children) != len(b.children):
        return False
    return all(same_shape(x, y) for x, y in zip(a.children, b.children, strict=True))


def adopt_weights(current: LayoutNode, proposed: LayoutNode) -> LayoutNode:
    """``current``'s structure carrying ``proposed``'s weights.

    Callers check :func:`same_shape` first; this keeps the session's own tree
    authoritative for STRUCTURE while letting a client persist the one thing a
    seam drag changes.
    """
    if isinstance(current, Leaf) or isinstance(proposed, Leaf):
        return current
    return Split(
        direction=current.direction,
        children=[
            adopt_weights(mine, theirs)
            for mine, theirs in zip(current.children, proposed.children, strict=True)
        ],
        weights=[_clean_weight(weight) for weight in proposed.weights[: len(current.children)]]
        + [1.0] * max(0, len(current.children) - len(proposed.weights)),
    )


def grid_hints(root: LayoutNode | None) -> dict[str, tuple[int, int]]:
    """Coarse legacy ``(column, slot)`` for every pane, from the tree.

    Consumers that describe the workspace in words ("the top-left terminal")
    still think in columns, and for the common shapes the mapping is exact.
    For deeper nesting it is deliberately coarse: the column is the pane's
    top-level branch, the slot its reading position within that branch —
    stable, ordered, and never claiming precision the flat model cannot hold.
    """
    if root is None:
        return {}
    if isinstance(root, Split) and root.direction == "row":
        return {
            pane: (column, slot)
            for column, child in enumerate(root.children)
            for slot, pane in enumerate(leaves(child))
        }
    # A single stack (or a lone pane) is one column of many slots.
    return {pane: (0, slot) for slot, pane in enumerate(leaves(root))}
