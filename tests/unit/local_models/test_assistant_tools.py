"""The assistant's tools: allowed tiers only, no delete, ask-tier through the executor.

Every action tool is exercised through a REAL ``ToolExecutor`` (bus, tier
evaluator, approval workflow — no mocks) on the ``interactive`` surface, so
the approval card the panel auto-answers is a genuine ticket, not a
convention.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core import config as cfg_mod
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig, SafetyConfig
from jarvis.core.events import ActionApprovalRequired, ActionDenied, ActionExecuted
from jarvis.core.protocols import ExecutionContext
from jarvis.local_models import assistant_prompt, assistant_tools
from jarvis.local_models.assistant_tools import ALLOWED_TIERS, TOOL_PREFIX, build_tools
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.approval_surface import INTERACTIVE
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import APPROVAL_DENIED_PREFIX, ToolExecutor
from tests.fakes.fake_ollama_server import FakeOllamaServer

ROOT = "http://fake-ollama:11434"

ASK_TOOLS = {
    "lm_install_ollama",
    "lm_start_server",
    "lm_stop_server",
    "lm_pull",
    "lm_set_role",
    "lm_set_model_options",
    "lm_install_voice_server",
    "lm_apply_voice_stack",
}


def _cfg(*, hf: bool = False, **picks: str) -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(
        model=picks.get("chat", ""),
        tool_model=picks.get("tools_screen", ""),
        deep_model=picks.get("deep", ""),
        base_url=ROOT,
        hf_enabled=hf,
    )
    return cfg


def _server() -> FakeOllamaServer:
    fake = FakeOllamaServer()
    fake.add("qwen3.5:4b", size=3_400_000_000, capabilities=("completion", "tools", "vision"))
    fake.add("embeddinggemma", size=600_000_000, capabilities=("embedding",), embed_dim=768)
    fake.load("qwen3.5:4b")
    return fake


def _ctx() -> ExecutionContext:
    return ExecutionContext(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)


def _executor(timeout_s: float = 5.0) -> tuple[ToolExecutor, EventBus]:
    bus = EventBus()
    executor = ToolExecutor(
        bus,
        RiskTierEvaluator(SafetyConfig()),
        ApprovalWorkflow(bus, timeout_s=timeout_s),
        default_timeout_s=timeout_s,
    )
    return executor, bus


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)
    return tmp_path


# ------------------------------------------------------------ the tool set


def test_every_tool_has_an_allowed_tier_and_the_prefix() -> None:
    tools = build_tools(_cfg(hf=True), root=ROOT)
    assert tools, "no tools built"
    for name, tool in tools.items():
        assert name == tool.name
        assert name.startswith(TOOL_PREFIX), name
        assert tool.risk_tier in ALLOWED_TIERS, (name, tool.risk_tier)
        assert tool.schema.get("type") == "object"
        assert tool.description
    assert {n for n, t in tools.items() if t.risk_tier == "ask"} == ASK_TOOLS
    assert {n for n, t in tools.items() if t.risk_tier == "monitor"} == {"lm_unload"}
    assert "lm_test_plan" in tools and tools["lm_test_plan"].risk_tier == "safe"


def test_no_delete_tool_and_nothing_writes_the_brain_primary() -> None:
    tools = build_tools(_cfg(hf=True), root=ROOT)
    for name in tools:
        assert not any(word in name for word in ("delete", "remove", "rm_", "uninstall")), name
    source = inspect.getsource(assistant_tools)
    assert "set_brain_primary" not in source
    assert "delete_model" not in source
    assert "switch_provider" not in source


def test_hf_search_is_offered_only_when_enabled() -> None:
    assert "lm_hf_search" not in build_tools(_cfg(hf=False), root=ROOT)
    assert "lm_hf_search" in build_tools(_cfg(hf=True), root=ROOT)


def test_describe_args_is_the_card_summary() -> None:
    tools = build_tools(_cfg(), root=ROOT)
    assert tools["lm_pull"].describe_args({"model": "qwen3.5:4b"}) == {  # type: ignore[attr-defined]
        "summary": "Download qwen3.5:4b"
    }
    assert tools["lm_set_role"].describe_args({"role": "chat", "model": "x"}) == {  # type: ignore[attr-defined]
        "summary": "Use x for chat"
    }
    assert tools["lm_inventory"].describe_args({}) == {}  # type: ignore[attr-defined]


# ------------------------------------------------------------ reads through the executor


async def test_safe_reads_run_through_the_executor_on_the_fake_server() -> None:
    fake = _server()
    tools = build_tools(_cfg(), root=ROOT, transport=fake.transport())
    executor, _bus = _executor()

    inventory = await executor.execute(
        tools["lm_inventory"], args={}, config_snapshot={"approval_surface": INTERACTIVE}
    )
    assert inventory.success, inventory.error
    names = {row["name"] for row in inventory.output["models"]}
    assert names == {"qwen3.5:4b", "embeddinggemma"}
    assert [r["name"] for r in inventory.output["running"]] == ["qwen3.5:4b"]

    status = await executor.execute(tools["lm_server_status"], args={})
    assert status.success, status.error
    assert status.output["probe"]["ok"] is True
    assert status.output["voice"]["status"] is None


async def test_test_plan_tool_reports_per_role() -> None:
    fake = _server()
    tools = build_tools(_cfg(chat="qwen3.5:4b"), root=ROOT, transport=fake.transport())
    executor, _bus = _executor()
    result = await executor.execute(tools["lm_test_plan"], args={"roles": ["chat"]})
    assert result.success, result.error
    assert result.output["roles"]["chat"]["status"] == "ok"
    assert result.output["overall"] == "ok"


async def test_a_failing_handler_is_an_honest_tool_error_not_an_exception() -> None:
    fake = _server()
    fake.offline = True
    tools = build_tools(_cfg(), root=ROOT, transport=fake.transport())
    result = await tools["lm_inventory"].execute({}, _ctx())
    assert result.success is False
    assert result.error and "OllamaServerError" in result.error


# ------------------------------------------------------------ ask tier = a real ticket


@pytest.mark.parametrize("name", sorted(ASK_TOOLS))
async def test_ask_tools_raise_an_interactive_ticket_and_stop_on_denial(name: str) -> None:
    fake = _server()
    tools = build_tools(_cfg(), root=ROOT, transport=fake.transport())
    executor, bus = _executor(timeout_s=5.0)
    cards: list[ActionApprovalRequired] = []
    executed: list[ActionExecuted] = []

    async def _on_card(event: ActionApprovalRequired) -> None:
        cards.append(event)
        await bus.publish(ActionDenied(trace_id=event.trace_id, reason="user_vetoed"))

    async def _on_executed(event: ActionExecuted) -> None:
        executed.append(event)

    bus.subscribe(ActionApprovalRequired, _on_card)
    bus.subscribe(ActionExecuted, _on_executed)

    args = {
        "model": "qwen3.5:4b",
        "role": "chat",
        "options": {"num_ctx": 4096},
        "brain_model": "qwen3.5:4b",
        "voice_model": "kokoro",
    }
    result = await asyncio.wait_for(
        executor.execute(tools[name], args=args, config_snapshot={"approval_surface": INTERACTIVE}),
        timeout=4.0,
    )

    assert len(cards) == 1, "exactly one approval card"
    card = cards[0]
    assert card.tool_name == name and card.risk_tier == "ask" and card.reason == "risk_tier"
    assert card.expires_at_ns > 0, "an interactive card has a real window"
    assert result.success is False
    assert result.error == f"{APPROVAL_DENIED_PREFIX} (user_vetoed)"
    assert executed == [], "a denied action never runs"
    # Nothing reached the server for a denied action.
    assert not any(path in ("/api/pull", "/api/generate") for _m, path, _b in fake.calls)


async def test_monitor_tool_runs_without_a_card() -> None:
    fake = _server()
    tools = build_tools(_cfg(), root=ROOT, transport=fake.transport())
    executor, bus = _executor()
    cards: list[ActionApprovalRequired] = []

    async def _on_card(event: ActionApprovalRequired) -> None:
        cards.append(event)

    bus.subscribe(ActionApprovalRequired, _on_card)
    result = await executor.execute(
        tools["lm_unload"],
        args={"model": "qwen3.5:4b"},
        config_snapshot={"approval_surface": INTERACTIVE},
    )
    assert result.success, result.error
    assert cards == []
    assert fake.models["qwen3.5:4b"].loaded is False


async def test_set_model_options_refuses_keys_the_suggestion_did_not_return() -> None:
    fake = _server()
    tools = build_tools(_cfg(), root=ROOT, transport=fake.transport())
    suggested = await tools["lm_suggested_options"].execute({"model": "qwen3.5:4b"}, _ctx())
    assert suggested.success, suggested.error
    assert set(suggested.output["options"]) <= set(cfg_mod.OLLAMA_MODEL_OPTION_KEYS)

    result = await tools["lm_set_model_options"].execute(
        {"model": "qwen3.5:4b", "options": {"not_a_knob": 1}}, _ctx()
    )
    assert result.success is False
    assert "refused: not_a_knob" in (result.error or "")


# ------------------------------------------------------------ the prompt


async def test_prompt_renders_with_the_fake_server_and_carries_the_contract() -> None:
    fake = _server()
    cfg = _cfg(chat="qwen3.5:4b")
    text = await assistant_prompt.build_system_extra(cfg, root=ROOT, transport=fake.transport())

    assert len(text) < 10_000, len(text)
    assert "Ollama server: http://fake-ollama:11434 — running, version 0.32.15" in text
    assert "- qwen3.5:4b · 3.2 GB · completion,tools,vision · ctx 262144" in text
    assert "chat · completion · qwen3.5:4b · yes · brain.providers.ollama.model" in text
    assert "tools_screen · tools+vision · not set" in text
    assert "CURATED SHORTLIST (reviewed " in text
    assert "qwen3.5:4b · chat · 3.4 · " in text and "· yes · tools+vision" in text
    assert "new_little_tested" in text or "proven" in text
    assert "```jarvis-proposal" in text
    assert "Never delete a model" in text
    assert "brain_switch" in text


async def test_prompt_is_honest_when_the_server_is_down() -> None:
    fake = _server()
    fake.offline = True
    text = await assistant_prompt.build_system_extra(_cfg(), root=ROOT, transport=fake.transport())
    assert "not answering" in text
    assert "INSTALLED MODELS\n- unavailable:" in text


def test_openers_name_the_contract_and_never_execute_unasked() -> None:
    for opener in (assistant_prompt.SETUP_OPENER, assistant_prompt.DIAGNOSE_OPENER):
        assert "jarvis-proposal" in opener
        assert "until I confirm" in opener
    assert "lm_test_plan" in assistant_prompt.TEST_OPENER
