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
* A grid stands on its ROWS. After every structural edit, a row of equally
  divided columns is transposed into a column of rows (``rows_outermost``) —
  the same picture, but each band owns its own vertical seam instead of one
  full-height seam owning them all. See that function for why one of the two
  readings has to win.

## Room accounting

* **A split halves its anchor.** The new pane takes half of the clicked
  pane's room and every other pane keeps exactly what it had. That promise
  already existed for the flat grid (`paneLayout.ts`, `weightsAfterSplit`)
  and the tree keeps it by construction.
* **A close gives the freed room to the siblings, proportionally.** The
  closed pane's weight is simply dropped; the remaining weights renormalise
  against each other, so a 2:1:1 stack losing its tail becomes 2:1.
* **The session evens the wall after every open — except by hand-sized
  boundaries.** The halving above decides the SHAPE; the weights it leaves
  are then replaced by ``evened`` — every terminal at the same share, what
  the grid's "even out" button does — because a new terminal carved from a
  pane that had been dragged small arrived as a sliver nobody asked for. A
  container whose boundaries the user dragged (``Split.pinned``, decided
  from the weights at save time) keeps them: a size chosen on purpose is
  not undone by the next open (maintainer request, 2026-08-22).
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
    #: The user dragged THIS container's boundaries by hand (its weights were
    #: uneven the last time a client saved sizes). A pinned container keeps
    #: its weights through the evening that follows every open; every other
    #: container is dealt equal shares again. Cleared the moment the user
    #: evens it back out — by hand or with the grid's button — so "manual"
    #: is exactly "still uneven", never a sticky mode.
    pinned: bool = False


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
    pinned = node.pinned
    raw_weights = list(node.weights)
    for index, child in enumerate(node.children):
        cleaned = normalize(child)
        weight = _clean_weight(raw_weights[index] if index < len(raw_weights) else 1.0)
        if isinstance(cleaned, Split) and cleaned.direction == node.direction:
            # A row inside a row: inline the grandchildren, scaling their
            # weights so together they still occupy exactly the room the
            # child had. Nothing moves on screen — only the bookkeeping
            # flattens. Hand-sized boundaries stay hand-sized: the merged
            # container is pinned if either of the two was.
            pinned = pinned or cleaned.pinned
            total = sum(cleaned.weights) or 1.0
            for grandchild, sub in zip(cleaned.children, cleaned.weights, strict=True):
                children.append(grandchild)
                weights.append(weight * sub / total)
        else:
            children.append(cleaned)
            weights.append(weight)

    if len(children) == 1:
        return children[0]
    return Split(direction=node.direction, children=children, weights=weights, pinned=pinned)


def leaves(node: LayoutNode | None) -> list[str]:
    """Every pane key in READING order — the order `_renumber` sorts by.

    Depth-first, children left to right / top to bottom. Because
    :func:`rows_outermost` stands a grid on its rows, this is the order a
    person actually reads a workspace in — across the top band first, then
    the next — rather than down one column and back up the next.
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

    The SHAPE is columns of ``depth`` — the same arithmetic the wizard preview
    draws with (frontend ``layout.ts``), so the workspace that appears is the
    one that was shown.

    The panes are then seated into that shape in READING order, which is the
    other half of the promise and the half that used to be free. Once a full
    rectangle stands on its rows (:func:`rows_outermost`) it is read across
    the top band first, so filling the shape column by column would put the
    second pane under the first and the third beside it. Callers hand their
    panes over in the order the user sees them — ``Registry.refold`` re-deals
    a workspace someone is watching — and a fold that quietly permutes them
    is a fold that cannot be undone by widening the window again.
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
        return _reseat(columns[0], keys)
    shape = _rows_outermost(Split(direction="row", children=columns, weights=[1.0] * len(columns)))
    return _reseat(shape, keys)


def _reseat(node: LayoutNode, keys: Iterable[str]) -> LayoutNode:
    """``node``'s shape with its leaves refilled from ``keys``, in order.

    The shape decides where the cells are; this decides which pane sits in
    which. Keeping the two apart is what lets a caller pick a layout by its
    geometry and still hand its panes over in the order they should be read.
    """
    supply = iter(keys)

    def walk(current: LayoutNode) -> LayoutNode:
        if isinstance(current, Leaf):
            return Leaf(pane=next(supply))
        return Split(
            direction=current.direction,
            children=[walk(child) for child in current.children],
            weights=list(current.weights),
            pinned=current.pinned,
        )

    return walk(node)


def from_grid(entries: Iterable[tuple[str, int, int]]) -> LayoutNode | None:
    """A tree for panes that only know their legacy ``(column, slot)`` place.

    This is the migration path: resume snapshots written before the tree
    existed carry the two integers per pane, and the columns-of-stacks shape
    they describe is exactly representable — a row of column stacks, squared
    up by :func:`rows_outermost` when it forms a full rectangle.
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
    return _rows_outermost(Split(direction="row", children=columns, weights=[1.0] * len(columns)))


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
            return _rows_outermost(normalize(rewritten))
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
        return _rows_outermost(normalize(root))
    return Split(direction="row", children=[root, Leaf(pane=added)], weights=[1.0, 1.0])


