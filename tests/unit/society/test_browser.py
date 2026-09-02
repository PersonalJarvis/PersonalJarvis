"""The agent's browser: provider mapping, out-of-process jobs, the tool, the board."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.society.browser.llm import LLMUnavailable, llm_spec_for
from jarvis.society.browser.session import (
    BrowserJobs,
    BrowserUnavailable,
    profile_dir,
    profile_has_logins,
)
from jarvis.society.browser.tool import BrowserTool, task_needs_approval
from jarvis.society.events import MsgType
from jarvis.society.failure_reasons import FailureReason
from jarvis.society.runtime import SocietyRuntime

CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)

#: A stand-in for runner.py: answers the protocol without browser-use.
FAKE_RUNNER = """
import json, sys, time
req = json.loads(sys.stdin.readline())
mode = req.get("mode")
def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
if mode == "probe":
    emit({"kind": "done", "ok": True, "version": "fake"})
elif mode == "login":
    emit({"kind": "login_open", "start_url": req.get("start_url")})
    line = sys.stdin.readline()
    emit({"kind": "done", "ok": True})
elif mode == "run":
    if "explode" in req["task"]:
        emit({"kind": "done", "ok": False, "error": "RuntimeError: boom"})
        sys.exit(1)
    if "hang" in req["task"]:
        time.sleep(30)
    for n in range(1, 7):
        emit({"kind": "step", "n": n, "url": f"https://example.com/{n}"})
    emit({"kind": "done", "ok": True, "final_result": "The answer is 42.",
          "urls": ["https://example.com/6"], "errors": [], "steps": 6, "seconds": 0.2,
          "cost_usd": 0.03, "headless": req.get("headless"), "llm": req.get("llm"),
          "profile_dir": req.get("profile_dir"), "cdp_url": req.get("cdp_url")})
