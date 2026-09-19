"""Preparation parity across producer, SQL JSON, DTO, TypeScript and UI boundaries."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from jarvis.core.swarm_preparation import (
    PreparationPlan,
    PreparationQuestion,
    PreparationView,
)
from jarvis.core.swarm_types import TaskSpec, TeamCreate
from jarvis.swarm.preparation import HEAD, _begin, _finish, read
from jarvis.swarm.store import TeamRegistry
from tests.integration.test_swarm_preparation import PLAN, QUESTIONS, answers, spec

FRONTEND = Path(__file__).resolve().parents[2] / "jarvis/ui/web/frontend/src/components/swarm"


def interface(name: str, filename: str = "preparationTypes.ts") -> dict[str, tuple[bool, str]]:
    text = (FRONTEND / filename).read_text(encoding="utf-8")
    match = re.search(rf"export interface {re.escape(name)}\s*\{{([^}}]*)\}}", text)
    assert match, f"Missing TypeScript interface: {name}"
    return {
        field: (optional == "?", annotation.strip())
        for field, optional, annotation in re.findall(
            r"(?:^|;)\s*(\w+)(\??)\s*:\s*([^;]+)", match[1]
        )
    }


@pytest.mark.parametrize(
    ("model", "filename"),
    [
        (PreparationQuestion, "preparationTypes.ts"),
        (PreparationPlan, "preparationTypes.ts"),
        (PreparationView, "preparationTypes.ts"),
        (TeamCreate, "types.ts"),
        (TaskSpec, "types.ts"),
    ],
)
def test_preparation_typescript_fields_match_the_serialized_dtos(model, filename):
    fields = interface(model.__name__, filename)
    assert set(fields) == set(model.model_fields)
    for name, (optional, _) in fields.items():
        if optional:
            assert not model.model_fields[name].is_required(), (
                f"Required DTO field made optional: {name}"
            )


def test_preparation_field_types_nullability_and_state_vocabulary_match():
    question = interface("PreparationQuestion")
    assert {name: annotation for name, (_, annotation) in question.items()} == {
        "id": "string",
        "prompt": "string",
        "choices": "string[]",
        "hint": "string",
    }
    plan = interface("PreparationPlan")
    assert plan["tasks"][1] == "TaskSpec[]"
    assert plan["remaining_decomposition"][1] == "boolean"
    assert plan["assumptions"][1] == plan["exclusions"][1] == "string[]"
    view = interface("PreparationView")
    assert view["team"][1] == "TeamRecord"
    assert view["revision"][1] == "number"
    assert view["busy"][1] == "boolean"
    assert view["questions"][1] == "PreparationQuestion[]"
    assert view["answers"][1] == "Record<string, string>"
    assert view["plan"][1] == "PreparationPlan | null"
    text = (FRONTEND / "preparationTypes.ts").read_text(encoding="utf-8")
    literal = re.search(r"export type PreparationState\s*=\s*([^;]+);", text)
    assert literal
    assert set(re.findall(r'"([a-z]+)"', literal[1])) == set(
        get_args(PreparationView.model_fields["state"].annotation)
    )
    assert interface("TeamCreate", "types.ts")["preparation_required"] == (True, "boolean")


def test_optional_question_fields_are_present_after_json_roundtrip():
    value = PreparationQuestion(id="scope", prompt="Which deliverable do you need?")
    wire = json.loads(value.model_dump_json())
    assert wire == {"id": "scope", "prompt": value.prompt, "choices": [], "hint": ""}
    assert set(wire) == set(interface("PreparationQuestion"))
    assert PreparationQuestion.model_validate(wire) == value


def test_stored_preparation_roundtrips_full_plan_without_enlarging_checkpoint(tmp_path):
    request = TeamCreate.model_validate_json(
        spec().model_copy(update={"preparation_required": True}).model_dump_json()
    )
    registry = TeamRegistry(tmp_path)
    team = registry.create(request)
    store = registry.open(team["id"])
    initial = PreparationView.model_validate(read(store))
    assert initial.team.state == "created"
    assert (
        initial.team.checkpoint["preparation"]["required"] is request.preparation_required is True
    )
    controller = store.acquire_controller("wire-contract")
    questions = _finish(
        store, controller, _begin(store, controller, "questions", "", None), QUESTIONS
    )
    ready = _finish(
        store, controller, _begin(store, controller, "answers", "", answers(questions)), PLAN
    )
    with store._tx() as connection:
        record = json.loads(
            connection.execute(
                "SELECT record FROM decisions WHERE request_key=?", (HEAD,)
            ).fetchone()[0]
        )
        team_record = json.loads(
            connection.execute("SELECT record FROM team WHERE singleton=1").fetchone()[0]
        )
    assert record["questions"] == ready["questions"]
    assert record["plan"] == ready["plan"]
    assert PreparationPlan.model_validate(record["plan"]).model_dump(mode="json") == ready["plan"]
    assert record["answers"] == ready["answers"]
    assert record["original_goal"] == request.goal
    assert record["digest"] == ready["digest"]
    dto = PreparationView.model_validate_json(
        json.dumps(
            {
                "team": team_record,
                **{
                    field: record[field]
                    for field in PreparationView.model_fields
                    if field != "team"
                },
            }
        )
    )
    assert dto.model_dump(mode="json") == ready
    assert set(dto.model_dump()) == set(interface("PreparationView"))
    marker = team_record["checkpoint"]["preparation"]
    assert set(marker) == {"required", "revision", "state", "decision_id", "digest"}
    assert len(json.dumps(team_record["checkpoint"]).encode()) < 16384
    assert store.records("tasks") == []


def test_ui_consumes_the_typed_questions_plan_and_approval_fields():
    component = (FRONTEND / "SwarmPreparation.tsx").read_text(encoding="utf-8")
    assert 'import type { PreparationView } from "./preparationTypes"' in component
    for field in PreparationQuestion.model_fields:
        assert re.search(rf"question\.{field}\b", component), (
            f"Question field not presented: {field}"
        )
    for field in PreparationPlan.model_fields:
        assert re.search(rf"view\.plan\.{field}\b", component), f"Plan field not presented: {field}"
    api = (FRONTEND / "preparationApi.ts").read_text(encoding="utf-8")
    assert "expected_revision: view.revision" in api
    assert "digest: view.digest" in api
    assert 'expected_storage_generation: view.team.storage_generation ?? ""' in api
    assert "request_key: requestKey" in api
    for field in ("expected_revision", "expected_storage_generation", "digest", "request_key"):
        assert field in api
