"""Versioned, secret-free machine and execution contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1
LEASE_SECONDS = 30
MachineOperation = Literal["shell", "read", "write", "list", "manifest", "desktop", "agent_turn"]
MachineOS = Literal["windows", "macos", "linux"]


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    CANCELLED = "cancelled"


class MachineCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    os: MachineOS
    shell: bool = True
    files: bool = True
    agent_runtime: bool = False
    desktop: bool = False
    isolated_desktop: bool = False
    workspace_isolation: bool = False
    shell_name: str = ""
    reason: str = ""


class MachineGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=100)
    machine_id: str = Field(min_length=1, max_length=100)
    shell: bool = False
    files: bool = False
    desktop: Literal["none", "own", "attached"] = "none"
    scope: Literal["workspace", "account"] = "workspace"
    workspace: str = Field(min_length=1, max_length=4096)


class MachineCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    agent_id: str
    trace_id: str
    operation: MachineOperation
    args: dict[str, Any]
    grant: MachineGrant
    timeout_s: float = Field(default=120, gt=0, le=900, allow_inf_nan=False)


class MachineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    success: bool
    output: Any = None
    error: str | None = None
    uncertain: bool = False
