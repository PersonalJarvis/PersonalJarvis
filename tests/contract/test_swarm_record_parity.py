"""Every typed Swarm browser record retains the backend's wire representation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis.core import swarm_types

SOURCE = (
    Path(__file__).resolve().parents[2] / "jarvis/ui/web/frontend/src/components/swarm/types.ts"
).read_text(encoding="utf-8")
ALIASES = dict(re.findall(r"export type (\w+)\s*=\s*([^;]+);", SOURCE))
INTERFACES = {
    name: (base, body)
    for name, base, body in re.findall(
        r"export interface (\w+)(?: extends (\w+))?\s*\{([^}]*)\}", SOURCE
    )
}
MODELS = [
    swarm_types.BudgetLimits,
    swarm_types.CapabilityPolicy,
    swarm_types.TeamRecord,
    swarm_types.TeamUnavailable,
    swarm_types.AgentRecord,
    swarm_types.TaskSpec,
    swarm_types.TaskRecord,
    swarm_types.ActivityRecord,
    swarm_types.WorldGroup,
    swarm_types.WorldSnapshot,
    swarm_types.TeamCreate,
    swarm_types.CheckpointSnapshot,
]


def fields(name):
    base, body = INTERFACES[name]
    result = fields(base) if base else {}
    for declaration in body.split(";"):
        if not declaration.strip():
            continue
        match = re.fullmatch(r"\s*(\w+)(\??)\s*:\s*(.+)\s*", declaration, flags=re.DOTALL)
        assert match, f"Unrecognized interface member: {name}.{declaration}"
        result[match[1]] = (bool(match[2]), match[3].strip())
    return result


def ts_shape(annotation):
    if annotation in ALIASES:
        return ts_shape(ALIASES[annotation])
    if annotation.startswith("Record<string,"):
        return ("map", ts_shape(annotation[len("Record<string,") : -1].strip()))
    if annotation.endswith("[]"):
        return ("array", ts_shape(annotation[:-2]))
    if "|" in annotation:
        return frozenset(ts_shape(part.strip()) for part in annotation.split("|"))
    if annotation.startswith('"') or annotation in {"true", "false"}:
        return ("literal", json.loads(annotation))
    if annotation in {"string", "number", "boolean", "null", "unknown"}:
        return annotation
    assert annotation in INTERFACES, f"Unrecognized TypeScript type: {annotation}"
    return ("record", annotation)


def schema_shape(schema, definitions):
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[1]
        if name in INTERFACES:
            return ("record", name)
        return schema_shape(definitions[name], definitions)
    if "anyOf" in schema:
        return frozenset(schema_shape(part, definitions) for part in schema["anyOf"])
    if "const" in schema:
        return ("literal", schema["const"])
    if "enum" in schema:
        return frozenset(("literal", value) for value in schema["enum"])
    # TeamCreate's bounded storage-mode vocabulary is encoded as a pattern.
    if schema.get("pattern") == r"^(local|distributed)$":
        return frozenset(("literal", value) for value in ("local", "distributed"))
    kind = schema.get("type", "unknown")
    if kind == "array":
        return ("array", schema_shape(schema["items"], definitions))
    if kind == "object":
        value = schema["additionalProperties"]
        return ("map", "unknown" if value is True else schema_shape(value, definitions))
    return "number" if kind == "integer" else kind


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_every_browser_record_field_and_serialized_shape_matches(model):
    browser = fields(model.__name__)
    schema = model.model_json_schema(mode="serialization")
    assert set(browser) == set(schema["properties"])
    for name, (optional, annotation) in browser.items():
        assert not optional or name not in schema.get("required", []), name
        assert ts_shape(annotation) == schema_shape(
            schema["properties"][name], schema.get("$defs", {})
        ), f"Wire type or nullability differs: {model.__name__}.{name}"


def test_new_typed_browser_records_cannot_escape_the_parity_inventory():
    public = set(INTERFACES) - {"Capabilities"}
    assert public == {model.__name__ for model in MODELS}
