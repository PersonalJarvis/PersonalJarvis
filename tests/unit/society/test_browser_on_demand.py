"""Agent and routine browser calls start the runner without a viewer or paid model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.society.browser import install
from jarvis.society.browser.tool import BrowserTool
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import society_system_extra, society_tool_filter, society_tools
from tests.fakes import browser_start_runner


@pytest.fixture
async def cold_browser(tmp_path, monkeypatch):
    installs = []
    monkeypatch.setattr(install, "is_installed", lambda _: bool(installs))
    monkeypatch.setattr(install, "ensure_installed", lambda path: installs.append(path))
    monkeypatch.setattr(install, "venv_python", lambda _: Path(sys.executable))
    monkeypatch.setattr(install, "runner_path", lambda: Path(browser_start_runner.__file__))
    monkeypatch.setattr(install, "browser_executable", lambda _: Path("unused-browser"))
    monkeypatch.setattr("jarvis.core.config.get_jarvis_agent_secret", lambda _: None)
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout", provider="ollama", model="fixture")

    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("The startup fixture must not call a model")

    runtime.browser.live.model_resolver = lambda _: SimpleNamespace(
        complete=unexpected_model_call, supports_vision=False
    )
    runtime.browser.live.executor = object()
    try:
        yield runtime, installs
    finally:
        await runtime.close()


@pytest.mark.parametrize("session_id", ["society:scout", "society:scout:routine:task:run"])
async def test_cold_agent_and_routine_start_reuse_and_reopen_browser(
    cold_browser, tmp_path, session_id
):
    runtime, installs = cold_browser
    session = SimpleNamespace(session_id=session_id, cwd=str(tmp_path), permission_mode="bypass")
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    briefing = await society_system_extra(cfg, None, session)
    assert "starts the browser automatically" in briefing
    assert "Not set up" not in briefing
    assert not installs and not runtime.browser.live.sessions
    tools = society_tools(cfg, None, session)
    tool = society_tool_filter(session)(tools)[BrowserTool.name]
    ctx = SimpleNamespace(
        trace_id=uuid4(),
        user_utterance="Read the page",
        config={"approval_ref": f"agent-chat:{session_id}"},
        memory_read=None,
    )
    args = {"task": "Read the page", "url": "https://example.com"}
    result = await tool.execute(args, ctx)
    assert result.success, result.error
    first = runtime.browser.live.sessions["scout"]
    assert not first.subscribers and first.control_owner is None
    assert installs == [tmp_path]
    assert (await tool.execute(args, ctx)).success
    assert runtime.browser.live.sessions["scout"] is first
    await first.close()
    assert (await tool.execute(args, ctx)).success
    assert runtime.browser.live.sessions["scout"] is not first
    assert installs == [tmp_path]
    calls = [
        json.loads(line)
        for line in (tmp_path / "society" / "scout" / "workspace" / "browser-start.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [call["op"] for call in calls] == ["ensure", "run", "run", "shutdown", "ensure", "run"]
    assert calls[0]["args"]["profile_dir"] == calls[4]["args"]["profile_dir"]


@pytest.mark.parametrize(
    "rules",
    [
        {"denies": ["core:browser"]},
        {"grant_mode": "allowlist", "grants": []},
    ],
)
async def test_cold_browser_still_respects_denied_capability(cold_browser, tmp_path, rules):
    runtime, installs = cold_browser
    await runtime.roster.update("scout", rules)
    session = SimpleNamespace(session_id="society:scout:routine:task:run", cwd=str(tmp_path))
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    await society_system_extra(cfg, None, session)
    tools = society_tools(cfg, None, session)
    result = await tools[BrowserTool.name].execute(
        {"task": "Read the page"}, SimpleNamespace(trace_id=uuid4())
    )
    assert not result.success and result.output["reason"] == "blocked_by_policy"
    assert not installs and not runtime.browser.live.sessions
