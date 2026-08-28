"""The setup test runner: one real call per configured role, an honest table.

Fakes only: the Ollama server is :class:`tests.fakes.fake_ollama_server.FakeOllamaServer`
over ``httpx.MockTransport``; the 1-token generation and the voice probe go
through the runner's injectable seams.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain import ollama_inventory as inventory
from jarvis.core import config as cfg_mod
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.local_models import assistant_test
from jarvis.local_models.assistant_test import NOT_SET, run_setup_test
from tests.fakes.fake_ollama_server import FakeOllamaServer


class _HealthResult:
    def __init__(self, ok: bool, error: str | None) -> None:
        self.ok = ok
        self.error = error
        self.duration_ms = 12.0


class _Probe:
    """Records the generation probes and answers ok unless told otherwise."""

    def __init__(self, failing: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failing = failing or {}

    async def __call__(self, provider: str, model: str) -> Any:
        self.calls.append((provider, model))
        return _HealthResult(model not in self.failing, self.failing.get(model))


def _cfg(**picks: str) -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(
        model=picks.get("chat", ""),
        tool_model=picks.get("tools_screen", ""),
        deep_model=picks.get("deep", ""),
        base_url="http://fake-ollama:11434",
    )
    return cfg


def _server() -> FakeOllamaServer:
    fake = FakeOllamaServer()
    fake.add("qwen3.5:4b", capabilities=("completion", "tools", "vision"))
    fake.add("chatonly:7b", capabilities=("completion",))
    fake.add("embeddinggemma", capabilities=("embedding",), embed_dim=768)
    return fake


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)
    return tmp_path


async def test_embed_probe_returns_the_vector_length() -> None:
    fake = _server()
    dim = await inventory.embed_probe("http://x", "embeddinggemma", transport=fake.transport())
    assert dim == 768
    assert ("POST", "/api/embed", {"model": "embeddinggemma", "input": "ping"}) in fake.calls
    with pytest.raises(inventory.OllamaModelNotFound):
        await inventory.embed_probe("http://x", "nope", transport=fake.transport())


async def test_full_setup_answers_ok_per_role_and_persists(tmp_path: Path) -> None:
    fake = _server()
    probe = _Probe()
    cfg = _cfg(chat="qwen3.5:4b", tools_screen="qwen3.5:4b", deep="qwen3.5:4b")
    report = await run_setup_test(cfg, transport=fake.transport(), brain_probe=probe)

    assert isinstance(report, assistant_test.TestReport)
    assert report.server["ok"] is True and report.server["version"] == "0.32.15"
    assert {r: c.status for r, c in report.roles.items()} == {
        "chat": "ok",
        "tools_screen": "ok",
        "deep": "ok",
        "voice": NOT_SET,
    }
    assert report.voice is None
    assert report.overall == "ok"
    # Exactly one real generation per generation role, on the Ollama provider.
    assert probe.calls == [("ollama", "qwen3.5:4b")] * 3
    # Persisted to the state file with the badge keys the monitor also writes.
    written = json.loads((tmp_path / "state" / "local_models_health.json").read_text("utf-8"))
    assert written["status"] == "ok" and written["overall"] == "ok"
    assert written["last_ok"] == written["checked_at"] == report.checked_at
    assert report.to_payload()["roles"]["chat"]["model"] == "qwen3.5:4b"


async def test_tools_screen_needs_declared_tools_and_vision() -> None:
    fake = _server()
    cfg = _cfg(tools_screen="chatonly:7b")
    report = await run_setup_test(
        cfg, ("tools_screen",), transport=fake.transport(), brain_probe=_Probe()
    )
    check = report.roles["tools_screen"]
    assert check.status == "error"
    assert "tools + vision" in check.detail
    assert report.overall == "error"
    assert report.reason().startswith("tools_screen:")


async def test_nothing_configured_is_needs_setup_not_error() -> None:
    fake = _server()
    report = await run_setup_test(_cfg(), transport=fake.transport(), brain_probe=_Probe())
    assert all(c.status == NOT_SET for c in report.roles.values())
    assert report.overall == "needs_setup"
    assert report.reason() == ""


async def test_server_down_marks_every_configured_role_unreachable() -> None:
    fake = _server()
    fake.offline = True
    probe = _Probe()
    cfg = _cfg(chat="qwen3.5:4b", deep="qwen3.5:4b")
    report = await run_setup_test(cfg, transport=fake.transport(), brain_probe=probe)
    assert report.server["ok"] is False
    assert report.roles["chat"].status == "unreachable"
    assert report.roles["deep"].status == "unreachable"
    assert probe.calls == [], "no generation is attempted against a dead server"
    assert report.overall == "error"


async def test_generation_failure_is_classified_with_the_provider_vocabulary() -> None:
    fake = _server()
    cfg = _cfg(chat="qwen3.5:4b")
    probe = _Probe(failing={"qwen3.5:4b": "model 'qwen3.5:4b' not found (404)"})
    report = await run_setup_test(cfg, ("chat",), transport=fake.transport(), brain_probe=probe)
    assert report.roles["chat"].status == "model_unavailable"


async def test_voice_probe_runs_only_for_the_managed_server() -> None:
    fake = _server()
    seen: list[str] = []

    async def voice_probe(base_url: str) -> dict[str, Any]:
        seen.append(base_url)
        return {"ok": True, "first_audio_ms": 640}

    cfg = _cfg(chat="qwen3.5:4b")
    cfg.brain.providers["local-realtime"] = BrainProviderConfig(
        launch_command="python server.py --model_name qwen3.5:4b --port 8765",
        base_url="http://127.0.0.1:8765",
    )
    report = await run_setup_test(
        cfg,
        ("chat", "voice"),
        transport=fake.transport(),
        brain_probe=_Probe(),
        voice_probe=voice_probe,
    )
    assert seen == ["http://127.0.0.1:8765"]
    assert report.voice == {
        "ok": True,
        "first_audio_ms": 640,
        "detail": "Voice round trip answered with audio.",
    }
    assert report.roles["voice"].status == "ok"
    assert report.roles["voice"].latency_ms == 640
    assert report.overall == "ok"

    # A bring-your-own command (no --model_name) is never probed.
    cfg.brain.providers["local-realtime"].launch_command = "my-own-server --port 8765"
    report = await run_setup_test(
        cfg, ("voice",), transport=fake.transport(), brain_probe=_Probe(), voice_probe=voice_probe
    )
    assert seen == ["http://127.0.0.1:8765"]
    assert report.voice is None and report.roles["voice"].status == NOT_SET


async def test_unknown_role_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown role"):
        await run_setup_test(_cfg(), ("chat", "ack"), transport=_server().transport())


async def test_since_is_kept_while_the_status_holds() -> None:
    fake = _server()
    cfg = _cfg(chat="qwen3.5:4b")
    first = await run_setup_test(cfg, ("chat",), transport=fake.transport(), brain_probe=_Probe())
    second = await run_setup_test(cfg, ("chat",), transport=fake.transport(), brain_probe=_Probe())
    written = assistant_test.load_last_report()
    assert written is not None
    assert written["since"] == first.checked_at
    assert written["checked_at"] == second.checked_at