def axis_span(node: LayoutNode, direction: Direction) -> int:
    """How many TERMINALS wide (``row``) or tall (``column``) ``node`` is.

    The yardstick "even" is measured with. A container that runs in
    ``direction`` is as many stripes as its children together; one running the
    other way is only as wide as its widest member. Without this a workspace
    like ``row[pane, stack-of-two, pane]`` would "even" to quarters and the
    stacked terminals come out half as wide as their neighbours — the same
    arithmetic the frontend's ``axisSpan`` (``treeLayout.ts``) uses, and the
    two must agree or the grid's "already even" button state lies.
    """
    if isinstance(node, Leaf):
        return 1
    spans = [axis_span(child, direction) for child in node.children]
    if not spans:
        return 1
    return sum(spans) if node.direction == direction else max(spans)


#: How far apart two sibling shares may be and still count as the same size.
#: Relative, because weights are multipliers: 7 and 7.0000001 are the same
#: pane on any screen. The frontend's ``EVEN_EPSILON`` (``treeLayout.ts``).
EVEN_EPSILON = 1e-4


def is_even(node: LayoutNode | None) -> bool:
    """Is every TERMINAL under ``node`` at the share ``evened`` would give it?

    Judged per stripe, the way ``evened`` deals weights out (``axis_span``),
    so equal container weights over a nested group do NOT read as even while
    one terminal draws twice the size of another.
    """
    if node is None or isinstance(node, Leaf):
        return True
    return _even_weights(node) and all(is_even(child) for child in node.children)


def _even_weights(node: Split) -> bool:
    """``node``'s own boundaries only — children not inspected."""
    shares = [
        _clean_weight(node.weights[index] if index < len(node.weights) else 1.0)
        / axis_span(child, node.direction)
        for index, child in enumerate(node.children)
    ]
    if not shares:
        return True
    first = shares[0]
    return all(abs(share - first) <= EVEN_EPSILON * first for share in shares)


def _shares(node: Split) -> list[float]:
    """``node``'s weights as fractions of its own total.

    Comparable ACROSS containers, which raw weights are not: two columns at
    ``[1, 1]`` and ``[4, 4]`` divide their room at exactly the same height.
    """
    weights = [
        _clean_weight(node.weights[index] if index < len(node.weights) else 1.0)
        for index in range(len(node.children))
    ]
    total = sum(weights) or 1.0
    return [weight / total for weight in weights]


def _is_one_grid(node: Split) -> bool:
    """Do ``node``'s children line up as ONE grid — same rows, same heights?

    True only when every child is a column, they all hold the same number of
    children, and they divide their height at the same fractions. That is
    exactly the condition under which :func:`rows_outermost` may transpose
    ``node`` without a single pane changing size.
    """
    if node.direction != "row" or len(node.children) < 2:
        return False
    columns: list[Split] = []
    for child in node.children:
        if not isinstance(child, Split) or child.direction != "column":
            return False
        columns.append(child)
    depth = len(columns[0].children)
    if depth < 2 or any(len(column.children) != depth for column in columns):
        return False
    first = _shares(columns[0])
    return all(
        abs(share - first[index]) <= EVEN_EPSILON * first[index]
        for column in columns[1:]
        for index, share in enumerate(_shares(column))
    )


