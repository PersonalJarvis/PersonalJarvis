"""The managed voice brain's ``num_ctx`` follows the machine's memory.

Maintainer mandate 2026-08-24: the context is not a cost lever. The old fixed
8k profile is now the floor; the real size is the largest rung of the shared
context ladder whose weights + KV cache fit the accelerator (minus the reserve
for local STT/TTS), capped at the model's native window.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from jarvis.brain import ollama_inventory
from jarvis.brain.ollama_profiles import largest_context_for
from jarvis.realtime.local_server import supervisor


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_ollama(monkeypatch, *, size_bytes: int, native: int | None, created: list[dict]):
    """Serve ``/api/tags`` + ``/api/show`` for one model and record ``/api/create``."""
    import urllib.request

    def urlopen(request, timeout=None):  # noqa: ANN001
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/api/tags"):
            return _FakeResponse({"models": [{"name": "qwen3.5:4b", "size": size_bytes}]})
        if url.endswith("/api/show"):
            info = {"general.architecture": "qwen3"}
            if native is not None:
                info["qwen3.context_length"] = native
            return _FakeResponse({"model_info": info})
        if url.endswith("/api/create"):
            created.append(json.loads(request.data.decode("utf-8")))
            return _FakeResponse({"status": "success"})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


@pytest.fixture(autouse=True)
def _fresh_memo() -> None:
    supervisor._prepared_voice_models.clear()


def test_ladder_helper_is_capped_by_native_window_and_budget() -> None:
    # 3.4 GB model, unlimited budget, native 32k -> the 32k rung, not 128k.
    assert largest_context_for(size_gb=3.4, native_context=32_768, budget_gb=500.0) == 32_768
    # Unknown budget -> the smallest rung.
    assert largest_context_for(size_gb=3.4, native_context=None, budget_gb=None) == 4096


def test_a_16gb_card_gets_a_64k_voice_context(monkeypatch) -> None:
    _fake_ollama(monkeypatch, size_bytes=3_400_000_000, native=262_144, created=[])
    monkeypatch.setattr(
        "jarvis.hardware.detection.usable_accelerator_gb", lambda: (15.9, "nvidia-smi")
    )
    tokens, why = supervisor.voice_brain_context_tokens("http://127.0.0.1:11434", "qwen3.5:4b")
    assert tokens == 65_536
    assert "reserved for local STT/TTS" in why


def test_a_128gb_box_climbs_to_the_top_rung_within_the_native_window(monkeypatch) -> None:
    _fake_ollama(monkeypatch, size_bytes=3_400_000_000, native=262_144, created=[])
    monkeypatch.setattr(
        "jarvis.hardware.detection.usable_accelerator_gb", lambda: (128.0, "apple-unified")
    )
    tokens, why = supervisor.voice_brain_context_tokens("http://127.0.0.1:11434", "qwen3.5:4b")
    assert tokens == 131_072
    assert "unified memory" in why


def test_unreadable_hardware_falls_back_to_the_floor_and_says_so(monkeypatch) -> None:
    _fake_ollama(monkeypatch, size_bytes=3_400_000_000, native=262_144, created=[])
    monkeypatch.setattr("jarvis.hardware.detection.usable_accelerator_gb", lambda: (0.0, "none"))
    monkeypatch.setattr("jarvis.hardware.detection.system_ram_gb", lambda: None)
    tokens, why = supervisor.voice_brain_context_tokens("http://127.0.0.1:11434", "qwen3.5:4b")
    assert tokens == supervisor.VOICE_BRAIN_CONTEXT_TOKENS_FLOOR
    assert "floor" in why


def test_no_accelerator_uses_the_ram_rule(monkeypatch) -> None:
    _fake_ollama(monkeypatch, size_bytes=3_400_000_000, native=262_144, created=[])
    monkeypatch.setattr("jarvis.hardware.detection.usable_accelerator_gb", lambda: (0.0, "none"))
    monkeypatch.setattr("jarvis.hardware.detection.system_ram_gb", lambda: 32.0)
    tokens, why = supervisor.voice_brain_context_tokens("http://127.0.0.1:11434", "qwen3.5:4b")
    # 60 % of 32 GB minus the 4 GB reserve = 15.2 GB -> the 64k rung fits (11.1 GB).
    assert tokens == 65_536
    assert "RAM" in why


def test_prepare_creates_an_alias_named_after_the_chosen_context(monkeypatch) -> None:
    created: list[dict] = []
    _fake_ollama(monkeypatch, size_bytes=3_400_000_000, native=262_144, created=created)
    monkeypatch.setattr(
        "jarvis.hardware.detection.usable_accelerator_gb", lambda: (15.9, "nvidia-smi")
    )
    command = (
        "python -m server --model_name qwen3.5:4b "
        "--responses_api_base_url http://127.0.0.1:11434/v1 --responses_api_api_key ollama"
    )
    out = supervisor.prepare_voice_brain_command(command)
    assert "qwen3.5:4b-voice-64k" in out
    assert created and created[0]["parameters"] == {"num_ctx": 65_536}
    assert created[0]["from"] == "qwen3.5:4b"


def test_alias_round_trip_folds_any_context_size_back_to_the_base() -> None:
    assert supervisor._voice_context_models("qwen3.5:4b-voice-64k") == (
        "qwen3.5:4b",
        "qwen3.5:4b-voice-8k",
    )
    assert supervisor._voice_context_models("qwen3.5:4b", 32_768) == (
        "qwen3.5:4b",
        "qwen3.5:4b-voice-32k",
    )
    assert supervisor._voice_context_models("qwen3.5:4b-voice-8k", 131_072)[1] == (
        "qwen3.5:4b-voice-128k"
    )


def test_inventory_hides_every_voice_alias_size() -> None:
    assert ollama_inventory.is_hidden_alias("qwen3.5:4b-voice-8k")
    assert ollama_inventory.is_hidden_alias("qwen3.5:4b-voice-64k:latest")
    assert not ollama_inventory.is_hidden_alias("qwen3.5:4b")


# -- Idle release + the user's own choice --


def test_the_users_tune_choice_wins_over_the_automatic_size(monkeypatch) -> None:
    _fake_ollama(monkeypatch, size_bytes=3_400_000_000, native=262_144, created=[])
    monkeypatch.setattr(
        "jarvis.hardware.detection.usable_accelerator_gb", lambda: (15.9, "nvidia-smi")
    )
    tokens, why = supervisor.voice_brain_context_tokens(
        "http://127.0.0.1:11434", "qwen3.5:4b", override=131_072
    )
    assert tokens == 131_072
    assert "set by you" in why


def test_prepare_reads_the_override_from_the_ollama_card(monkeypatch) -> None:
    created: list[dict] = []
    _fake_ollama(monkeypatch, size_bytes=3_400_000_000, native=262_144, created=created)
    monkeypatch.setattr(supervisor, "_voice_context_override", lambda model: 32_768)
    command = (
        "python -m server --model_name qwen3.5:4b "
        "--responses_api_base_url http://127.0.0.1:11434/v1 --responses_api_api_key ollama"
    )
    out = supervisor.prepare_voice_brain_command(command)
    assert "qwen3.5:4b-voice-32k" in out
    assert created[0]["parameters"] == {"num_ctx": 32_768}


def test_override_is_the_tune_num_ctx_of_the_base_model(monkeypatch) -> None:
    from jarvis.core.config import BrainProviderConfig, JarvisConfig, OllamaModelOptions

    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(
        models={"qwen3.5:4b": OllamaModelOptions(num_ctx=16_384)}
    )
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: cfg)
    assert supervisor._voice_context_override("qwen3.5:4b") == 16_384
    assert supervisor._voice_context_override("qwen3.5:4b-voice-8k") is None
    assert supervisor._voice_context_override("other:1b") is None


def test_keep_alive_follows_the_idle_window() -> None:
    assert supervisor._brain_keep_alive(0) == supervisor.BRAIN_KEEP_ALIVE
    assert supervisor._brain_keep_alive(1800) == "1800s"
    assert supervisor._brain_keep_alive(10) == "60s"


def test_idle_release_reads_the_voice_setting(monkeypatch) -> None:
    from jarvis.core.config import JarvisConfig

    cfg = JarvisConfig()
    assert cfg.voice.local_idle_release_minutes == 30
    cfg.voice.local_idle_release_minutes = 5
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: cfg)
    assert supervisor.idle_release_s() == 300.0
    cfg.voice.local_idle_release_minutes = 0
    assert supervisor.idle_release_s() == 0.0


def _monitor_setup(monkeypatch, tmp_path, pool: dict) -> list[str]:
    import threading

    root = tmp_path / "local_realtime"
    command = f'"{root / "server.exe"}" --mode realtime'
    monkeypatch.setattr(supervisor, "RUNTIME_MONITOR_POLL_S", 0.0)
    monkeypatch.setattr(supervisor, "RUNTIME_MONITOR_POOL_INTERVAL_S", 0.0)
    monkeypatch.setattr(supervisor, "managed_install_root", lambda value: root)
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (4711, True))
    monkeypatch.setattr(
        supervisor,
        "_revive_from_monitor",
        lambda **kwargs: pytest.fail("a healthy server must never be revived"),
    )
    monkeypatch.setattr(supervisor, "warm_brain", lambda **kwargs: True)
    stopped: list[str] = []
    stop_event = threading.Event()
    probes = {"n": 0}

    def probe(*args, **kwargs):
        import time

        probes["n"] += 1
        if probes["n"] == 1:
            # Windows' monotonic clock ticks every ~16 ms; let the idle window pass.
            time.sleep(0.05)
        if probes["n"] >= 25:
            stop_event.set()
        return pool

    monkeypatch.setattr(supervisor, "probe_runtime", probe)

    def stop(*, owned_only: bool = True, install_root=None):
        stopped.append("stopped")
        stop_event.set()
        return True, "stopped"

    monkeypatch.setattr(supervisor, "stop", stop)
    monkeypatch.setattr(supervisor, "idle_release_s", lambda: 0.001)
    supervisor._runtime_monitor(
        command, "http://127.0.0.1:8765", stop_event, (command, "http://127.0.0.1:8765/v1/pool")
    )
    return stopped


def test_monitor_stops_the_server_after_the_idle_window(monkeypatch, tmp_path) -> None:
    idle = {"size": 1, "in_use": 0, "available": 1, "active": 0, "draining": 0, "stuck": 0}
    assert _monitor_setup(monkeypatch, tmp_path, idle) == ["stopped"]


def test_monitor_keeps_the_server_while_a_call_is_active(monkeypatch, tmp_path) -> None:
    busy = {"size": 1, "in_use": 1, "available": 0, "active": 1, "draining": 0, "stuck": 0}
    assert _monitor_setup(monkeypatch, tmp_path, busy) == []
