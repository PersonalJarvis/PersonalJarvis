"""Monte Carlo Tree Search primitives backing LATS (ADR-0008 / FORENSICS gap).

Closes the FORENSICS Q3 verdict on ``lats.py``: the previous LATSEngine body
was ``try/except + counter`` with no tree, no UCB1, no rollout. This module
implements the actual MCTS primitives — PlanNode, ucb1, select, expand,
backpropagate — that ``LATSEngine.search_and_execute`` consumes to perform a
real tree search over candidate actions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, List


@dataclass
class PlanNode:
    state: Any
    parent: "PlanNode | None" = None
    children: List["PlanNode"] = field(default_factory=list)
    visits: int = 0
    value: float = 0.0
    untried_actions: List[Any] = field(default_factory=list)


def ucb1(child: PlanNode, parent_visits: int, c: float = math.sqrt(2.0)) -> float:
    if child.visits == 0:
        return float("inf")
    exploit = child.value / child.visits
    explore = c * math.sqrt(math.log(parent_visits) / child.visits)
    return exploit + explore


def select(node: PlanNode, *, c: float = math.sqrt(2.0)) -> PlanNode:
    while not node.untried_actions and node.children:
        node = max(node.children, key=lambda ch: ucb1(ch, node.visits, c=c))
    return node


def expand(
    node: PlanNode,
    *,
    action_to_state: Callable[[Any], Any],
) -> PlanNode:
    if not node.untried_actions:
        return node
    action = node.untried_actions.pop()
    child_state = action_to_state(action)
    child = PlanNode(state=child_state, parent=node)
    node.children.append(child)
    return child


def backpropagate(node: PlanNode, reward: float) -> None:
    current: PlanNode | None = node
    while current is not None:
        current.visits += 1
        current.value += reward
        current = current.parent


def best_child(node: PlanNode) -> PlanNode | None:
    if not node.children:
        return None
    return max(node.children, key=lambda ch: ch.visits)
