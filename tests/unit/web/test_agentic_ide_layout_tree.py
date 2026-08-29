"""The split tree behind the Agentic-IDE workspace: splits are LOCAL.

The flat "columns × slots" grid could not say "beside the top pane only" —
"split right" was a full-height column by construction, so splitting the top
pane of a stack restructured the whole workspace (reported with a drawing,
2026-08-12). These tests pin the tree's answer: every split and every drop
carves the clicked pane's own rectangle and leaves every cousin alone.
"""

from __future__ import annotations

import pytest

from jarvis.agentic_ide import layout_tree as lt
from jarvis.agentic_ide.layout_tree import Leaf, Split


def check_canonical(node: lt.LayoutNode | None) -> None:
    """Assert the module's structural invariants, recursively."""
    if node is None or isinstance(node, Leaf):
        return
    assert len(node.children) >= 2, "a container must hold at least two children"
    assert len(node.weights) == len(node.children), "one weight per child"
    assert all(w > 0 for w in node.weights), "weights are positive"
    for child in node.children:
        if isinstance(child, Split):
            assert child.direction != node.direction, "no same-direction nesting"
        check_canonical(child)


def boxes(
    node: lt.LayoutNode | None,
    box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
) -> dict[str, tuple[float, float, float, float]]:
    """Every pane's rectangle as fractions of the workspace — the PICTURE.

    The frontend's ``treeLayout`` arithmetic, repeated here so a test can ask
    "does this tree draw what that tree drew?" without caring how the tree is
    shaped. That is exactly the question ``rows_outermost`` has to answer yes
    to: it moves boundaries between CONTAINERS, never on screen.
    """
    x, y, width, height = box
    if node is None:
        return {}
    if isinstance(node, Leaf):
        return {node.pane: (round(x, 9), round(y, 9), round(width, 9), round(height, 9))}
    weights = [lt._clean_weight(node.weights[i]) for i in range(len(node.children))]
    total = sum(weights) or 1.0
    along = width if node.direction == "row" else height
    cursor = x if node.direction == "row" else y
    drawn: dict[str, tuple[float, float, float, float]] = {}
    for child, weight in zip(node.children, weights, strict=True):
        share = (weight / total) * along
        drawn.update(
            boxes(
                child,
                (cursor, y, share, height)
                if node.direction == "row"
                else (x, cursor, width, share),
            )
        )
        cursor += share
    return drawn


# ------------------------------------------------------------ opening shapes


def test_wizard_tree_of_one_is_a_bare_leaf() -> None:
    assert lt.wizard_tree(["t1"], 2) == Leaf(pane="t1")


def test_wizard_tree_stacks_two_deep_before_opening_a_column() -> None:
    tree = lt.wizard_tree(["t1", "t2", "t3", "t4"], 2)
    # Two columns of two, stood on their rows so each band owns its own
    # vertical seam — and seated in reading order, so the panes come out in
    # the sequence they were handed over in.
    assert boxes(tree) == {
        "t1": (0.0, 0.0, 0.5, 0.5),
        "t2": (0.5, 0.0, 0.5, 0.5),
        "t3": (0.0, 0.5, 0.5, 0.5),
        "t4": (0.5, 0.5, 0.5, 0.5),
    }
    assert isinstance(tree, Split) and tree.direction == "column"
    assert lt.leaves(tree) == ["t1", "t2", "t3", "t4"]
    check_canonical(tree)


def test_wizard_tree_stands_an_odd_pane_in_a_column_of_its_own() -> None:
    tree = lt.wizard_tree(["t1", "t2", "t3"], 2)
    assert isinstance(tree, Split) and tree.direction == "row"
    assert [lt.leaves(child) for child in tree.children] == [["t1", "t2"], ["t3"]]


def test_leaves_read_left_to_right_top_to_bottom() -> None:
    tree = lt.wizard_tree(["t1", "t2", "t3", "t4", "t5"], 2)
    assert lt.leaves(tree) == ["t1", "t2", "t3", "t4", "t5"]


# ------------------------------------------------------- splits stay local


