"""Credential-free proof of the canonical Realtime-to-mission path.

The test deliberately keeps the production orchestration seams intact:
Realtime transcript planning, the single ``jarvis_action`` facade,
``BrainManager``, ``ToolExecutor``, ``SpawnWorkerTool``, ``MissionManager``,
and ``Kontrollierer`` all run normally. Only the external worker and critic
model calls are replaced with deterministic in-process fakes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainTierConfig, JarvisConfig
from jarvis.missions.budget import BudgetTracker
from jarvis.missions.critic.verdict import (
    REQUIRED_AXES,
    CriticAxis,
    CriticVerdict,
)
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.kontrollierer.orchestrator import Kontrollierer
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from jarvis.plugins.tool.spawn_worker import SpawnWorkerTool
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession
from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor


@dataclass(slots=True)
class _WorkerResult:
    """Minimal successful stream event consumed by ``Kontrollierer``."""

    type: str = "result"
    cost_usd: float = 0.0
    total_tokens: int = 1
    session_id: str = "credential-free-worker"


class _FakeWorker:
    """Deterministic worker that never starts a process or uses a credential."""

    cli = "python"

    def __init__(self) -> None:
        self.last_pid = 12345
        self.spawn_calls: list[dict[str, Any]] = []

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        **kwargs: Any,
    ) -> AsyncIterator[_WorkerResult]:
        del env, job
        self.spawn_calls.append(
            {
                "prompt": prompt,
                "worktree": worktree,
                "worker_id": worker_id,
                **kwargs,
            }
        )
        await asyncio.to_thread(log_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            (log_dir / "stream.jsonl").write_text,
            '{"type":"result","subtype":"success"}\n',
            encoding="utf-8",
        )
        yield _WorkerResult()


class _FakeCritic:
    """Deterministic critic that proves the supervised review seam ran."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> CriticVerdict:
        self.calls.append(kwargs)
        return CriticVerdict(
            verdict="approve",
            axes={
                axis: CriticAxis(status="pass", evidence=["proof.txt:1"]) for axis in REQUIRED_AXES
            },
            issues=[],
            correction_instruction="",
            summary="The deterministic mission result is approved.",
            summary_de="The deterministic mission result is approved.",
            confidence=1.0,
            suggested_next_action="accept",
        )


class _FakeDecomposer:
    async def decompose(self, prompt: str) -> MissionPlan:
        return MissionPlan(
            steps=[Step(slug="proof", prompt=prompt)],
            n_workers=1,
            expected_output="A deterministic proof artifact.",
        )


class _FakeJob:
    async def __aenter__(self) -> _FakeJob:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    def assign(self, pid: int) -> None:
        del pid


class _FakeWorktreeManager:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._counter = 0

    def create(self, *, task_id: str, **kwargs: Any) -> Path:
        del kwargs
        self._counter += 1
        worktree = self._root / f"worker-{self._counter}-{task_id[:8]}"
        worktree.mkdir(parents=True, exist_ok=True)
        return worktree

    def remove(self, path: Path, **kwargs: Any) -> None:
        del path, kwargs


class _RecordingMissionManager(MissionManager):
    """Real manager with a test witness for IDs entering ``dispatch``."""

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.dispatched_ids: list[str] = []

    async def dispatch(self, **kwargs: Any) -> str:
        mission_id = await super().dispatch(**kwargs)
        self.dispatched_ids.append(mission_id)
        return mission_id


class _RealtimeWireSession:
    """Provider wire with no network, audio device, GPU, or external binary."""

    session_id = "credential-free-realtime"
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = False

    def __init__(self, events: tuple[RealtimeEvent, ...]) -> None:
        self._events = events
        self.session_updates: list[dict[str, Any]] = []
        self.required_tools: list[str | None] = []
        self.text_inputs: list[str] = []
        self.tool_results: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    async def send_audio(self, chunk: Any) -> None:
        del chunk

    async def receive(self) -> AsyncIterator[RealtimeEvent]:
        for event in self._events:
            yield event
            await asyncio.sleep(0)

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: Any = None,
    ) -> None:
        self.session_updates.append(
            {"instructions": instructions, "language": language, "tools": tools}
        )

    async def request_response(self, *, required_tool: str | None = None) -> None:
        self.required_tools.append(required_tool)

    async def send_text(self, text: str) -> None:
        self.text_inputs.append(text)

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self) -> None:
        return None

    async def send_tool_result(
        self,
        call_id: str,
        name: str,
        result: dict[str, Any],
    ) -> None:
        self.tool_results.append((call_id, name, result))

    async def close(self) -> None:
        self.closed = True


