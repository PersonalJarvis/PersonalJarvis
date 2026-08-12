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


# ------------------------------------------------------------ opening shapes


def test_wizard_tree_of_one_is_a_bare_leaf() -> None:
    assert lt.wizard_tree(["t1"], 2) == Leaf(pane="t1")


def test_wizard_tree_stacks_two_deep_before_opening_a_column() -> None:
    tree = lt.wizard_tree(["t1", "t2", "t3", "t4"], 2)
    assert isinstance(tree, Split) and tree.direction == "row"
    assert [lt.leaves(child) for child in tree.children] == [["t1", "t2"], ["t3", "t4"]]
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

    assert isinstance(moved, Split) and moved.direction == "row"
    first, second = moved.children
    # The vacated half folds away: t2 stands alone, full height.
    assert first == Leaf(pane="t2")
    assert isinstance(second, Split) and second.direction == "column"
    assert second.children[0] == Leaf(pane="t3")
    cell = second.children[1]
    assert isinstance(cell, Split) and cell.direction == "row"
    assert lt.leaves(cell) == ["t1", "t4"]
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
    # One top-level stack → one column; slots follow reading order.
    assert lt.grid_hints(tree) == {"t1": (0, 0), "t3": (0, 1), "t2": (0, 2)}