def test_split_right_on_top_of_a_stack_leaves_the_bottom_full_width() -> None:
    """THE reported bug, as a structure: T2 must keep the whole bottom row."""
    stack = lt.wizard_tree(["t1", "t2"], 2)
    tree = lt.split_pane(stack, "t1", "t3", "right")

    assert isinstance(tree, Split) and tree.direction == "column"
    top, bottom = tree.children
    assert isinstance(top, Split) and top.direction == "row"
    assert lt.leaves(top) == ["t1", "t3"]
    # The bottom pane is a DIRECT child of the stack — full width, untouched.
    assert bottom == Leaf(pane="t2")
    check_canonical(tree)


def test_split_right_on_the_bottom_of_a_stack_leaves_the_top_full_width() -> None:
    stack = lt.wizard_tree(["t1", "t2"], 2)
    tree = lt.split_pane(stack, "t2", "t3", "right")

    assert isinstance(tree, Split) and tree.direction == "column"
    top, bottom = tree.children
    assert top == Leaf(pane="t1")
    assert isinstance(bottom, Split) and lt.leaves(bottom) == ["t2", "t3"]


def test_split_right_in_a_row_joins_the_row_and_halves_the_anchor() -> None:
    row = Split(
        direction="row",
        children=[Leaf(pane="t1"), Leaf(pane="t2")],
        weights=[2.0, 1.0],
    )
    tree = lt.split_pane(row, "t1", "t3", "right")

    assert isinstance(tree, Split) and tree.direction == "row"
    assert lt.leaves(tree) == ["t1", "t3", "t2"]
    # The pair shares the anchor's former room; the neighbour keeps its own.
    assert tree.weights == [1.0, 1.0, 1.0]


def test_split_down_in_a_stack_halves_the_anchor_only() -> None:
    stack = Split(
        direction="column",
        children=[Leaf(pane="t1"), Leaf(pane="t2")],
        weights=[2.0, 2.0],
    )
    tree = lt.split_pane(stack, "t1", "t3", "down")

    assert isinstance(tree, Split) and tree.direction == "column"
    assert lt.leaves(tree) == ["t1", "t3", "t2"]
    assert tree.weights == [1.0, 1.0, 2.0]


def test_split_down_beside_a_neighbour_leaves_the_neighbour_whole() -> None:
    row = lt.wizard_tree(["t1", "t2"], 1)  # two panes side by side
    tree = lt.split_pane(row, "t2", "t3", "down")

    assert isinstance(tree, Split) and tree.direction == "row"
    left, right = tree.children
    assert left == Leaf(pane="t1")
    assert isinstance(right, Split) and right.direction == "column"
    assert lt.leaves(right) == ["t2", "t3"]


def test_deep_splits_stay_local_at_any_depth() -> None:
    tree: lt.LayoutNode | None = lt.wizard_tree(["t1", "t2"], 2)
    tree = lt.split_pane(tree, "t1", "t3", "right")
    tree = lt.split_pane(tree, "t3", "t4", "down")
    tree = lt.split_pane(tree, "t4", "t5", "right")

    # However deep it went, T2 is still a direct child of the root stack.
    assert isinstance(tree, Split) and tree.direction == "column"
    assert tree.children[1] == Leaf(pane="t2")
    check_canonical(tree)


def test_anchorless_split_appends_a_full_height_column() -> None:
    tree = lt.split_pane(lt.wizard_tree(["t1", "t2"], 2), None, "t3", "right")
    assert isinstance(tree, Split) and tree.direction == "row"
    assert lt.leaves(tree) == ["t1", "t2", "t3"]
    assert tree.children[1] == Leaf(pane="t3")


def test_split_on_a_vanished_anchor_falls_back_to_appending() -> None:
    tree = lt.split_pane(Leaf(pane="t1"), "ghost", "t2", "right")
    assert lt.leaves(tree) == ["t1", "t2"]


def test_split_into_an_empty_workspace_is_a_bare_leaf() -> None:
    assert lt.split_pane(None, None, "t1", "right") == Leaf(pane="t1")


def test_appending_takes_an_even_share_of_a_dragged_row() -> None:
    row = Split(
        direction="row",
        children=[Leaf(pane="t1"), Leaf(pane="t2")],
        weights=[3.0, 1.0],
    )
    tree = lt.append_pane(row, "t3")
    assert isinstance(tree, Split)
    # The mean of the neighbours, not 1.0 — "an even share" must mean the same
    # thing whatever scale the user's drags left the weights at.
    assert tree.weights == [3.0, 1.0, 2.0]


