"""MCTS primitives: UCB1, select, expand, backpropagate."""

from __future__ import annotations

import math

from skillbook.guardrails.mcts import (
    PlanNode,
    backpropagate,
    expand,
    select,
    ucb1,
)


def test_ucb1_returns_inf_for_unvisited_child() -> None:
    parent = PlanNode(state="root", parent=None)
    parent.visits = 5
    child = PlanNode(state="a", parent=parent, visits=0, value=0.0)
    assert ucb1(child, parent.visits) == float("inf")


def test_ucb1_matches_formula() -> None:
    parent = PlanNode(state="root", parent=None)
    parent.visits = 10
    child = PlanNode(state="a", parent=parent, visits=3, value=2.0)
    expected = (2.0 / 3) + math.sqrt(2.0) * math.sqrt(math.log(10) / 3)
    assert abs(ucb1(child, parent.visits, c=math.sqrt(2.0)) - expected) < 1e-9


def test_select_returns_node_with_untried_actions() -> None:
    root = PlanNode(state="root", parent=None)
    root.untried_actions = [0, 1, 2]
    assert select(root) is root


def test_select_descends_through_fully_expanded_nodes() -> None:
    root = PlanNode(state="root", parent=None, visits=10)
    child_a = PlanNode(state="a", parent=root, visits=5, value=4.0)
    child_b = PlanNode(state="b", parent=root, visits=5, value=1.0)
    root.children = [child_a, child_b]
    chosen = select(root)
    assert chosen is child_a


def test_expand_adds_child_with_popped_action() -> None:
    root = PlanNode(state="root", parent=None)
    root.untried_actions = [0, 1, 2]
    new_child = expand(root, action_to_state=lambda a: f"action_{a}")
    assert new_child.state == "action_2"
    assert new_child.parent is root
    assert new_child in root.children
    assert root.untried_actions == [0, 1]


def test_expand_noop_when_no_untried_actions() -> None:
    root = PlanNode(state="root", parent=None)
    root.untried_actions = []
    out = expand(root, action_to_state=lambda a: a)
    assert out is root


def test_backpropagate_updates_full_chain() -> None:
    root = PlanNode(state="root", parent=None)
    a = PlanNode(state="a", parent=root)
    root.children = [a]
    leaf = PlanNode(state="leaf", parent=a)
    a.children = [leaf]

    backpropagate(leaf, reward=1.0)
    assert leaf.visits == 1 and leaf.value == 1.0
    assert a.visits == 1 and a.value == 1.0
    assert root.visits == 1 and root.value == 1.0

    backpropagate(leaf, reward=0.0)
    assert leaf.visits == 2 and leaf.value == 1.0
    assert root.visits == 2 and root.value == 1.0


def test_mcts_finds_winning_arm_among_three_candidates() -> None:
    root = PlanNode(state="root", parent=None)
    root.untried_actions = ["losing_a", "winner", "losing_b"]

    def rollout(state: str) -> float:
        return 1.0 if state == "winner" else 0.0

    for _ in range(60):
        node = select(root, c=math.sqrt(2.0))
        if node.untried_actions:
            node = expand(node, action_to_state=lambda a: a)
        reward = rollout(node.state)
        backpropagate(node, reward)

    by_visits = sorted(root.children, key=lambda c: c.visits, reverse=True)
    assert by_visits[0].state == "winner"
    assert by_visits[0].visits > by_visits[1].visits
