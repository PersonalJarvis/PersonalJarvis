"""Tests for the ``create_artifact`` router tool.

The tool is a dispatcher: validate, brief, hand to the mission stack, promise
aloud. What is worth pinning is each seam — the brief reaches the manager
verbatim, the Kontrollierer actually runs the mission (BUG-016's lesson), the
UI is moved to the Artifacts section, the call returns at once with a
background marker, and every dead end becomes a spoken failure rather than a
log line (AU-11). Fakes, not mocks (project convention).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.events import (
    JarvisAgentAnnouncement,
    JarvisAgentBackgroundCompleted,
    NavigateSidebar,
)
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.create_artifact import CreateArtifactTool


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


class _FakeManager:
    def __init__(self) -> None:
        self.dispatched: list[dict[str, Any]] = []

    async def dispatch(self, *, prompt: str, language: str, source_actor: str) -> str:
        self.dispatched.append(
            {"prompt": prompt, "language": language, "source_actor": source_actor}
        )
        return f"mission-{len(self.dispatched)}"


class _FakeKontrollierer:
    def __init__(self, *, fail: bool = False) -> None:
        self.ran: list[str] = []
        self._fail = fail

    async def run_mission(self, mission_id: str) -> None:
        if self._fail:
            raise RuntimeError("worker exploded")
        self.ran.append(mission_id)


def _ctx(
    utterance: str = "mach mir ein dashboard",  # i18n-allow: DE test vocabulary
    language: str = "de",
) -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance=utterance,
        config={"output_language": language},
        memory_read=None,
    )


async def _settle() -> None:
    """Let the fire-and-forget task run to completion."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_dispatches_the_brief_and_runs_the_mission() -> None:
    bus, manager, kontrollierer = _FakeBus(), _FakeManager(), _FakeKontrollierer()
    tool = CreateArtifactTool(bus, manager=manager, kontrollierer=kontrollierer)

    result = await tool.execute(
        {"title": "Umsatz-Dashboard", "request": "Umsatz pro Monat 2026 als Balken."},  # i18n-allow
        _ctx(),
    )
    await _settle()

    assert result.success is True
    assert result.artifacts[0]["background_task"] is True  # type: ignore[index]
    assert result.artifacts[0]["artifact_file"] == "umsatz-dashboard.html"  # type: ignore[index]
    # The spoken promise names the artifact and is in the turn's language.
    assert "Umsatz-Dashboard" in result.output and "Hintergrund" in result.output  # i18n-allow

    assert len(manager.dispatched) == 1
    prompt = manager.dispatched[0]["prompt"]
    assert "Exactly ONE self-contained HTML file named `umsatz-dashboard.html`" in prompt
    assert "Umsatz pro Monat 2026 als Balken." in prompt  # i18n-allow
    assert manager.dispatched[0]["language"] == "de"
    assert kontrollierer.ran == ["mission-1"]

    kinds = [type(e) for e in bus.published]
    assert JarvisAgentAnnouncement in kinds
    nav = next(e for e in bus.published if isinstance(e, NavigateSidebar))
    assert nav.section == "visualization"


@pytest.mark.asyncio
async def test_a_brain_supplied_ack_is_spoken_verbatim() -> None:
    tool = CreateArtifactTool(
        _FakeBus(), manager=_FakeManager(), kontrollierer=_FakeKontrollierer()
    )
    result = await tool.execute(
        {"title": "T", "request": "R", "spoken_ack": "Kommt sofort."},  # i18n-allow
        _ctx(),
    )
    await _settle()
    assert result.output == "Kommt sofort."  # i18n-allow


@pytest.mark.asyncio
async def test_missing_fields_are_the_models_to_fix() -> None:
    tool = CreateArtifactTool(_FakeBus(), manager=_FakeManager())
    no_title = await tool.execute({"request": "x"}, _ctx())
    no_request = await tool.execute({"title": "x"}, _ctx())
    assert no_title.success is False and "title" in (no_title.error or "")
    assert no_request.success is False and "request" in (no_request.error or "")


@pytest.mark.asyncio
async def test_no_mission_stack_is_an_honest_error_not_a_promise() -> None:
    tool = CreateArtifactTool(_FakeBus(), manager_resolver=lambda: None)
    result = await tool.execute({"title": "T", "request": "R"}, _ctx(language="en"))
    assert result.success is False
    assert "restart" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_no_kontrollierer_becomes_a_spoken_failure() -> None:
    bus, manager = _FakeBus(), _FakeManager()
    tool = CreateArtifactTool(bus, manager=manager, kontrollierer_resolver=lambda: None)
    result = await tool.execute({"title": "T", "request": "R"}, _ctx())
    await _settle()
    assert result.success is True  # the promise was made...
    done = [e for e in bus.published if isinstance(e, JarvisAgentBackgroundCompleted)]
    assert len(done) == 1 and done[0].success is False  # ...and honestly withdrawn


@pytest.mark.asyncio
async def test_a_crashing_worker_becomes_a_spoken_failure() -> None:
    bus = _FakeBus()
    tool = CreateArtifactTool(
        bus, manager=_FakeManager(), kontrollierer=_FakeKontrollierer(fail=True)
    )
    await tool.execute({"title": "T", "request": "R"}, _ctx())
    await _settle()
    done = [e for e in bus.published if isinstance(e, JarvisAgentBackgroundCompleted)]
    assert len(done) == 1 and done[0].success is False
    assert "worker exploded" in (done[0].error or "")


@pytest.mark.asyncio
async def test_revise_starts_from_the_existing_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_ISOLATION_ROOT", str(tmp_path))
    files = tmp_path / "mission_a" / "tasks" / "019e0001" / "artifacts" / "files"
    files.mkdir(parents=True)
    (files / "plan-comparison.html").write_text(
        "<!doctype html><html><head><title>Plan comparison</title></head>"
        "<body>OLD-BODY</body></html>",
        encoding="utf-8",
    )
    manager = _FakeManager()
    tool = CreateArtifactTool(_FakeBus(), manager=manager, kontrollierer=_FakeKontrollierer())

    result = await tool.execute(
        {"title": "whatever", "request": "Make the bars red.", "revise": "plan comparison"},
        _ctx(language="en"),
    )
    await _settle()

    assert result.success is True
    assert result.artifacts[0]["revision"] is True  # type: ignore[index]
    assert result.artifacts[0]["artifact_file"] == "plan-comparison.html"  # type: ignore[index]
    prompt = manager.dispatched[0]["prompt"]
    assert "## Starting point" in prompt and "OLD-BODY" in prompt
    assert "Artifact: Plan comparison" in prompt  # the page's own title wins


@pytest.mark.asyncio
async def test_revise_of_an_unknown_artifact_is_a_retryable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_ISOLATION_ROOT", str(tmp_path))
    manager = _FakeManager()
    tool = CreateArtifactTool(_FakeBus(), manager=manager)
    result = await tool.execute(
        {"title": "T", "request": "R", "revise": "budget forecast"}, _ctx(language="en")
    )
    assert result.success is False
    assert "budget forecast" in (result.error or "")
    assert manager.dispatched == []