# --------------------------------------------------------- a grid on rows


def test_a_grid_reached_either_way_comes_out_the_same() -> None:
    """The 2026-08-25 report: split order decided how the seams behaved.

    Right-then-down-twice used to leave ``row[column, column]``, whose middle
    line runs the full height — drag it under the bottom pair and the top
    pair moves with it. Down-then-right-twice left the transpose, where each
    band resizes on its own. Same picture, two behaviours. Now both orders
    land on the second, so a workspace behaves the way it looks.
    """
    right_first = lt.split_pane(Leaf(pane="t1"), "t1", "t2", "right")
    right_first = lt.split_pane(right_first, "t1", "t3", "down")
    right_first = lt.split_pane(right_first, "t2", "t4", "down")

    down_first = lt.split_pane(Leaf(pane="t1"), "t1", "t3", "down")
    down_first = lt.split_pane(down_first, "t1", "t2", "right")
    down_first = lt.split_pane(down_first, "t3", "t4", "right")

    assert boxes(right_first) == boxes(down_first)
    assert lt.same_shape(right_first, down_first)
    assert isinstance(right_first, Split) and right_first.direction == "column"
    check_canonical(right_first)


def test_standing_a_grid_on_its_rows_moves_nothing() -> None:
    """The whole promise: containers change hands, the picture does not."""
    columns = Split(
        direction="row",
        children=[
            Split(
                direction="column",
                children=[Leaf(pane="t1"), Leaf(pane="t2")],
                weights=[3.0, 1.0],
            ),
            Split(
                direction="column",
                children=[Leaf(pane="t3"), Leaf(pane="t4")],
                weights=[6.0, 2.0],  # the same 3:1, said differently
            ),
        ],
        weights=[2.0, 1.0],
    )
    rows = lt.rows_outermost(columns)
    assert boxes(rows) == boxes(columns)
    assert isinstance(rows, Split) and rows.direction == "column"
    assert [lt.leaves(band) for band in rows.children] == [["t1", "t3"], ["t2", "t4"]]
    check_canonical(rows)


def test_columns_cut_at_different_heights_are_left_alone() -> None:
    """No row structure can draw two bands that do not line up, so: hands off.

    This is also what keeps the rule off a hand-sized workspace — a column
    dragged out of line with its neighbour simply stops qualifying.
    """
    ragged = Split(
        direction="row",
        children=[
            Split(
                direction="column",
                children=[Leaf(pane="t1"), Leaf(pane="t2")],
                weights=[3.0, 1.0],
            ),
            Split(
                direction="column",
                children=[Leaf(pane="t3"), Leaf(pane="t4")],
                weights=[1.0, 3.0],
            ),
        ],
        weights=[1.0, 1.0],
    )
    stood = lt.rows_outermost(ragged)
    assert isinstance(stood, Split) and stood.direction == "row"
    assert boxes(stood) == boxes(ragged)


def test_an_incomplete_grid_is_left_alone() -> None:
    """Two stacks and a full-height pane are not a rectangle — nothing to do."""
    tree = Split(
        direction="row",
        children=[
            Split(
                direction="column",
                children=[Leaf(pane="t1"), Leaf(pane="t2")],
                weights=[1.0, 1.0],
            ),
            Leaf(pane="t3"),
        ],
        weights=[1.0, 1.0],
    )
    stood = lt.rows_outermost(tree)
    assert isinstance(stood, Split) and stood.direction == "row"
    assert boxes(stood) == boxes(tree)


def test_standing_a_grid_on_its_rows_settles_at_once() -> None:
    """Idempotent, so repeated edits cannot flip a workspace back and forth."""
    once = lt.rows_outermost(lt.wizard_tree(["t1", "t2", "t3", "t4", "t5", "t6"], 3))
    assert lt.rows_outermost(once) == once


