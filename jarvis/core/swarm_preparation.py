"""Owner-facing clarification and immutable launch contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .swarm_types import TaskSpec, TeamRecord, WireModel


class PreparationQuestion(WireModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    prompt: str = Field(min_length=1, max_length=1000)
    choices: list[str] = Field(default_factory=list, max_length=5)
    hint: str = Field(default="", max_length=1000)

    @field_validator("choices")
    @classmethod
    def bounded_choices(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 300 for item in values):
            raise ValueError("Question choices must be short and nonempty")
        return values


class PreparationPlan(WireModel):
    goal: str = Field(min_length=1, max_length=20000)
    acceptance: str = Field(min_length=1, max_length=10000)
    summary: str = Field(min_length=1, max_length=4000)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    exclusions: list[str] = Field(default_factory=list, max_length=12)
    tasks: list[TaskSpec] = Field(min_length=1, max_length=32)
    remaining_decomposition: bool = Field(default=False, strict=True)

    @field_validator("assumptions", "exclusions")
    @classmethod
    def bounded_notes(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 1000 for item in values):
            raise ValueError("Plan notes must be bounded and nonempty")
        return values


class PreparationAnswers(WireModel):
    expected_revision: int = Field(ge=1)
    expected_storage_generation: str = Field(max_length=64)
    answers: dict[str, str] = Field(max_length=3)
    request_key: str = Field(min_length=1, max_length=100)

    @field_validator("answers")
    @classmethod
    def bounded_answers(cls, values: dict[str, str]) -> dict[str, str]:
        if any(not value.strip() or len(value) > 10000 for value in values.values()):
            raise ValueError("Provide a nonempty, bounded response to each question")
        return values


class PreparationBegin(WireModel):
    expected_storage_generation: str = Field(max_length=64)
    request_key: str = Field(min_length=1, max_length=100)


class PreparationLaunch(WireModel):
    expected_revision: int = Field(ge=1)
    expected_storage_generation: str = Field(max_length=64)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_key: str = Field(min_length=1, max_length=100)


class PreparationView(WireModel):
    team: TeamRecord
    revision: int = Field(ge=0)
    state: Literal["clarifying", "planning", "ready", "failed", "launched"]
    busy: bool
    questions: list[PreparationQuestion] = Field(default_factory=list, max_length=3)
    answers: dict[str, str] = Field(default_factory=dict)
    plan: PreparationPlan | None = None
    digest: str = ""
    error: str = ""
