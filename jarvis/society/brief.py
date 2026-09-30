"""An agent's standing instructions, composed from a structured brief.

A voice request is one sentence ("an agent that sends me a morning briefing"),
and an agent that stores that sentence as its rulebook works from almost
nothing. The creating model fills these fields instead — mission, concrete
responsibilities, working rules, output format, boundaries, success
criteria — and this module renders them into the Markdown ``description``
deterministically, with no model call of its own (any single key works).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

__all__ = ["BRIEF_FIELDS", "compose_description", "has_brief"]

#: Structured fields, in the order they appear in the rendered instructions.
BRIEF_FIELDS: Final[tuple[str, ...]] = (
    "mission",
    "responsibilities",
    "working_rules",
    "output_format",
    "boundaries",
    "success_criteria",
)

_HEADINGS: Final[dict[str, str]] = {
    "responsibilities": "Responsibilities",
    "working_rules": "How you work",
    "output_format": "Output",
    "boundaries": "Boundaries",
    "success_criteria": "Done means",
}


def has_brief(fields: Mapping[str, Any]) -> bool:
    return any(_present(fields.get(name)) for name in BRIEF_FIELDS)


def _present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Iterable):
        return any(str(item).strip() for item in value)
    return False


def _lines(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [" ".join(str(item).split()) for item in value or () if str(item).strip()]


def compose_description(description: str, fields: Mapping[str, Any]) -> str:
    """Standing instructions: the mission, the free text, then one section per field."""
    parts: list[str] = []
    mission = _lines(fields.get("mission"))
    if mission:
        parts.append(" ".join(mission))
    if description.strip():
        parts.append(description.strip())
    for name in BRIEF_FIELDS[1:]:
        items = _lines(fields.get(name))
        if not items:
            continue
        heading = f"## {_HEADINGS[name]}"
        if isinstance(fields.get(name), str):
            parts.append(f"{heading}\n{items[0]}")
        else:
            parts.append(heading + "\n" + "\n".join(f"- {item}" for item in items))
    return "\n\n".join(parts)