def rows_outermost(root: LayoutNode | None) -> LayoutNode | None:
    """A grid rewritten as rows of panes rather than columns of panes.

    Geometry in, the SAME geometry out — this moves no boundary and resizes
    no pane. What it changes is which container owns each boundary, and that
    decides how far a seam drag reaches.

    A workspace built by splitting right and then down twice comes out as
    ``row[column[a, c], column[b, d]]``: two columns side by side, so the
    line between them belongs to the ROOT and runs the full height. Dragging
    it under the bottom pair therefore resizes the top pair too — the two
    halves are welded together, which is what the maintainer reported on
    2026-08-25 ("the upper and the lower terminal move with each other").
    Reaching the same 2×2 the other way round — split down, then right twice
    — gives ``column[row[a, b], row[c, d]]``, where each row owns its own
    vertical line and the two rows resize independently. Same picture, two
    behaviours, decided by the order the panes happened to be opened in.

    So a grid is put in ONE of those forms, always the second: side-by-side
    boundaries are the ones people size (a terminal needs width for its
    output far more often than a column needs its own height), and the cost
    is the single horizontal line now spanning the full width. Rows first is
    the maintainer's call, not a fact about split trees — both readings are
    legitimate, only one can be true at a time.

    The transpose is refused unless every column divides its height at the
    same fractions (:func:`_is_one_grid`): otherwise the horizontal lines sit
    at different heights and no single row structure can draw them. That is
    also what keeps this from undoing hand-dragged sizes — a column dragged
    out of line with its neighbours simply stops qualifying.

    Applied bottom-up, and only after STRUCTURAL edits (a split, a close, a
    move). A seam drag never comes through here: the client posts back the
    tree it was looking at, and reshaping under a drag would make the
    workspace jump under the pointer.
    """
    return None if root is None else _rows_outermost(root)


def _rows_outermost(root: LayoutNode) -> LayoutNode:
    if isinstance(root, Leaf):
        return root

    node = Split(
        direction=root.direction,
        children=[_rows_outermost(child) for child in root.children],
        weights=list(root.weights),
        pinned=root.pinned,
    )
    if not _is_one_grid(node):
        return normalize(node)

    columns = [child for child in node.children if isinstance(child, Split)]
    # The columns' shared height fractions become the outer stack's weights;
    # the row's own weights become every new row's, so each band is cut at the
    # verticals the grid already had.
    heights = _shares(columns[0])
    widths = [
        _clean_weight(node.weights[index] if index < len(node.weights) else 1.0)
        for index in range(len(columns))
    ]
    bands = [
        Split(
            direction="row",
            children=[column.children[band] for column in columns],
            weights=list(widths),
            pinned=node.pinned,
        )
        for band in range(len(columns[0].children))
    ]
    return normalize(
        Split(
            direction="column",
            children=list(bands),
            weights=list(heights),
            pinned=any(column.pinned for column in columns),
        )
    )


def evened(root: LayoutNode | None) -> LayoutNode | None:
    """Every TERMINAL back at an equal share — except where a hand set them.

    The backend half of the grid's "even out" button, run for the user after
    every pane the session OPENS (``Registry.add_terminal``), so a fresh
    terminal never arrives as a sliver carved from a pane that had already
    been dragged small. The arrangement — which pane sits where — is never
    touched, only how the boundaries fall; weights are stripe counts rather
    than a flat 1, so a nested group is given as much room as the terminals
    it lines up (see ``axis_span``).

    A ``pinned`` container — one whose boundaries the user dragged by hand —
    keeps its weights: the maintainer asked (2026-08-22) that a size chosen
    on purpose survives the next open, while everything NOT chosen comes out
    even. Its children are still evened individually, so a split inside a
    hand-sized column shares that column evenly without moving the column.
    Unlike the button, which evens everything, this one is the quiet default
    and must not undo a choice.
    """
    if root is None:
        return None
    return _evened(root)


def _evened(node: LayoutNode) -> LayoutNode:
    if isinstance(node, Leaf):
        return node
    children = [_evened(child) for child in node.children]
    if node.pinned:
        weights = [
            _clean_weight(node.weights[index] if index < len(node.weights) else 1.0)
            for index in range(len(children))
        ]
    else:
        weights = [float(axis_span(child, node.direction)) for child in node.children]
    return Split(direction=node.direction, children=children, weights=weights, pinned=node.pinned)


