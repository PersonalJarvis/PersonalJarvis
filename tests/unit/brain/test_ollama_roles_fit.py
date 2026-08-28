"""One verdict per (job, model): the fit the card, the picker and the setup
automation all read, plus the honest voice write and the download picks."""

from __future__ import annotations

import pytest

from jarvis.brain import ollama_inventory, ollama_roles
from jarvis.core import config_writer
from tests.fakes.fake_ollama_server import FakeOllamaServer
from tests.unit.brain.test_ollama_roles import (  # noqa: F401 — `shortlist` is a fixture
    ROOT,
    _cfg,
    _info,
    shortlist,
)


def _voice() -> ollama_roles.RoleSpec:
    return ollama_roles.role_spec("voice")


def _with_context(info: ollama_inventory.OllamaModelInfo, tokens: int):
    return ollama_inventory.OllamaModelInfo(
        name=info.name,
        size_bytes=info.size_bytes,
        digest="",
        modified_at="",
        family="",
        parameter_size="",
        quantization_level="",
        context_length=tokens,
        capabilities=info.capabilities,
        license="",
    )


def test_fit_for_gates_the_voice_job_on_tool_calls_and_context() -> None:
    card = ollama_roles.Machine(memory_gb=32.0, accelerator_gb=16.0)
    chatty = _info("deepseek-llm:latest", 3.7, "completion")
    assert ollama_roles.fit_for(_voice(), chatty, card) == ("unfit", "no tool calls")
    short = _with_context(_info("short:7b", 3.7, "completion", "tools"), 4096)
    fit, reason = ollama_roles.fit_for(_voice(), short, card)
    assert fit == "unfit" and "4k context" in reason and "8k" in reason
    fits = _info("ornith:9b", 5.2, "completion", "tools", "thinking")
    assert ollama_roles.fit_for(_voice(), fits, card) == ("fits", "")


def test_fit_for_names_the_cost_of_a_model_that_can_do_the_job_but_slowly() -> None:
    card = ollama_roles.Machine(memory_gb=32.0, accelerator_gb=16.0)
    big = _info("gemma4:12b-it-qat", 6.7, "completion", "tools", "vision")
    fit, reason = ollama_roles.fit_for(_voice(), big, card)
    assert fit == "slow" and "over 6 GB" in reason
    # Past the graphics card a call cannot wait for the processor …
    huge = _info("nemotron-cascade-2:latest", 22.6, "completion", "tools")
    fit, reason = ollama_roles.fit_for(_voice(), huge, card)
    assert fit == "unfit" and "graphics memory" in reason
    # … while the chat job takes it, slower.
    fit, reason = ollama_roles.fit_for(ollama_roles.role_spec("chat"), huge, card)
    assert fit == "slow" and "processor" in reason


def test_fit_for_calls_an_unprobed_download_unknown_not_unfit() -> None:
    unknown = _info("broken:1b", 1.0, probed=False)
    fit, reason = ollama_roles.fit_for(_voice(), unknown)
    assert fit == "unknown" and reason


def test_choices_judge_every_installed_download_in_inventory_order() -> None:
    card = ollama_roles.Machine(memory_gb=32.0, accelerator_gb=16.0)
    models = [
        _info("ornith:9b", 5.2, "completion", "tools"),
        _info("bge-m3:latest", 1.1, "embedding"),
        _info("deepseek-llm:latest", 3.7, "completion"),
    ]
    choices = ollama_roles.choices_for(_voice(), models, card)
    assert [c[0] for c in choices] == ["ornith:9b", "bge-m3:latest", "deepseek-llm:latest"]
    assert [c[1] for c in choices] == ["fits", "unfit", "unfit"]
    assert ollama_roles.qualifying_models(_voice(), models, card) == ("ornith:9b",)


async def test_list_roles_carries_the_verdict_on_the_current_pick(shortlist) -> None:  # noqa: F811
    ollama_inventory._reset_for_tests()
    server = FakeOllamaServer()
    server.add("qwen3.5:4b", size=3_400_000_000, capabilities=("completion", "tools", "vision"))
    server.add("blind:7b", size=4_000_000_000, capabilities=("completion", "tools"))
    cfg = _cfg()
    cfg.brain.providers["ollama"].tool_model = "blind:7b"
    cfg.brain.providers["ollama"].deep_model = "gone:1b"
    states, _ = await ollama_roles.list_roles(ROOT, cfg, transport=server.transport())
    by_id = {s.spec.id: s for s in states}
    assert by_id["tools_screen"].current_fit == "unfit"
    assert by_id["tools_screen"].current_reason == "no vision"
    assert by_id["deep"].current_fit == "absent"
    assert by_id["chat"].current_fit == "fits"
    assert [c[0] for c in by_id["chat"].choices] == ["qwen3.5:4b", "blind:7b"]
    assert by_id["chat"].spec.layout == "card"
    assert by_id["ack"].spec.layout == "footnote"


def _row(tag: str, size: float, *, vision: bool, installed: bool = False) -> dict:
    return {
        "id": tag,
        "label": tag,
        "role": "chat",
        "tools": True,
        "vision": vision,
        "size_gb": size,
        "installed": installed,
        "fit": "comfortable",
        "fit_note": "",
    }


def test_download_picks_respect_the_jobs_gates() -> None:
    rows = [
        _row("big:27b", 18.0, vision=True),
        _row("small:4b", 3.4, vision=True),
        _row("blind:9b", 5.6, vision=False),
        _row("here:4b", 3.4, vision=True, installed=True),
    ]
    voice = [t for t, *_ in ollama_roles.download_picks(_voice(), rows)]
    assert voice == ["blind:9b", "small:4b"]  # under 6 GB, not installed, largest first
    screen = ollama_roles.role_spec("tools_screen")
    assert [t for t, *_ in ollama_roles.download_picks(screen, rows)] == ["big:27b", "small:4b"]
    # A role whose job none of these rows serves gets nothing rather than a
    # chat model dressed up as a pick (`deep` pulls the "coder" shortlist).
    assert ollama_roles.download_picks(ollama_roles.role_spec("deep"), rows) == ()


def test_set_role_voice_refuses_when_nothing_reached_the_toml(monkeypatch) -> None:
    cfg = _cfg()
    monkeypatch.setattr(config_writer, "update_local_realtime_launch_model", lambda tag: False)
    with pytest.raises(ValueError, match="could not be updated"):
        ollama_roles.set_role("voice", "ornith:9b", cfg=cfg)
    # The same value is a no-op, not a failure.
    assert ollama_roles.set_role("voice", "qwen3.5:4b", cfg=cfg)["model"] == "qwen3.5:4b"


def test_roles_using_can_include_the_read_only_consumers() -> None:
    cfg = _cfg()
    cfg.ack_brain.providers.ollama.model = "qwen3.5:4b"
    assert ollama_roles.roles_using(cfg, "qwen3.5:4b") == ["chat", "voice"]
    assert ollama_roles.roles_using(cfg, "qwen3.5:4b", include_readonly=True) == [
        "chat",
        "voice",
        "ack",
    ]
