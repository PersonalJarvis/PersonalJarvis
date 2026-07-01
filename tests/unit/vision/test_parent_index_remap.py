"""Regression test H1: parent_index is remapped correctly after pruning.

Before the fix, every ``parent_index`` in the pruned list was hardcoded to
``-1``, regardless of what the original tree said. This test builds a
synthetic tree, prunes it, and checks the remap logic.
"""
from __future__ import annotations

from jarvis.vision.pruning import RawNode, _remap_parent_indices, prune_tree


def _make_tree() -> list[RawNode]:
    """Baut einen kleinen Tree:

        Root (depth=0)
        ├── Panel (depth=1, gets pruned — not interesting)
        │   ├── Button-A (depth=2, bleibt)
        │   └── Button-B (depth=2, bleibt)
        └── Button-C (depth=1, bleibt)
    """
    return [
        RawNode(role="Window", name="Root", automation_id="root", depth=0,
                parent_index=-1, bounds=(0, 0, 100, 100)),
        RawNode(role="Panel", name="", automation_id="", depth=1,
                parent_index=0, bounds=(0, 0, 100, 50)),
        RawNode(role="Button", name="A", automation_id="btnA", depth=2,
                parent_index=1, bounds=(10, 10, 20, 20)),
        RawNode(role="Button", name="B", automation_id="btnB", depth=2,
                parent_index=1, bounds=(40, 10, 20, 20)),
        RawNode(role="Button", name="C", automation_id="btnC", depth=1,
                parent_index=0, bounds=(10, 60, 20, 20)),
    ]


def test_remap_direct_parent_survives():
    original = _make_tree()
    kept = [original[0], original[4]]                 # Root + Button-C
    _remap_parent_indices(kept, original)
    assert kept[0].parent_index == -1                  # Root
    assert kept[1].parent_index == 0                   # Button-C → Root (new idx 0)


def test_remap_skips_pruned_intermediate_parent():
    """Button-A and Button-B have Panel as their parent — Panel is gone —
    the new parents must point directly at Root.
    """
    original = _make_tree()
    kept = [original[0], original[2], original[3]]    # Root, Button-A, Button-B
    _remap_parent_indices(kept, original)
    assert kept[0].parent_index == -1
    assert kept[1].parent_index == 0
    assert kept[2].parent_index == 0


def test_remap_isolated_node_gets_minus_one():
    """Only Button-A alone — no ancestor chain left — parent_index=-1."""
    original = _make_tree()
    kept = [original[2]]
    _remap_parent_indices(kept, original)
    assert kept[0].parent_index == -1


def test_prune_tree_yields_remapped_indices():
    """End-to-End: prune_tree ruft _remap_parent_indices intern auf."""
    original = _make_tree()
    pruned = prune_tree(original, max_depth=6,
                         monitor_bounds=(0, 0, 1000, 1000))
    # Alle parent_indices zeigen auf gueltige Indizes innerhalb pruned
    for i, n in enumerate(pruned):
        assert n.parent_index == -1 or 0 <= n.parent_index < i, (
            f"pruned[{i}] has parent_index={n.parent_index} — invalid "
            f"(Subset-Groesse {len(pruned)})"
        )
    # Concretely: Root is [0], the buttons have parent=0
    assert pruned[0].name == "Root"
    for node in pruned[1:]:
        assert node.parent_index == 0
