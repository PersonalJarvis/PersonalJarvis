"""Exact, team-scoped skill and measured recheck wire contracts."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .swarm_types import Counter


class MeasuredRate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    numerator: Counter | None
    denominator: Counter | None
    rate: float | None = Field(ge=0, le=1)

    @model_validator(mode="after")
    def measured_consistently(self) -> Self:
        if self.numerator is None or self.denominator is None:
            if self.numerator is not None or self.denominator is not None or self.rate is not None:
                raise ValueError("Unmeasured rates must have null counts and a null rate")
        else:
            numerator, denominator = int(self.numerator), int(self.denominator)
            if numerator > denominator:
                raise ValueError("A rate numerator cannot exceed its denominator")
            expected = numerator / denominator if denominator else None
            if self.rate != expected:
                raise ValueError("The displayed rate must match its measured counts")
        return self


class SkillProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str
    level: int = Field(ge=1)
    reliability: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=0.5)
    samples: Counter
    verified_tasks: Counter
    difficulty_counts: dict[str, Counter]
    credits: float = Field(ge=0)
    next_level_credits: float | None = Field(gt=0)
    acceptance: MeasuredRate
    regression: MeasuredRate
    rollback: MeasuredRate


class AgentSkills(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_id: str
    agent_id: str
    level: int = Field(ge=1)
    profiles: list[SkillProfile]
    history: list[dict[str, Any]]
    has_more: bool


class ContributionRecheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    team_id: str
    task_id: str
    contribution_id: str
    state: Literal["passed", "regression", "fabrication", "inconclusive", "unsupported", "running"]
    reason: str
    created_at: float
    finished_at: float | None
    evidence_ids: list[str]
    rating_id: str | None
