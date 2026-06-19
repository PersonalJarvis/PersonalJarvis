"""ACE wire-format models: Task, TaskResult, Verdict."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from skillbook.guardrails.diagnostics import Diagnostic


class TaskStatus(StrEnum):
    OK = "ok"
    BLOCKED_BY_GUARDRAIL = "blocked_by_guardrail"


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    intent: str
    actor: str
    params: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    task_id: str
    status: TaskStatus
    result: dict[str, Any] | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    rule_applied: str | None = None


class Verdict(BaseModel):
    """Output of the Recursive Reflector's sandbox run.

    Wire format: a single JSON line on the subprocess stdout. ``rule`` is the
    Curator's input; ``evidence`` flows into the Reflector audit log.
    """

    model_config = ConfigDict(extra="forbid")

    outcome: str  # "success" | "failure" | "no_action"
    evidence: str
    rule: dict[str, Any] | None = None
