"""Names on the rows, aliases folded onto their download, and what sits in
graphics memory when every job is loaded at once."""

from __future__ import annotations

from dataclasses import asdict

from jarvis.brain import ollama_overview, ollama_roles
from jarvis.brain.ollama_inventory import OllamaModelInfo, OllamaRunningModel


def _model(name: str, size_gb: float, *caps: str, params: str = "", quant: str = ""):
    return OllamaModelInfo(
        name=name,
        size_bytes=int(size_gb * 1024**3),
        digest="",
        modified_at="",
        family="",
        parameter_size=params,
        quantization_level=quant,
        context_length=None,
        capabilities=tuple(caps),
        license="",
    )


def _running(name: str, size_gb: float, ctx: int | None = None) -> OllamaRunningModel:
    return OllamaRunningModel(
        name=name,
        size_bytes=int(size_gb * 1024**3),
        size_vram_bytes=int(size_gb * 1024**3),
        expires_at="2026-08-27T19:00:00+02:00",
        context_length=ctx,
    )


def test_model_row_carries_a_readable_name_and_folds_a_loaded_alias() -> None:
    info = _model("ornith:9b", 5.2, "completion", "tools", params="9.0B", quant="Q4_K_M")
    running = {"ornith:9b-voice-32k": _running("ornith:9b-voice-32k", 6.5, ctx=32768)}
    row = ollama_overview.model_row(info, running, ["voice"])
    assert row["display_name"] == "Ornith"
    assert row["display_label"] == "Ornith 9B"
    assert row["quant_label"] == "Q4_K_M"
    assert row["loaded"] is True
    assert row["loaded_as"] == "ornith:9b-voice-32k"
    assert row["running_context_length"] == 32768
    live = _running("ornith:9b-voice-32k", 6.5)
    assert ollama_overview.running_row(live, ["ornith:9b"]) == {
        **asdict(live),
        "base_tag": "ornith:9b",
        "kind": "voice_profile",
    }


def _state(role: str, current: str, **extra) -> ollama_roles.RoleState:
    return ollama_roles.RoleState(
        spec=ollama_roles.role_spec(role),
        current=current,
        installed=True,
        qualifying=(),
        recommended="",
        **extra,
    )


def test_resident_payload_adds_up_what_the_jobs_load_at_once() -> None:
    chat = _model("qwen3.8-16gb:latest", 7.8, "completion", "tools", params="26.9B")
    voice = _model("ornith:9b", 5.2, "completion", "tools", params="9.0B")
    embed = _model("bge-m3:latest", 1.1, "embedding", params="566.70M")
    running = {"qwen3.8-16gb:latest": _running("qwen3.8-16gb:latest", 8.8, ctx=32768)}
    states = [
        _state("chat", "qwen3.8-16gb:latest"),
        _state("voice", "ornith:9b", context_tokens=32768, context_source="manual"),
        _state("tools_screen", "qwen3.8-16gb:latest"),
        _state("deep", "qwen3.8-16gb:latest"),
        _state("embedding", "bge-m3:latest"),
        _state("ack", "qwen3.8-16gb:latest"),
    ]
    resident = ollama_overview.resident_payload(
        states,
        [chat, voice, embed],
        running,
        ollama_roles.Machine(memory_gb=32.0, accelerator_gb=15.9),
        voice_reserve_gb=4.0,
    )
    items = {i["tag"]: i for i in resident["items"]}
    # One model on three jobs is loaded once; the read-only ack is not counted.
    assert items["qwen3.8-16gb:latest"]["roles"] == ["chat", "tools_screen", "deep"]
    assert items["qwen3.8-16gb:latest"]["loaded"] is True
    # Loaded: the context is what /api/ps reports beyond the weights.
    assert items["qwen3.8-16gb:latest"]["context_gb"] == 1.0
    assert items["qwen3.8-16gb:latest"]["context_tokens"] == 32768
    # Not loaded: the rule of thumb from the voice window.
    assert items["ornith:9b"]["context_tokens"] == 32768
    assert 4.5 < items["ornith:9b"]["context_gb"] < 5.5
    assert items["ornith:9b"]["display_label"] == "Ornith 9B"
    assert resident["reserve_gb"] == 4.0
    assert resident["over"] is True
    assert resident["total_gb"] > 15.9


def test_resident_payload_is_empty_without_picks() -> None:
    resident = ollama_overview.resident_payload([], [], {}, ollama_roles.Machine())
    assert resident == {
        "items": [],
        "reserve_gb": 0.0,
        "total_gb": 0.0,
        "accelerator_gb": 0.0,
        "over": False,
    }
