"""Public logical travel messages, deliberately independent of task execution."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from .models import ContractModel, Identity


class TravelMode(StrEnum):
    PEDESTRIAN = "pedestrian"
    ROVER = "rover"


class NavigationState(StrEnum):
    QUEUEING = "queueing"
    MOVING = "moving"
    TEMPORARILY_BLOCKED = "temporarily_blocked"
    REROUTING = "rerouting"
    ARRIVED = "arrived"
    UNREACHABLE = "unreachable"
    CANCELED = "canceled"


NAVIGATION_TERMINAL = frozenset(
    {NavigationState.ARRIVED, NavigationState.UNREACHABLE, NavigationState.CANCELED}
)


class MoveCommand(ContractModel):
    world_id: Literal["mars:ordinary"] = "mars:ordinary"
    schema_version: Literal[1] = 1
    layout_version: int = Field(default=1, ge=1)
    graph_version: int = Field(default=1, ge=1)
    request_id: Identity
    station_id: Identity
    mode: TravelMode = TravelMode.PEDESTRIAN

    @field_validator("schema_version", "layout_version", "graph_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("navigation versions must be integers")
        return value


class PedestrianMoveCommand(MoveCommand):
    """The public visit API does not yet expose vehicle control."""

    mode: Literal[TravelMode.PEDESTRIAN] = TravelMode.PEDESTRIAN


class NavigationRecord(ContractModel):
    command_id: Identity
    request_id: Identity
    agent_id: Identity
    trace_id: Identity
    world_id: Literal["mars:ordinary"] = "mars:ordinary"
    schema_version: Literal[1] = 1
    layout_version: int
    graph_version: int
    graph_signature: str
    station_id: Identity
    mode: TravelMode
    state: NavigationState
    # Positions are logical graph coordinates, not client animation observations.
    position: tuple[float, float, float]
    current_node: Identity
    edge_id: Identity | None = None
    next_node: Identity | None = None
    edge_progress: float = Field(default=0, ge=0, le=1)
    path: tuple[str, ...] = Field(default=(), max_length=512)
    placement: Literal["canonical_spawn", "persisted"]
    # A spawn queue position is an approach coordinate, not a materialized body.
    # Missing fields in old durable records conservatively mean physically placed.
    presence: Literal["spawn_queue", "placed"] = "placed"
    created_ms: int
    updated_ms: int
    last_progress_ms: int
    deadline_ms: int
    retry_at_ms: int = 0
    retries: int = 0
    reason: str = ""


class NavigationLease(ContractModel):
    resource_id: str
    command_id: Identity
    agent_id: Identity
    expires_ms: int


class NavigationOccupancy(ContractModel):
    """Physical placement persists independently of command/intent lease lifetime."""

    resource_id: str
    command_id: Identity
    agent_id: Identity
    position: tuple[float, float, float]
    graph_signature: str


class NavigationSnapshot(ContractModel):
    world_id: Literal["mars:ordinary"] = "mars:ordinary"
    schema_version: Literal[1] = 1
    graph_version: int
    graph_signature: str
    seq: int
    commands: tuple[NavigationRecord, ...]
    leases: tuple[NavigationLease, ...]
    occupancies: tuple[NavigationOccupancy, ...] = ()
