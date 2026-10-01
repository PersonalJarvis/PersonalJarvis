"""Shared, provider-independent Ultra Agent Swarm contracts.

Quantities crossing the wire are decimal strings: JavaScript numbers cannot
represent all durable budget values. Authority handles never enter model prompts
or browser payloads; the store validates their opaque token on every operation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from .swarm_preparation import PreparationAnswers, PreparationBegin, PreparationLaunch


class ProviderUnavailableError(RuntimeError):
    """Provider availability needs attention; task acceptance has not failed."""


def decimal_counter(value: Any) -> str:
    """Normalize exact nonnegative quantities without float coercion."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Use an integer or decimal string for a counter")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or len(text) > 31:
        raise ValueError("Counter must be a nonnegative integer of at most 31 digits")
    return str(int(text))


Counter = Annotated[str, BeforeValidator(decimal_counter)]
ToolName = Annotated[str, Field(min_length=1, max_length=100)]
DomainName = Annotated[str, Field(min_length=1, max_length=253)]


class TeamState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    ARCHIVED = "archived"


class TaskState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class AgentRole(StrEnum):
    LEAD = "lead"
    COORDINATOR = "coordinator"
    WORKER = "worker"


class AgentState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    STOPPED = "stopped"
    FAILED = "failed"
    COMPLETED = "completed"


class PeerIntent(StrEnum):
    COORD_STATUS = "COORD_STATUS"
    REQUEST_PROGRESS = "REQUEST_PROGRESS"
    REPORT_PROGRESS = "REPORT_PROGRESS"
    REQUEST_HELP = "REQUEST_HELP"
    OFFER_HELP = "OFFER_HELP"
    SHARE_FINDING = "SHARE_FINDING"
    CLAIM_WORK = "CLAIM_WORK"
    RELEASE_WORK = "RELEASE_WORK"
    CONFLICT = "CONFLICT"


class VerificationKind(StrEnum):
    REVIEW = "review"
    JAVASCRIPT = "javascript"


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class BudgetLimits(WireModel):
    token_budget: Counter = "250000"  # noqa: S105 - exact quantity, not a credential
    monetary_limit_microusd: Counter | None = None
    concurrency: int = Field(default=3, ge=1, le=10000)
    worker_limit: Counter = "12"
    runtime_seconds: int = Field(default=1800, ge=1, le=31_536_000)
    max_attempts: int = Field(default=3, ge=1, le=20)
    max_output_tokens: int = Field(default=8192, ge=128, le=1_048_576)
    max_tool_calls: int = Field(default=32, ge=1, le=1024)


DEFAULT_SWARM_TOOLS = (
    "fetch_url",
    "run_javascript",
    "write_artifact",
    "read_artifact",
    "search_team",
    "send_message",
    "read_messages",
    "ack_message",
    "register_team_tool",
    "install_dependency",
)


class CapabilityPolicy(WireModel):
    tools: list[ToolName] = Field(default_factory=lambda: list(DEFAULT_SWARM_TOOLS), max_length=100)
    internet: bool = True
    allowed_domains: list[DomainName] = Field(default_factory=list, max_length=100)
    dependency_registries: list[DomainName] = Field(
        default_factory=lambda: ["registry.npmjs.org"],
        max_length=20,
    )
    max_network_bytes: Counter = "20000000"
    max_artifact_bytes: Counter = "50000000"
    allow_dependencies: bool = True

    @field_validator("tools", "allowed_domains", "dependency_registries")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class TaskSpec(WireModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20000)
    acceptance: str = Field(min_length=1, max_length=10000)
    dependencies: list[str] = Field(default_factory=list, max_length=1000)
    domain: str = Field(default="general", min_length=1, max_length=80)
    milestone: str = Field(default="delivery", min_length=1, max_length=100)
    difficulty: int = Field(default=3, ge=1, le=5)
    priority: int = Field(default=5, ge=0, le=9)
    verification: VerificationKind = VerificationKind.REVIEW
    verification_script: str = Field(
        default="",
        max_length=100000,
        description="JavaScript main(input): input.result is the worker's parsed JSON value; "
        "input.text is its original reply; input.artifacts are recorded evidence. "
        "Return {accepted: boolean, reason: string}.",
    )
    required_tools: list[str] = Field(default_factory=list, max_length=100)
    independent_verification: bool = False


class TeamCreate(WireModel):
    preparation_required: bool = False
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=20000)
    acceptance: str = Field(default="", max_length=10000)
    limits: BudgetLimits = Field(default_factory=BudgetLimits)
    policy: CapabilityPolicy = Field(default_factory=CapabilityPolicy)
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=1000)
    request_key: str = Field(min_length=1, max_length=100)
    mode: str = Field(default="local", pattern=r"^(local|distributed)$")


class TeamRecord(WireModel):
    storage_generation: str = Field(default="", max_length=64)
    id: str
    name: str
    goal: str
    acceptance: str = ""
    lead_id: str
    state: TeamState
    version: int
    created_at: float
    updated_at: float
    started_at: float | None = None
    reason: str = ""
    limits: BudgetLimits
    policy: CapabilityPolicy
    tokens_used: Counter = "0"
    tokens_reserved: Counter = "0"
    cost_microusd: Counter = "0"
    cost_reserved_microusd: Counter = "0"
    network_bytes: Counter = "0"
    mode: str = "local"
    checkpoint: dict[str, Any] = Field(default_factory=dict)


