"""Unit tests for the pruning helpers (ADR-0002).

Works on synthetic RawNode trees so the tests also run on
non-Windows systems.
"""
from __future__ import annotations

import time

import pytest

from jarvis.vision.pruning import (
    DEFAULT_INTERESTING_ROLES,
    RawNode,
    filter_by_depth,
    filter_by_role,
    filter_on_screen,
    prune_tree,
    rect_overlap_fraction,
)

MONITOR = (0, 0, 1920, 1080)


def _node(
    role: str = "Button",
    name: str = "OK",
    *,
    depth: int = 1,
    bounds: tuple[int, int, int, int] = (100, 100, 80, 30),
    automation_id: str = "",
    is_offscreen: bool = False,
) -> RawNode:
    return RawNode(
        role=role,
        name=name,
        automation_id=automation_id,
        bounds=bounds,
        depth=depth,
        is_offscreen=is_offscreen,
    )


# ---------------------------------------------------------------------------
# filter_by_depth
# ---------------------------------------------------------------------------

def test_filter_by_depth_keeps_up_to_max():
    nodes = [_node(depth=d) for d in range(10)]
    kept = filter_by_depth(nodes, max_depth=6)
    assert all(n.depth <= 6 for n in kept)
    assert len(kept) == 7  # depth 0..6


def test_filter_by_depth_empty():
    assert filter_by_depth([], max_depth=6) == []


# ---------------------------------------------------------------------------
# filter_by_role
# ---------------------------------------------------------------------------

def test_filter_by_role_drops_uninteresting_roles():
    nodes = [
        _node(role="Pane", name="wrap"),
        _node(role="Button", name="Save"),
        _node(role="Group", name="grp"),
        _node(role="Edit", name="query"),
    ]
    kept = filter_by_role(nodes)
    assert {n.role for n in kept} == {"Button", "Edit"}


def test_filter_by_role_drops_empty_name_unless_automation_id():
    nodes = [
        _node(role="Button", name=""),
        _node(role="Button", name="", automation_id="btnSave"),
        _node(role="Edit", name="search"),
    ]
    kept = filter_by_role(nodes)
    assert len(kept) == 2
    assert any(n.automation_id == "btnSave" for n in kept)
    assert any(n.name == "search" for n in kept)


def test_filter_by_role_accepts_whitelist_parameter():
    nodes = [_node(role="Pane", name="p"), _node(role="Button", name="b")]
    kept = filter_by_role(nodes, interesting_roles=("Pane",))
    assert len(kept) == 1
    assert kept[0].role == "Pane"


# ---------------------------------------------------------------------------
# rect_overlap_fraction
# ---------------------------------------------------------------------------

def test_rect_overlap_fully_inside():
    assert rect_overlap_fraction((10, 10, 100, 100), MONITOR) == 1.0


def test_rect_overlap_half_out():
    # Halbes Rect ausserhalb rechts.
    assert rect_overlap_fraction((1880, 500, 80, 80), MONITOR) == pytest.approx(0.5)


def test_rect_overlap_zero_area():
    assert rect_overlap_fraction((100, 100, 0, 0), MONITOR) == 0.0


def test_rect_overlap_completely_outside():
    assert rect_overlap_fraction((3000, 3000, 100, 100), MONITOR) == 0.0


# ---------------------------------------------------------------------------
# filter_on_screen
# ---------------------------------------------------------------------------

def test_filter_on_screen_drops_offscreen_flag():
    nodes = [_node(bounds=(10, 10, 50, 50), is_offscreen=True)]
    kept = filter_on_screen(nodes, monitor_bounds=MONITOR)
    assert kept == []


def test_filter_on_screen_applies_overlap_threshold():
    nodes = [
        _node(bounds=(10, 10, 50, 50)),                 # 100% inside
        _node(bounds=(1880, 500, 80, 80)),              # 50% inside
        _node(bounds=(1900, 500, 80, 80)),              # 25% inside -> out
        _node(bounds=(3000, 3000, 100, 100)),           # 0% -> out
    ]
    kept = filter_on_screen(nodes, monitor_bounds=MONITOR, min_overlap=0.5)
    assert len(kept) == 2


# ---------------------------------------------------------------------------
# prune_tree (Pipeline)
# ---------------------------------------------------------------------------

def test_prune_tree_applies_all_three_filters_in_order():
    root = _node(role="Window", name="root", depth=0, bounds=(0, 0, 1920, 1080))
    nodes = [
        root,
        _node(role="Button", name="OK", depth=2, bounds=(100, 100, 80, 30)),
        _node(role="Pane", name="", depth=1),                        # dropped (role)
        _node(role="Button", name="far-offscreen", depth=2,
              bounds=(3000, 3000, 50, 50)),                          # dropped (offscreen)
        _node(role="Edit", name="search", depth=8),                  # dropped (depth)
        _node(role="Button", name="", automation_id="closeBtn", depth=1,
              bounds=(10, 10, 20, 20)),                              # kept via autoID
    ]
    kept = prune_tree(nodes, max_depth=6, monitor_bounds=MONITOR)
    names = [n.name or n.automation_id for n in kept]
    # Root is always kept, plus the two good children.
    assert "root" in names
    assert "OK" in names
    assert "closeBtn" in names
    assert "far-offscreen" not in names
    assert "search" not in names


def test_prune_tree_preserves_root_even_if_role_would_drop():
    root = _node(role="Pane", name="", depth=0, bounds=(0, 0, 1920, 1080))
    nodes = [root, _node(role="Button", name="b", depth=1)]
    kept = prune_tree(nodes)
    assert kept[0] is root


def test_prune_tree_empty_input():
    assert prune_tree([]) == []


# ---------------------------------------------------------------------------
# Performance budget for a 5000-node tree (mandate §Pattern-Warnung)
# ---------------------------------------------------------------------------

def test_prune_tree_budget_under_300ms_for_5000_nodes():
    """Synthetic 5000-node tree — pruning must stay under 300ms.

    The mandate text explicitly warns: Chrome/VSCode/Slack have 5000+ nodes.
    Without this budget the CU loop would become impractical.
    """
    roles = list(DEFAULT_INTERESTING_ROLES) + ["Pane", "Group", "Separator"]
    nodes: list[RawNode] = [
        _node(role="Window", name="root", depth=0, bounds=(0, 0, 1920, 1080))
    ]
    # Mix aus guten + schlechten Nodes, verschiedene Tiefen.
    for i in range(5000):
        nodes.append(
            RawNode(
                role=roles[i % len(roles)],
                name=f"n{i}" if i % 3 != 0 else "",
                automation_id=f"id{i}" if i % 7 == 0 else "",
                bounds=(
                    (i * 13) % 1920,
                    (i * 7) % 1080,
                    80,
                    30,
                ),
                depth=(i % 9),
                parent_index=(i - 1) if i else 0,
            )
        )

    start = time.perf_counter()
    kept = prune_tree(nodes, max_depth=6, monitor_bounds=MONITOR)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 300, f"Pruning took {elapsed_ms:.1f}ms, budget 300ms"
    # We don't strictly require <=150; prune_tree itself doesn't retry.
    # That's the job of UIATreeSource. Here it's enough that it works in time.
    assert len(kept) >= 1