def remove_pane(root: LayoutNode | None, pane: str) -> LayoutNode | None:
    """The tree without ``pane``; ``None`` when it held nothing else.

    The freed room goes to the closed pane's SIBLINGS, proportionally — its
    weight is dropped and the rest renormalise. A container left with one
    child dissolves, which is how a workspace that was split apart folds back
    to simple shapes instead of accumulating scar tissue.

    A close can leave the survivors standing as a grid, so the result goes
    through :func:`rows_outermost` — once, at the end, never per recursion
    step.
    """
    return rows_outermost(_removed(root, pane))


def _removed(root: LayoutNode | None, pane: str) -> LayoutNode | None:
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
            slimmed = _removed(child, pane)
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
    return normalize(
        Split(direction=root.direction, children=children, weights=weights, pinned=root.pinned)
    )


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
    # The untransposed removal on purpose: the drop is carved from the tree
    # the user was looking at, and the grid is squared up once, afterwards.
    slimmed = _removed(root, pane)
    if slimmed is None:
        return Leaf(pane=pane) if contains(root, pane) else root
    rewritten, found = _insert_beside(slimmed, target, pane, direction, after)
    if not found:
        # The target vanished mid-flight (a concurrent close). Losing the
        # DROP is recoverable; losing the PANE is not — put it back at the
        # edge rather than dropping it from the tree.
        return append_pane(slimmed, pane)
    return _rows_outermost(normalize(rewritten))


# ------------------------------------------------------------- serialization


def to_dict(node: LayoutNode) -> dict[str, Any]:
    """The wire/persistence form: plain dicts, safe to JSON-encode."""
    if isinstance(node, Leaf):
        return {"pane": node.pane}
    return {
        "direction": node.direction,
        "children": [to_dict(child) for child in node.children],
        "weights": [_clean_weight(weight) for weight in node.weights],
        "pinned": bool(node.pinned),
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
    # A snapshot written before containers carried the flag: a container that
    # is uneven was dragged by a hand that is no longer there to say so, and
    # its sizes must survive the next open the same way they did until now.
    raw_pinned = data.get("pinned")
    node.pinned = bool(raw_pinned) if isinstance(raw_pinned, bool) else not _even_weights(node)
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

    This is also where ``pinned`` is decided, from the weights alone: a
    container the save leaves uneven was sized by hand and holds that size
    through later opens; one the save leaves even — dragged back, a seam
    double-clicked, the "even out" button — is released. The client's own
    ``pinned`` value, if it sends one, is not consulted: the flag is a fact
    about the weights, and deriving it here is what keeps the two from
    disagreeing.
    """
    if isinstance(current, Leaf) or isinstance(proposed, Leaf):
        return current
    adopted = Split(
        direction=current.direction,
        children=[
            adopt_weights(mine, theirs)
            for mine, theirs in zip(current.children, proposed.children, strict=True)
        ],
        weights=[_clean_weight(weight) for weight in proposed.weights[: len(current.children)]]
        + [1.0] * max(0, len(current.children) - len(proposed.weights)),
    )
    adopted.pinned = not _even_weights(adopted)
    return adopted


def grid_hints(root: LayoutNode | None) -> dict[str, tuple[int, int]]:
    """Coarse legacy ``(column, slot)`` for every pane, from the tree.

    Consumers that describe the workspace in words ("the top-left terminal")
    still think in columns, and for the common shapes the mapping is exact —
    both readings of a grid, since :func:`rows_outermost` puts one of them in
    the other's form. For deeper nesting it is deliberately coarse: the
    outermost branch fixes one coordinate, reading position within it the
    other — stable, ordered, and never claiming precision the flat model
    cannot hold.
    """
    if root is None:
        return {}
    if isinstance(root, Split) and root.direction == "row":
        return {
            pane: (column, slot)
            for column, child in enumerate(root.children)
            for slot, pane in enumerate(leaves(child))
        }
    if isinstance(root, Split):
        # A stack of bands: the band IS the slot, and reading across it gives
        # the column. A plain column of panes falls out of this as column 0.
        return {
            pane: (column, slot)
            for slot, child in enumerate(root.children)
            for column, pane in enumerate(leaves(child))
        }
    # A lone pane.
    return {pane: (0, slot) for slot, pane in enumerate(leaves(root))}