def test_a_hand_sized_grid_keeps_its_pin_on_the_bands() -> None:
    """The row's hand-set widths become each band's — the sizes were the WIDTHS."""
    columns = Split(
        direction="row",
        children=[
            Split(
                direction="column",
                children=[Leaf(pane="t1"), Leaf(pane="t2")],
                weights=[1.0, 1.0],
            ),
            Split(
                direction="column",
                children=[Leaf(pane="t3"), Leaf(pane="t4")],
                weights=[1.0, 1.0],
            ),
        ],
        weights=[3.0, 1.0],
        pinned=True,
    )
    rows = lt.rows_outermost(columns)
    assert isinstance(rows, Split)
    assert all(isinstance(band, Split) and band.pinned for band in rows.children)
    # And the pin does its job: an evening pass leaves those widths alone.
    assert boxes(lt.evened(rows)) == boxes(rows)


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7, 8, 9, 12])
def test_the_wizard_preview_seats_panes_where_the_backend_does(count: int) -> None:
    """The preview labels its tiles with the agents' names — so WHICH, not just where.

    `wizardPanes` (frontend ``layout.ts``) is the preview's half of this
    function, and the two now have a fill order to agree on as well as a
    shape: a full rectangle is read across its bands, a ragged count down its
    columns. This is the parity test that keeps them from drifting — the
    frontend's own copy is pinned in ``layout.test.ts``.
    """
    depth = 2  # WIZARD_COLUMN_HEIGHT
    keys = [f"t{index + 1}" for index in range(count)]
    hints = lt.grid_hints(lt.wizard_tree(keys, depth))

    across = -(-count // depth)
    rectangular = across > 0 and count == across * depth
    preview = [
        (index % across, index // across) if rectangular else (index // depth, index % depth)
        for index in range(count)
    ]
    assert [hints[key] for key in keys] == preview


def test_a_grid_stands_on_its_rows_after_a_close_too() -> None:
    """A close can leave a rectangle behind; it gets the same treatment.

    Two stacks beside a full-height pane is not a grid, so nothing is stood on
    anything while t5 is there. Closing t5 makes it one, and the survivors get
    their per-band seams without the user having to split again.
    """
    tree = Split(
        direction="row",
        children=[
            Split(
                direction="column",
                children=[Leaf(pane="t1"), Leaf(pane="t3")],
                weights=[1.0, 1.0],
            ),
            Split(
                direction="column",
                children=[Leaf(pane="t2"), Leaf(pane="t4")],
                weights=[1.0, 1.0],
            ),
            Leaf(pane="t5"),
        ],
        weights=[1.0, 1.0, 1.0],
    )
    assert lt.rows_outermost(tree) == tree

    closed = lt.remove_pane(tree, "t5")
    assert isinstance(closed, Split) and closed.direction == "column"
    assert [lt.leaves(band) for band in closed.children] == [["t1", "t2"], ["t3", "t4"]]
    check_canonical(closed)


# ------------------------------------------------------------- evening out


def test_evened_levels_a_dragged_row() -> None:
    row = Split(
        direction="row",
        children=[Leaf(pane="t1"), Leaf(pane="t2"), Leaf(pane="t3")],
        weights=[3.0, 1.0, 2.0],
    )
    tree = lt.evened(row)
    assert isinstance(tree, Split)
    assert tree.weights == [1.0, 1.0, 1.0]
    # Only the boundaries moved — the arrangement is untouched.
    assert lt.leaves(tree) == ["t1", "t2", "t3"]
    check_canonical(tree)


def test_evened_counts_terminals_not_tree_nodes() -> None:
    """A nested stack is as many stripes as the terminals it lines up.

    ``row[pane, stack-of-two, pane]`` evened to a flat 1:1:1 would hand the
    stack a quarter of the width and draw its two terminals half as wide as
    their neighbours (reported 2026-08-12 on the grid's button). Stripe
    counts are the yardstick the frontend's ``evenedTree`` uses, and the
    backend must deal the same shares or the button reads "already even"
    over a workspace the server just left uneven.
    """
    stack = Split(
        direction="row",
        children=[Leaf(pane="t2"), Leaf(pane="t3")],
        weights=[5.0, 1.0],
    )
    tree = lt.evened(
        Split(
            direction="column",
            children=[Leaf(pane="t1"), stack, Leaf(pane="t4")],
            weights=[4.0, 1.0, 1.0],
        )
    )
    assert isinstance(tree, Split)
    # A row inside a column is only ONE terminal tall — plain shares.
    assert tree.weights == [1.0, 1.0, 1.0]
    inner = tree.children[1]
    assert isinstance(inner, Split) and inner.weights == [1.0, 1.0]

    wide = lt.evened(
        Split(
            direction="row",
            children=[Leaf(pane="t1"), stack, Leaf(pane="t4")],
            weights=[4.0, 1.0, 1.0],
        )
    )
    assert isinstance(wide, Split)
    # A row inside a row is TWO terminals wide and is dealt two shares.
    assert wide.weights == [1.0, 2.0, 1.0]


def test_evened_leaves_the_trivial_trees_alone() -> None:
    assert lt.evened(None) is None
    assert lt.evened(Leaf(pane="t1")) == Leaf(pane="t1")


def test_evened_keeps_hand_sized_boundaries_and_evens_the_rest() -> None:
    """A pinned container holds its weights; its children still even out."""
    column = Split(
        direction="column",
        children=[Leaf(pane="t2"), Leaf(pane="t3"), Leaf(pane="t4")],
        weights=[1.0, 0.5, 0.5],  # a split's halving, nobody's choice
    )
    tree = lt.evened(
        Split(
            direction="row",
            children=[Leaf(pane="t1"), column],
            weights=[3.0, 1.0],
            pinned=True,
        )
    )
    assert isinstance(tree, Split)
    assert tree.weights == [3.0, 1.0] and tree.pinned is True
    inner = tree.children[1]
    assert isinstance(inner, Split)
    assert inner.weights == [1.0, 1.0, 1.0] and inner.pinned is False


def test_adopting_weights_decides_pinned_from_the_weights() -> None:
    mine = lt.wizard_tree(["t1", "t2", "t3"], 2)
    theirs = lt.wizard_tree(["t1", "t2", "t3"], 2)
    assert isinstance(mine, Split) and isinstance(theirs, Split)

    theirs.weights = [3.0, 1.0]
    dragged = lt.adopt_weights(mine, theirs)
    assert isinstance(dragged, Split) and dragged.pinned is True
    # The inner stack was not touched and stays free.
    assert isinstance(dragged.children[0], Split) and dragged.children[0].pinned is False

    theirs.weights = [1.0, 1.0]
    # A client may echo `pinned` back; the flag is a fact about the weights
    # and the echo is not consulted.
    theirs.pinned = True
    released = lt.adopt_weights(dragged, theirs)
    assert isinstance(released, Split) and released.pinned is False


def test_is_even_measures_terminals_not_nodes() -> None:
    stack = Split(direction="row", children=[Leaf(pane="t2"), Leaf(pane="t3")], weights=[1.0, 1.0])
    quarters = Split(
        direction="row",
        children=[Leaf(pane="t1"), stack, Leaf(pane="t4")],
        weights=[1.0, 1.0, 1.0],
    )
    # Equal container weights over a two-wide group: NOT even.
    assert not lt.is_even(quarters)
    assert lt.is_even(lt.evened(quarters))
    assert lt.is_even(None) and lt.is_even(Leaf(pane="t1"))


def test_pinned_survives_the_dict_form_and_legacy_snapshots() -> None:
    tree = Split(direction="row", children=[Leaf(pane="t1"), Leaf(pane="t2")], weights=[3.0, 1.0])
    tree.pinned = True
    assert lt.to_dict(tree)["pinned"] is True
    assert lt.from_dict(lt.to_dict(tree)) == tree
    assert lt.from_dict(lt.to_dict(lt.evened(tree))).pinned is True  # explicit flag wins

    # A snapshot from before the flag: an uneven container was dragged by a
    # hand that is no longer there to say so, and keeps its sizes.
    legacy = {"direction": "row", "children": [{"pane": "a"}, {"pane": "b"}], "weights": [3, 1]}
    assert lt.from_dict(legacy).pinned is True
    legacy["weights"] = [1, 1]
    assert lt.from_dict(legacy).pinned is False


def test_structural_edits_carry_the_pin() -> None:
    row = Split(
        direction="row",
        children=[Leaf(pane="t1"), Leaf(pane="t2"), Leaf(pane="t3")],
        weights=[2.0, 1.0, 1.0],
        pinned=True,
    )
    slimmed = lt.remove_pane(row, "t3")
    assert isinstance(slimmed, Split) and slimmed.pinned is True
    split = lt.split_pane(row, "t2", "t4", "right")
    assert isinstance(split, Split) and split.pinned is True
    # A split running the other way opens a fresh, free container.
    stacked = lt.split_pane(row, "t2", "t5", "down")
    assert isinstance(stacked, Split) and stacked.pinned is True
    inner = stacked.children[1]
    assert isinstance(inner, Split) and inner.pinned is False
    # Merging a pinned row into its parent row keeps the pin.
    nested = Split(
        direction="row",
        children=[Leaf(pane="t0"), row],
        weights=[1.0, 1.0],
    )
    flat = lt.normalize(nested)
    assert isinstance(flat, Split) and flat.pinned is True
    check_canonical(flat)


def test_axis_span_is_the_widest_member_across_the_grain() -> None:
    stack = Split(
        direction="column",
        children=[Leaf(pane="t1"), Leaf(pane="t2"), Leaf(pane="t3")],
        weights=[1.0, 1.0, 1.0],
    )
    assert lt.axis_span(stack, "column") == 3
    assert lt.axis_span(stack, "row") == 1
    assert lt.axis_span(Leaf(pane="t9"), "row") == 1


# ---------------------------------------------------------------- closing


def test_removing_a_split_half_folds_the_pair_back_to_one_pane() -> None:
    tree = lt.split_pane(lt.wizard_tree(["t1", "t2"], 2), "t1", "t3", "right")
    slimmed = lt.remove_pane(tree, "t3")

    # The workspace is exactly the stack it was before the split.
    assert slimmed == lt.wizard_tree(["t1", "t2"], 2)


def test_removing_gives_the_room_to_the_siblings() -> None:
    row = Split(
        direction="row",
        children=[Leaf(pane="t1"), Leaf(pane="t2"), Leaf(pane="t3")],
        weights=[2.0, 1.0, 1.0],
    )
    slimmed = lt.remove_pane(row, "t3")
    assert isinstance(slimmed, Split)
    assert slimmed.weights == [2.0, 1.0]  # 2:1 survives, the tail's share dissolves


def test_removing_the_last_pane_empties_the_tree() -> None:
    assert lt.remove_pane(Leaf(pane="t1"), "t1") is None


def test_removing_an_unknown_pane_changes_nothing() -> None:
    tree = lt.wizard_tree(["t1", "t2"], 2)
    assert lt.remove_pane(tree, "ghost") == tree


def test_a_collapse_that_meets_its_grandparent_flattens() -> None:
    # row[ column[row[t1,t2], t3] , t4 ] — closing t3 leaves column holding one
    # row child, which must splice into the outer row, not nest row-in-row.
    tree: lt.LayoutNode | None = Split(
        direction="row",
        children=[
            Split(
                direction="column",
                children=[
                    Split(
                        direction="row",
                        children=[Leaf(pane="t1"), Leaf(pane="t2")],
                        weights=[1.0, 1.0],
                    ),
                    Leaf(pane="t3"),
                ],
                weights=[1.0, 1.0],
            ),
            Leaf(pane="t4"),
        ],
        weights=[1.0, 1.0],
    )
    slimmed = lt.remove_pane(tree, "t3")
    assert isinstance(slimmed, Split) and slimmed.direction == "row"
    assert lt.leaves(slimmed) == ["t1", "t2", "t4"]
    check_canonical(slimmed)


# ------------------------------------------------------------------ moving


def test_swap_exchanges_panes_and_keeps_every_weight() -> None:
    tree = Split(
        direction="row",
        children=[Leaf(pane="t1"), Leaf(pane="t2")],
        weights=[3.0, 1.0],
    )
    swapped = lt.move_pane(tree, "t1", "t2", "swap")
    assert isinstance(swapped, Split)
    assert lt.leaves(swapped) == ["t2", "t1"]
    assert swapped.weights == [3.0, 1.0]


def test_dropping_left_of_a_pane_takes_that_panes_left_half() -> None:
    tree = lt.wizard_tree(["t1", "t2", "t3", "t4"], 2)
    moved = lt.move_pane(tree, "t1", "t4", "left")

    # t1 lands in t4's left half — an eighth of the workspace each — and the
    # top band closes over the hole t1 left, t2 taking the full width.
    assert boxes(moved) == {
        "t2": (0.0, 0.0, 1.0, 0.5),
        "t3": (0.0, 0.5, 0.5, 0.5),
        "t1": (0.5, 0.5, 0.25, 0.5),
        "t4": (0.75, 0.5, 0.25, 0.5),
    }
    check_canonical(moved)


def test_dropping_below_a_pane_takes_that_panes_bottom_half() -> None:
    row = lt.wizard_tree(["t1", "t2"], 1)
    moved = lt.move_pane(row, "t1", "t2", "below")
    assert isinstance(moved, Split) and moved.direction == "column"
    assert lt.leaves(moved) == ["t2", "t1"]


def test_dropping_on_itself_is_a_no_op() -> None:
    tree = lt.wizard_tree(["t1", "t2"], 2)
    assert lt.move_pane(tree, "t1", "t1", "left") == tree


def test_a_drop_whose_target_vanished_keeps_the_pane_at_the_edge() -> None:
    tree = lt.wizard_tree(["t1", "t2", "t3"], 2)
    moved = lt.move_pane(tree, "t1", "ghost", "left")
    assert moved is not None
    assert sorted(lt.leaves(moved)) == ["t1", "t2", "t3"]
    check_canonical(moved)


# ------------------------------------------------- serialization & weights


def test_round_trip_survives_dict_form() -> None:
    tree = lt.split_pane(lt.wizard_tree(["t1", "t2", "t3"], 2), "t2", "t4", "right")
    assert tree is not None
    assert lt.from_dict(lt.to_dict(tree)) == tree


@pytest.mark.parametrize(
    "junk",
    [
        "not a dict",
        {"direction": "diagonal", "children": [{"pane": "a"}, {"pane": "b"}]},
        {"direction": "row", "children": [{"pane": "a"}]},
        {"direction": "row", "children": [{"pane": "a"}, {"pane": "a"}]},
        {"pane": ""},
    ],
)
def test_from_dict_refuses_malformed_trees(junk: object) -> None:
    with pytest.raises(ValueError):
        lt.from_dict(junk)


def test_from_dict_normalizes_stored_degenerates() -> None:
    # A same-direction nesting written by a buggy or older client flattens on
    # read, with weights scaled so nothing moves on screen.
    stored = {
        "direction": "row",
        "children": [
            {"pane": "t1"},
            {
                "direction": "row",
                "children": [{"pane": "t2"}, {"pane": "t3"}],
                "weights": [1.0, 3.0],
            },
        ],
        "weights": [1.0, 1.0],
    }
    tree = lt.from_dict(stored)
    assert isinstance(tree, Split) and tree.direction == "row"
    assert lt.leaves(tree) == ["t1", "t2", "t3"]
    assert tree.weights == [1.0, 0.25, 0.75]
    check_canonical(tree)


def test_adopting_weights_needs_the_same_shape() -> None:
    mine = lt.wizard_tree(["t1", "t2", "t3"], 2)
    dragged = lt.wizard_tree(["t1", "t2", "t3"], 2)
    assert isinstance(mine, Split) and isinstance(dragged, Split)
    dragged.weights = [3.0, 1.0]

    assert lt.same_shape(mine, dragged)
    adopted = lt.adopt_weights(mine, dragged)
    assert isinstance(adopted, Split)
    assert adopted.weights == [3.0, 1.0]

    reshaped = lt.split_pane(dragged, "t3", "t9", "down")
    assert not lt.same_shape(mine, reshaped)


# ------------------------------------------------------- legacy migration


def test_from_grid_rebuilds_the_columns_of_stacks_shape() -> None:
    legacy = [("t1", 0, 0), ("t2", 0, 1), ("t3", 1, 0)]
    assert lt.from_grid(legacy) == lt.wizard_tree(["t1", "t2", "t3"], 2)


def test_from_grid_of_nothing_is_an_empty_tree() -> None:
    assert lt.from_grid([]) is None


def test_grid_hints_are_exact_for_flat_shapes() -> None:
    tree = lt.wizard_tree(["t1", "t2", "t3"], 2)
    assert lt.grid_hints(tree) == {"t1": (0, 0), "t2": (0, 1), "t3": (1, 0)}


def test_grid_hints_stay_coarse_but_ordered_for_nested_shapes() -> None:
    stack = lt.wizard_tree(["t1", "t2"], 2)
    tree = lt.split_pane(stack, "t1", "t3", "right")
    # A stack of bands: the band is the slot, reading across it the column.
    # t1 and t3 share the top band, t2 has the bottom one to itself.
    assert lt.grid_hints(tree) == {"t1": (0, 0), "t3": (1, 0), "t2": (0, 1)}
