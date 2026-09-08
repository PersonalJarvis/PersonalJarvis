"""Discover loaded event contracts and reject inert routine filters."""

from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
from typing import Any

from jarvis.core.events import Event


def event_catalog() -> dict[str, list[str]]:
    """Loaded event schemas; availability still depends on a live publisher."""
    pending = list(Event.__subclasses__())
    result = {}
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if is_dataclass(cls):
            result[cls.__name__] = [field.name for field in fields(cls)]
    return dict(sorted(result.items()))


def validate_event_schedule(schedule: dict[str, Any]) -> None:
    name = str(schedule.get("event_name") or "")
    catalog = event_catalog()
    if name not in catalog:
        raise ValueError(f"Unknown event {name!r}; inspect society_routines for supported events")
    expression = schedule.get("filter_expr")
    if not expression:
        return
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, TypeError) as exc:
        raise ValueError("Invalid event filter") from exc
    allowed = (
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Name,
        ast.Constant,
        ast.Load,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError("Event filters support field equality, inequality, and/or/not only")
        if isinstance(node, ast.Name) and node.id not in catalog[name]:
            raise ValueError(f"Unknown field {node.id!r} on {name}")