"""


@pytest.fixture
def fake_runner(tmp_path: Path) -> Path:
    path = tmp_path / "fake_runner.py"
    path.write_text(FAKE_RUNNER, encoding="utf-8")
    return path


def _jobs(tmp_path: Path, fake_runner: Path, *, installed: bool = True) -> BrowserJobs:
    return BrowserJobs(
        tmp_path, python=Path(sys.executable), runner=fake_runner, installed=lambda: installed
    )


# ------------------------------------------------------------------ llm


def test_llm_mapping_with_explicit_keys():
    spec = llm_spec_for("anthropic", "claude-x", secret=lambda p: "sk-ant")
    assert spec.cls == "ChatAnthropic" and spec.model == "claude-x" and spec.api_key == "sk-ant"
    grok = llm_spec_for("grok", secret=lambda p: "xai")
    assert grok.cls == "ChatOpenAI" and grok.base_url and "x.ai" in grok.base_url
    ollama = llm_spec_for("ollama", "qwen3:8b", secret=lambda p: None)
    assert ollama.cls == "ChatOllama" and ollama.api_key is None
    assert "api_key" not in ollama.to_request()
    with pytest.raises(LLMUnavailable) as no_key:
        llm_spec_for("openai", secret=lambda p: None)
    assert no_key.value.reason is FailureReason.AUTH_FAILED
    with pytest.raises(LLMUnavailable) as unknown:
        llm_spec_for("deepseek-harness", secret=lambda p: "k")
    assert unknown.value.reason is FailureReason.BLOCKED_BY_POLICY


def test_task_needs_approval_words():
    assert task_needs_approval("Send the invoice to Bob")
    assert task_needs_approval("Bitte den Newsletter kündigen")  # i18n-allow: sample task
    assert not task_needs_approval("Read today's headlines and summarize them")


# ----------------------------------------------------------------- jobs


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout", provider="openai", model="gpt-5")
    try:
        yield runtime
    finally:
        await runtime.close()


async def test_run_streams_steps_and_returns_the_outcome(rt, tmp_path, fake_runner):
    jobs = _jobs(tmp_path, fake_runner)
    scout = await rt.roster.get("scout")
    seen: list[dict] = []
    outcome = await jobs.run(
        scout, task="find it", llm={"class": "ChatOpenAI", "model": "m"}, on_step=seen.append
    )
    assert outcome.ok and outcome.final_result == "The answer is 42."
    assert outcome.steps == 6 and outcome.cost_usd == 0.03
    assert [e["n"] for e in seen if e["kind"] == "step"] == [1, 2, 3, 4, 5, 6]
    assert not jobs.running_for("scout")
    assert profile_dir(tmp_path, "scout").is_dir()


async def test_run_failure_and_timeout(rt, tmp_path, fake_runner):
    jobs = _jobs(tmp_path, fake_runner)
    scout = await rt.roster.get("scout")
    bad = await jobs.run(scout, task="explode now", llm={})
    assert bad.ok is False and "boom" in (bad.error or "")
    slow = await jobs.run(scout, task="hang", llm={}, wall_s=1.5)
    assert slow.ok is False and "exceeded" in (slow.error or "")
    assert not jobs.running_for("scout")


async def test_not_installed_and_attach_mode(rt, tmp_path, fake_runner):
    scout = await rt.roster.get("scout")
    with pytest.raises(BrowserUnavailable) as exc:
        await _jobs(tmp_path, fake_runner, installed=False).run(scout, task="x", llm={})
    assert exc.value.reason is FailureReason.BLOCKED_BY_POLICY
    attached = await rt.roster.update("scout", {"browser_mode": "attach"})
    jobs = _jobs(tmp_path, fake_runner)
    status = jobs.status_for(attached)
    assert status["mode"] == "attach" and status["cdp_url"]
    assert (await jobs.login(attached))["skipped"]


async def test_login_session_closes_on_done(rt, tmp_path, fake_runner):
    import asyncio

    jobs = _jobs(tmp_path, fake_runner)
    scout = await rt.roster.get("scout")
    task = asyncio.create_task(jobs.login(scout, start_url="https://example.com/login"))
    for _ in range(50):
        await asyncio.sleep(0.05)
        if jobs.running_for("scout"):
            break
    assert await jobs.end_login("scout") is True
    result = await asyncio.wait_for(task, timeout=10)
    assert result["ok"] is True
    assert profile_has_logins(tmp_path, "scout") is False


# ----------------------------------------------------------------- tool


async def test_tool_runs_and_writes_digests(rt, tmp_path, fake_runner, monkeypatch):
    jobs = _jobs(tmp_path, fake_runner)
    monkeypatch.setattr(
        "jarvis.society.browser.tool.llm_spec_for",
        lambda provider, model="", **kw: llm_spec_for(provider, model, secret=lambda p: "k"),
    )
    tool = BrowserTool(rt, "scout", jobs)
    res = await tool.execute({"task": "read the headlines", "url": "https://news.example"}, CTX)
    assert res.success, res.error
    assert res.output["final_result"] == "The answer is 42."
    assert res.output["provider"] == "openai"
    digests = [e for e in await rt.store.events_since(0) if e.msg_type is MsgType.DIGEST]
    kinds = [e.payload["kind"] for e in digests]
    assert kinds == ["browser_steps", "browser_done"]
    assert digests[-1].cost_usd == 0.03
    assert (await rt.store.agent_stats("scout"))["total_cost_usd"] == pytest.approx(0.03)


async def test_tool_gates(rt, tmp_path, fake_runner):
    jobs = _jobs(tmp_path, fake_runner)
    tool = BrowserTool(rt, "scout", jobs)
    assert tool.risk_tier_for_args({"task": "delete the old posts"}) == "ask"
    queued = await tool.execute({"task": "send the report to the team"}, CTX)
    assert queued.success is False and queued.output["reason"] == "approval_required"
    assert len(await rt.approvals.pending()) == 1
    off = BrowserTool(rt, "scout", _jobs(tmp_path, fake_runner, installed=False))
    not_ready = await off.execute({"task": "read"}, CTX)
    assert not_ready.success is False and "install_action" in not_ready.output
    await rt.store.set_kill_switch(True)
    assert (await tool.execute({"task": "read"}, CTX)).output["reason"] == "kill_switch"


async def test_briefing_and_surface_reflect_the_browser(rt, tmp_path, fake_runner):
    from jarvis.society.surface import society_system_extra, society_tools

    session = SimpleNamespace(
        session_id="society:scout", cwd=str(tmp_path / "ws"), permission_mode="ask"
    )
    cfg = SimpleNamespace(
        wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
        memory=SimpleNamespace(data_dir=str(tmp_path)),
    )
    briefing = await society_system_extra(cfg, None, session)
    assert "## Your browser\nNot set up" in briefing
    assert "society_browser" not in society_tools(cfg, None, session)
    rt.browser = _jobs(tmp_path, fake_runner)
    briefing = await society_system_extra(cfg, None, session)
    assert "runs in your own persistent browser profile" in briefing
    assert "society_browser" in society_tools(cfg, None, session)
