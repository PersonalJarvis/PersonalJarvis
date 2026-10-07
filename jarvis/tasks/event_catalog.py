"""Discover loaded event contracts and reject inert routine filters."""

from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
from typing import Any, Final

from jarvis.core.events import Event

#: Events a routine must never trigger on, by class name. Kept out of the
#: catalog on purpose, so they are neither advertised to the user or an agent
#: nor accepted by ``validate_event_schedule``.
#:
#: ``PermissionNeeded`` / ``PermissionResolved``
#:     They describe the state of an OS consent dialog and the grants behind it.
#:     An automation reacting to them would be an agent answering a system
#:     dialog (the macOS permission rules say an AI agent never does), and
#:     their ``target`` / ``detail`` fields describe the user's machine.
EXCLUDED_EVENT_NAMES: Final[frozenset[str]] = frozenset(
    {"PermissionNeeded", "PermissionResolved"}
)


def event_catalog() -> dict[str, list[str]]:
    """Loaded event schemas; availability still depends on a live publisher.

    Events in ``EXCLUDED_EVENT_NAMES`` are not part of it.
    """
    pending = list(Event.__subclasses__())
    result = {}
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if is_dataclass(cls) and cls.__name__ not in EXCLUDED_EVENT_NAMES:
            result[cls.__name__] = [field.name for field in fields(cls)]
    return dict(sorted(result.items()))


def validate_event_schedule(schedule: dict[str, Any]) -> None:
    name = str(schedule.get("event_name") or "")
    if name in EXCLUDED_EVENT_NAMES:
        raise ValueError(f"Event {name!r} cannot trigger a routine")
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
