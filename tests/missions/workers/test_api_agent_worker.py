"""Tests for ApiAgentWorker — the in-process agentic worker for grok/openai/
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


def test_supports_api_agent_worker() -> None:
    assert supports_api_agent_worker("grok")
    assert supports_api_agent_worker("openai")
    assert supports_api_agent_worker("openrouter")
    assert not supports_api_agent_worker("claude-api")
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
    worker = ApiAgentWorker("grok")
    log_dir = tmp_path / "_logs"

    events = await _drain(
        worker, prompt="make out.txt", worktree=tmp_path, env={}, job=None,
        worker_id="m::0", log_dir=log_dir, model="grok-4.3",
    )

    kinds = [type(e).__name__ for e in events]
    assert kinds[0] == "ClaudeSystemInit"
    assert kinds[-1] == "ClaudeResult"
    assert "ClaudeAssistantMessage" in kinds and "ClaudeUserMessage" in kinds
    # the worker forwarded the worker tool specs to the brain
    assert "Write" in fake.seen_tools and "Bash" in fake.seen_tools
    # GROUND TRUTH: the file is really on disk
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "RESULT"
    # terminal result is success
    result = events[-1]
    assert result.is_error is False
    # stream.jsonl is Critic-readable: the write is credited
    stream_text = (log_dir / "stream.jsonl").read_text(encoding="utf-8")
    assert "out.txt" in extract_write_targets(stream_text)


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
    worker = ApiAgentWorker("grok")
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
    worker = ApiAgentWorker("grok")
    log_dir = tmp_path / "_logs"
    await _drain(worker, prompt="x", worktree=tmp_path, env={}, job=None,
                 worker_id="m::0", log_dir=log_dir)
    for line in (log_dir / "stream.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)  # every line is valid JSON