class TeamUnavailable(WireModel):
    """Known catalog identity whose current team state cannot be read."""

    id: str
    name: str
    created_at: float
    available: Literal[False] = False
    error: str


TeamListItem = TeamRecord | TeamUnavailable


class CheckpointSnapshot(WireModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    kind: Literal["checkpoint_snapshot"] = "checkpoint_snapshot"
    team_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    checkpoint_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    version: Counter
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: str = Field(max_length=80)
    created_at: float = Field(ge=0)
    checkpoint: dict[str, Any]

    @field_validator("version")
    @classmethod
    def positive_version(cls, value: str) -> str:
        if int(value) < 1:
            raise ValueError("Checkpoint versions start at one")
        return value


class AgentRecord(WireModel):
    source_agent_id: str | None = None
    id: str
    team_id: str
    name: str
    role: AgentRole
    state: AgentState = AgentState.IDLE
    domain: str = "general"
    group_id: str = "delivery"
    task_id: str | None = None
    generation: int = 1
    level: int = 1
    reliability: float = 0.5
    verified_tasks: Counter = "0"
    tool_activity: str | None = None


class TaskRecord(WireModel):
    id: str
    team_id: str
    title: str
    description: str
    acceptance: str
    dependencies: list[str]
    domain: str
    milestone: str
    difficulty: int
    priority: int
    verification: VerificationKind
    verification_script: str = ""
    required_tools: list[str] = Field(default_factory=list)
    independent_verification: bool = False
    state: TaskState
    owner_id: str | None = None
    attempt_count: int = 0
    fence: int = 0
    version: int = 1
    result: str = ""
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    created_at: float
    updated_at: float


class PeerMessage(WireModel):
    intent: PeerIntent
    task_id: str
    summary: str = Field(min_length=1, max_length=2000)
    recipients: list[str] = Field(default_factory=list, max_length=16)
    topic: str = Field(default="", max_length=100)
    correlation_id: str = Field(default="", max_length=100)
    evidence: list[str] = Field(default_factory=list, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    deadline: float | None = None
    priority: int = Field(default=5, ge=0, le=9)
    request_key: str = Field(min_length=1, max_length=100)


class ActivityRecord(WireModel):
    id: str
    team_id: str
    seq: Counter
    kind: str
    summary: str
    agent_id: str | None = None
    task_id: str | None = None
    trace_id: str
    created_at: float
    data: dict[str, Any] = Field(default_factory=dict)


class WorldGroup(WireModel):
    id: str
    title: str
    counts: dict[str, Counter]
    agents: Counter
    level: int = 1


class WorldSnapshot(WireModel):
    team: TeamRecord
    revision: Counter
    agents: list[AgentRecord]
    tasks: list[TaskRecord]
    groups: list[WorldGroup]
    activity: list[ActivityRecord]
    counts: dict[str, Counter]
    aggregated: bool
    has_more: bool = False


@dataclass(frozen=True, slots=True)
class SwarmActor:
    """Opaque server-issued worker authority; token is never serialized."""

    team_id: str
    agent_id: str
    token: str
    task_id: str = ""
    task_fence: int = 0


@dataclass(frozen=True, slots=True)
class SwarmController:
    """Durable lease identity, fencing number and opaque lease credential."""

    team_id: str
    instance_id: str
    fence: int
    token: str


@dataclass(frozen=True, slots=True)
class SandboxResult:
    output: Any
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float


class SwarmSandbox(Protocol):
    async def run(
        self,
        script: str,
        inputs: Any,
        *,
        timeout_s: float = 10,
        cancel: Callable[[], bool] | None = None,
    ) -> SandboxResult: ...


class SwarmBrief(WireModel):
    """An ordinary specialist's proposal; it contains no execution authority."""

    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=8000)
    acceptance: str = Field(min_length=1, max_length=4000)
    authorized_input: str = Field(default="", max_length=16000)
    request_key: str = Field(min_length=1, max_length=100)


class SwarmProfiles(Protocol):
    async def profile(self, source_agent_id: str) -> dict[str, Any]: ...


class SwarmRequests(Protocol):
    async def request_swarm(
        self, source_agent_id: str, brief: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def request_status(self, source_agent_id: str, request_id: str) -> dict[str, Any]: ...


class SwarmService(Protocol):
    """UI/control-plane boundary. Worker tools use independently bound scopes."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def capabilities(self) -> dict[str, Any]: ...
    async def create_team(self, spec: TeamCreate) -> dict[str, Any]: ...
    async def create_preparation(self, spec: TeamCreate) -> dict[str, Any]: ...
    async def preparation(self, team_id: str) -> dict[str, Any]: ...
    async def begin_preparation(self, team_id: str, body: PreparationBegin) -> dict[str, Any]: ...
    async def answer_preparation(
        self, team_id: str, body: PreparationAnswers
    ) -> dict[str, Any]: ...
    async def launch_preparation(self, team_id: str, body: PreparationLaunch) -> dict[str, Any]: ...
    async def list_teams(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]: ...
    async def team(self, team_id: str) -> dict[str, Any]: ...
    async def control(
        self,
        team_id: str,
        action: str,
        *,
        expected_version: int | None = None,
        expected_storage_generation: str | None = None,
    ) -> dict[str, Any]: ...
    async def world(self, team_id: str, *, group: str = "") -> dict[str, Any]: ...
    async def skills(
        self, team_id: str, agent_id: str, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]: ...
    async def recheck(self, team_id: str, task_id: str, request_key: str) -> dict[str, Any]: ...
    async def records(
        self,
        team_id: str,
        kind: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
