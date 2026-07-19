"""Tests for ApiAgentWorker — the in-process agentic worker for openai/
openrouter. Uses a scripted FakeBrain so no network call is made; the real
provider is exercised by the live probe, not the unit suite."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.missions.stream_evidence import extract_write_targets
from jarvis.missions.workers.api_agent_worker import (
    ApiAgentWorker,
    supports_api_agent_worker,
)


class FakeBrain:
    """Yields a scripted sequence of BrainDelta lists, one per complete() turn."""

    def __init__(self, turns: list[list[BrainDelta]]) -> None:
        self._turns = turns
        self.calls = 0
        self.seen_tools: list[str] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        # record that the worker passed the tool specs through
        self.seen_tools = [t["name"] for t in req.tools]
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for d in turn:
            yield d


def _patch_brain(monkeypatch: pytest.MonkeyPatch, brain: FakeBrain) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.api_agent_worker._build_brain",
        lambda provider, model: brain,
    )


async def _drain(worker: ApiAgentWorker, **kw):  # noqa: ANN003
    events = []
    async for ev in worker.spawn(**kw):
        events.append(ev)
    return events


@pytest.fixture(autouse=True)
def _reset_family_cooldowns() -> None:
    """The per-family cooldown is a process global — reset around every test."""
    from jarvis.api_family_quota_state import clear_api_family_cooldown

    for prov in ("openai", "openrouter", "gemini", "claude-api", "grok"):
        clear_api_family_cooldown(prov)
    yield
    for prov in ("openai", "openrouter", "gemini", "claude-api", "grok"):
        clear_api_family_cooldown(prov)


def test_supports_api_agent_worker() -> None:
    assert supports_api_agent_worker("openai")
    assert supports_api_agent_worker("openrouter")
    assert supports_api_agent_worker("grok")
    # Groq remains STT-only and must not appear as an API-agent brain.
    assert not supports_api_agent_worker("groq")
    # claude-api and gemini are now in-process api-agent workers (B3/B4, 2026-06-29)
    assert supports_api_agent_worker("claude-api")
    assert not supports_api_agent_worker("antigravity")
    assert not supports_api_agent_worker(None)


@pytest.mark.asyncio
async def test_worker_writes_file_and_emits_critic_readable_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Write tool_call is executed (real file on disk), the frames are emitted
    in the claude shape, and the stream.jsonl is credited by extract_write_targets."""
    turns = [
        # turn 0: model asks to Write a file
        [
            BrainDelta(tool_call={"id": "c1", "name": "Write",
                                  "input": {"file_path": "out.txt", "content": "RESULT"}}),
            BrainDelta(finish_reason="tool_use"),
        ],
        # turn 1: model is satisfied, finishes with text, no tool_call
        [BrainDelta(content="Created out.txt."), BrainDelta(finish_reason="end_turn")],
    ]
    fake = FakeBrain(turns)
    _patch_brain(monkeypatch, fake)
    worker = ApiAgentWorker("openai")
    log_dir = tmp_path / "_logs"

    events = await _drain(
        worker, prompt="make out.txt", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=log_dir, model="gpt-5.5",
    )

    kinds = [type(e).__name__ for e in events]
    assert kinds[0] == "ClaudeSystemInit"
    assert kinds[-1] == "ClaudeResult"
    assert "ClaudeAssistantMessage" in kinds and "ClaudeUserMessage" in kinds
    # the worker forwarded the worker tool specs to the brain
    assert "Write" in fake.seen_tools and "RunCommand" in fake.seen_tools
    # GROUND TRUTH: the file is really on disk
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "RESULT"
    # terminal result is success
    result = events[-1]
    assert result.is_error is False
    # stream.jsonl is Critic-readable: the write is credited
    stream_text = (log_dir / "stream.jsonl").read_text(encoding="utf-8")
    assert "out.txt" in extract_write_targets(stream_text)


@pytest.mark.asyncio
async def test_worker_provider_call_uses_scoped_agent_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jarvis.core import config as cfg

    observed: list[str | None] = []

    class _CredentialReadingBrain:
        def can_call_tools(self) -> bool:
            return True

        async def complete(self, req):  # noqa: ANN001, ANN201
            observed.append(cfg.get_provider_secret("openai"))
            yield BrainDelta(content="done")

    monkeypatch.setattr(
        cfg,
        "get_secret",
        lambda key, *args, **kwargs: {
            "jarvis_agent_openai_api_key": "agent-key",
            "openai_api_key": "brain-key",
            "realtime_openai_api_key": "realtime-key",
        }.get(key),
    )
    monkeypatch.setattr(
        "jarvis.missions.workers.api_agent_worker._build_brain",
        lambda provider, model: _CredentialReadingBrain(),
    )

    events = await _drain(
        ApiAgentWorker("openai"),
        prompt="t",
        worktree=tmp_path,
        env={},
        job=None,
        worker_id="m::0",
        log_dir=tmp_path / "_logs",
        model="gpt-5.5",
    )

    assert events[-1].is_error is False
    assert observed == ["agent-key"]
    assert cfg.get_provider_secret("openai") == "brain-key"