class _RealtimeProvider:
    name = "credential-free-realtime"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, events: tuple[RealtimeEvent, ...]) -> None:
        self._events = events
        self.opened_with: Any = None
        self.session: _RealtimeWireSession | None = None

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _RealtimeWireSession:
        self.opened_with = config
        self.session = _RealtimeWireSession(self._events)
        return self.session


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Timed out waiting for the canonical mission path.")
        await asyncio.sleep(0.01)


async def _wait_for_state(
    manager: MissionManager,
    mission_id: str,
    expected: MissionState,
    *,
    timeout_s: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        view = await manager.mission(mission_id)
        if view is not None and view.state is expected:
            return
        if asyncio.get_running_loop().time() >= deadline:
            current = view.state.value if view is not None else "missing"
            raise TimeoutError(
                f"Timed out waiting for mission state {expected.value}; current state is {current}."
            )
        await asyncio.sleep(0.01)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_realtime_delegate_reaches_supervised_mission_without_legacy_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spoken mission request reaches Worker and Critic through one facade."""
    request = "Spawn a Jarvis-Agent to create a deterministic proof artifact."

    # Router selection is fail-closed: Realtime sees one stable facade, and the
    # classic router can select only the supervised mission entry point.
    assert "dispatch-with-review" not in ROUTER_TOOLS
    assert "spawn-worker" in ROUTER_TOOLS

    config = JarvisConfig()
    config.brain.reply_language = "en"
    config.brain.worker = BrainTierConfig(provider="credential-free-worker")
    config.voice.realtime_tool_mode = "delegate"

    manager = _RecordingMissionManager(tmp_path / "missions.db")
    await manager.start()
    worker = _FakeWorker()
    critic = _FakeCritic()
    kontrollierer = Kontrollierer(
        manager=manager,
        decomposer=_FakeDecomposer(),
        critic_runner=critic,
        worktree_mgr=_FakeWorktreeManager(tmp_path / "worktrees"),
        env_builder=lambda _path: {},
        budget=BudgetTracker(per_mission_usd=1.0, daily_usd=1.0),
        worker_factory=lambda _step: worker,
        job_factory=_FakeJob,
        isolation_root=tmp_path / "mission-output",
    )

    bus = EventBus()
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(config.safety),
        ApprovalWorkflow(bus),
    )
    spawn_tool = SpawnWorkerTool(
        bus=bus,
        manager=manager,
        kontrollierer=kontrollierer,
    )
    brain = BrainManager(
        config=config,
        bus=bus,
        tools={"spawn_worker": spawn_tool},
        tool_executor=executor,
    )
    brain._registry._loaded = True
    monkeypatch.setattr(
        "jarvis.brain.factory.is_worker_bootstrap_failed",
        lambda: False,
    )

    provider = _RealtimeProvider(
        (RealtimeEvent(type="input_transcript", text=request, is_final=True),)
    )
    session = RealtimeVoiceSession(
        session_id="canonical-mission-path",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=config,
        brain=brain,
    )

    try:
        await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
        await session.wait_finished()
        await _wait_until(lambda: bool(manager.dispatched_ids))
        mission_id = manager.dispatched_ids[0]
        await _wait_until(
            lambda: bool(critic.calls),
        )

        await _wait_for_state(manager, mission_id, MissionState.APPROVED)

        view = await manager.mission(mission_id)
        assert view is not None
        assert view.state is MissionState.APPROVED

        declared_names = [tool["name"] for tool in provider.opened_with.tools]
        assert declared_names == ["jarvis_action", "end_call"]
        assert provider.session is not None
        assert provider.session.required_tools == []
        assert provider.session.text_inputs
        assert "<trusted_action_result>" in provider.session.text_inputs[-1]

        assert len(manager.dispatched_ids) == 1
        assert len(worker.spawn_calls) == 1
        assert "create a deterministic proof artifact" in worker.spawn_calls[0]["prompt"]
        assert len(critic.calls) == 1
    finally:
        await session.end(reason="test")
        await manager.stop()
