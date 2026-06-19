"""Pydantic models for skillbook persistence.

Wire format for P2P sync (rules) and reflector trace input (traces) lives here.
Dicts are kept open-shape so that the Reflector can emit new strategy kinds
without an upstream code change; concrete strategy validation lives at the
Generator's application site.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Rule(BaseModel):
    """A skillbook delta: trigger + strategy + CRDT metadata."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    id: str
    trigger: dict[str, Any]
    strategy: dict[str, Any]
    source_peer: str
    created_at_ns: int
    priority: int = 0
    deleted: bool = False
    evidence: str = ""


class TraceStep(BaseModel):
    """One execution step recorded by the Generator for later reflection."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    step_idx: int
    actor: str
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    status: str
    ts_ns: int


class Entity(BaseModel):
    """Knowledge-graph node with bi-temporal validity."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    valid_from_ns: int
    valid_to_ns: int | None = None


class Relation(BaseModel):
    """Directed edge between two Entities, also bi-temporal."""

    model_config = ConfigDict(extra="forbid")

    id: str
    src_id: str
    dst_id: str
    kind: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    valid_from_ns: int
    valid_to_ns: int | None = None