@pytest.mark.asyncio
async def test_worker_run_command_is_async_and_mission_contained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The provider loop uses direct argv and assigns the process to its job."""
    (tmp_path / "build.py").write_text("print('build-ok')\n", encoding="utf-8")
    turns = [
        [
            BrainDelta(
                tool_call={
                    "id": "run-1",
                    "name": "RunCommand",
                    "input": {"program": "python", "args": ["build.py"]},
                }
            ),
            BrainDelta(finish_reason="tool_use"),
        ],
        [BrainDelta(content="Build complete."), BrainDelta(finish_reason="end_turn")],
    ]
    fake = FakeBrain(turns)
    _patch_brain(monkeypatch, fake)

    class _Job:
        def __init__(self) -> None:
            self.assigned: list[int] = []

        def assign(self, pid: int) -> None:
            self.assigned.append(pid)

    job = _Job()
    events = await _drain(
        ApiAgentWorker("openai"),
        prompt="run the build",
        worktree=tmp_path,
        env={},
        job=job,
        worker_id="m::0",
        log_dir=tmp_path / "_logs",
        model="gpt-5.5",
    )

    tool_results = [
        block
        for event in events
        if type(event).__name__ == "ClaudeUserMessage"
        for block in event.message["content"]
    ]
    assert tool_results[0]["content"] == "build-ok"
    assert tool_results[0]["is_error"] is False
    assert len(job.assigned) == 1
    assert events[-1].is_error is False


class _RaisingBrain:
    """A brain whose complete() dies with a provider error (429/401/...)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def can_call_tools(self) -> bool:
        return True

    async def complete(self, req):  # noqa: ANN001, ANN201
        raise self._exc
        yield  # pragma: no cover — makes this an async generator


@pytest.mark.asyncio
async def test_quota_depleted_error_arms_family_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mission 019f3d0f (2026-07-07): gemini's prepaid credits were depleted
    (429 RESOURCE_EXHAUSTED) and every retry re-picked gemini. A quota/auth
    provider error must arm the per-family cooldown so the factory's family
    walk skips this family on the retry and reaches the next healthy key."""
    from jarvis.api_family_quota_state import api_family_in_cooldown

    monkeypatch.setattr(
        "jarvis.missions.workers.api_agent_worker._build_brain",
        lambda provider, model: _RaisingBrain(
            RuntimeError(
                "429 Too Many Requests. Your prepayment credits are depleted. "
                "RESOURCE_EXHAUSTED"
            )
        ),
    )
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret", lambda p: "DEAD-GEMINI-KEY"
    )
    worker = ApiAgentWorker("gemini")

    events = await _drain(
        worker, prompt="t", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs", model="gemini-3.5-flash",
    )

    assert events[-1].is_error is True
    from jarvis.claude_auth_state import credential_fingerprint

    assert (
        api_family_in_cooldown(
            "gemini", current_fingerprint=credential_fingerprint("DEAD-GEMINI-KEY")
        )
        is True
    )


@pytest.mark.asyncio
async def test_dead_key_401_arms_family_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mission 019f3d01: a stale claude-api credential 401'd on every retry.
    An auth error is family-unusable just like a quota error — arm the cooldown."""
    from jarvis.api_family_quota_state import api_family_in_cooldown

    monkeypatch.setattr(
        "jarvis.missions.workers.api_agent_worker._build_brain",
        lambda provider, model: _RaisingBrain(
            RuntimeError(
                "Error code: 401 - {'type': 'error', 'error': {'type': "
                "'authentication_error', 'message': 'invalid x-api-key'}}"
            )
        ),
    )
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret", lambda p: "STALE-KEY"
    )
    worker = ApiAgentWorker("claude-api")

    events = await _drain(
        worker, prompt="t", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs", model="claude-opus-4-8",
    )

    assert events[-1].is_error is True
    assert api_family_in_cooldown("claude-api") is True


@pytest.mark.asyncio
async def test_plain_worker_error_does_not_arm_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-quota, non-auth crash must NOT block the family — the key is fine."""
    from jarvis.api_family_quota_state import api_family_in_cooldown

    monkeypatch.setattr(
        "jarvis.missions.workers.api_agent_worker._build_brain",
        lambda provider, model: _RaisingBrain(RuntimeError("kaboom: flaky socket")),
    )
    worker = ApiAgentWorker("openrouter")

    events = await _drain(
        worker, prompt="t", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs", model="m",
    )

    assert events[-1].is_error is True
    assert api_family_in_cooldown("openrouter") is False


@pytest.mark.asyncio
async def test_success_clears_family_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful run proves the key works again — the cooldown must clear."""
    from jarvis.api_family_quota_state import (
        api_family_in_cooldown,
        mark_api_family_cooldown,
    )

    mark_api_family_cooldown("openai")
    turns = [[BrainDelta(content="done"), BrainDelta(finish_reason="end_turn")]]
    _patch_brain(monkeypatch, FakeBrain(turns))
    worker = ApiAgentWorker("openai")

    events = await _drain(
        worker, prompt="t", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs", model="gpt-5.5",
    )

    assert events[-1].is_error is False
    assert api_family_in_cooldown("openai") is False


@pytest.mark.asyncio
async def test_worker_tool_error_is_fed_back_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failing tool_call (read missing file) becomes a tool_result with
    is_error, the loop continues, and the worker still finishes."""
    turns = [
        [BrainDelta(tool_call={"id": "c1", "name": "Read", "input": {"file_path": "nope.txt"}}),
         BrainDelta(finish_reason="tool_use")],
        [BrainDelta(content="Could not read; done."), BrainDelta(finish_reason="end_turn")],
    ]
    _patch_brain(monkeypatch, FakeBrain(turns))
    worker = ApiAgentWorker("openai")
    events = await _drain(
        worker, prompt="x", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs",
    )
    user_msgs = [e for e in events if type(e).__name__ == "ClaudeUserMessage"]
    assert user_msgs
    block = user_msgs[0].message["content"][0]
    assert block["type"] == "tool_result" and block["is_error"] is True
    assert events[-1].is_error is False  # mission still finishes


@pytest.mark.asyncio
async def test_unknown_provider_returns_clean_error(tmp_path: Path) -> None:
    """An unsupported provider yields an error ClaudeResult (so the orchestrator
    can fall back) instead of raising."""
    worker = ApiAgentWorker("definitely-not-a-provider")
    events = await _drain(
        worker, prompt="x", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs",
    )
    assert events[-1].is_error is True


@pytest.mark.asyncio
async def test_init_failure_revokes_worker_tool_grant(tmp_path: Path) -> None:
    """An early provider failure must not leave a live bearer grant behind."""

    class _Binding:
        tool_specs: tuple[dict[str, object], ...] = ()
        closed = False

        def close(self) -> None:
            self.closed = True

    class _Inventory:
        def __init__(self, binding: _Binding) -> None:
            self.binding = binding

        def bind_broker(self, **_kwargs):  # noqa: ANN003, ANN201
            return self.binding

        def report_for(self, _backend: str, **_kwargs):  # noqa: ANN003, ANN201
            return {}

    binding = _Binding()
    worker = ApiAgentWorker(
        "definitely-not-a-provider",
        capability_inventory=_Inventory(binding),  # type: ignore[arg-type]
    )

    await _drain(
        worker, prompt="x", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=tmp_path / "_logs",
    )

    assert binding.closed is True


@pytest.mark.asyncio
async def test_stream_jsonl_is_valid_ndjson(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    turns = [
        [BrainDelta(tool_call={"id": "c1", "name": "Write",
                               "input": {"file_path": "a.txt", "content": "x"}}),
         BrainDelta(finish_reason="tool_use")],
        [BrainDelta(content="done"), BrainDelta(finish_reason="end_turn")],
    ]
    _patch_brain(monkeypatch, FakeBrain(turns))
    worker = ApiAgentWorker("openai")
    log_dir = tmp_path / "_logs"
    await _drain(worker, prompt="x", worktree=tmp_path, env={}, job=None,
                 worker_id="m::0", log_dir=log_dir)
    for line in (log_dir / "stream.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)  # every line is valid JSON


@pytest.mark.asyncio
async def test_stream_jsonl_is_isolated_per_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A new worker attempt must replace evidence from the prior iteration."""
    _patch_brain(
        monkeypatch,
        FakeBrain([[BrainDelta(content="current spawn"), BrainDelta(finish_reason="end_turn")]]),
    )
    log_dir = tmp_path / "_logs"
    log_dir.mkdir()
    stream_path = log_dir / "stream.jsonl"
    stream_path.write_text('{"stale":"prior spawn"}\n', encoding="utf-8")

    await _drain(
        ApiAgentWorker("openai"),
        prompt="x",
        worktree=tmp_path,
        env={},
        job=None,
        worker_id="m::1",
        log_dir=log_dir,
    )

    stream_text = stream_path.read_text(encoding="utf-8")
    assert "prior spawn" not in stream_text
    assert "current spawn" in stream_text
