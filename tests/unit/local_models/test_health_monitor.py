"""The self-check: skip when there is nothing to watch, a bounded probe otherwise,
a schedule of +10 min then every N hours — all with a fake clock and fake probes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jarvis.core import config as cfg_mod
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.local_models import health_monitor as hm


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)
    return tmp_path


def _cfg(chat: str = "", hours: float | None = None) -> JarvisConfig:
    cfg = JarvisConfig()
    provider = BrainProviderConfig(model=chat, base_url="http://fake:11434")
    if hours is not None:
        provider.health_check_hours = hours
    cfg.brain.providers["ollama"] = provider
    return cfg


def _probe(ok: bool):
    async def _fn(_root: str) -> dict[str, Any]:
        return {"ok": ok, "version": "0.32.15", "detail": "" if ok else "No Ollama answered."}

    return _fn


class _Result:
    def __init__(self, status: str, detail: str = "") -> None:
        self.status = status
        self.detail = detail


def _record(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "state" / "local_models_health.json").read_text("utf-8"))


def _capability(detail: str | Exception):
    async def probe(_root: str, _model: str) -> str:
        if isinstance(detail, Exception):
            raise detail
        return detail

    return probe


def _embed(dims: int | Exception):
    async def _fn(_root: str, _model: str) -> int:
        if isinstance(dims, Exception):
            raise dims
        return dims

    return _fn


def _cfg_with_embedding(chat: str, embedding: str) -> JarvisConfig:
    cfg = _cfg(chat)
    cfg.ultrawiki.embedding_provider = "ollama"
    cfg.ultrawiki.embedding_model = embedding
    return cfg


@pytest.mark.asyncio
async def test_verify_reports_every_step_and_writes_the_record(tmp_path: Path) -> None:
    async def _generate(_cfg: Any, _model: str) -> _Result:
        return _Result("ok")

    result = await hm.verify_setup(
        _cfg_with_embedding("qwen3.5:4b", "embeddinggemma"),
        probe=_probe(True),
        generate=_generate,
        embed=_embed(768),
        tool_call=_capability("Called the tool."),
        vision=_capability("Saw the image: white"),
    )
    assert result["ok"] is True and result["status"] == "ok" and result["reason"] == ""
    steps = {s["id"]: s for s in result["steps"]}
    assert list(steps) == ["server", "chat", "voice", "tools_screen", "embedding"]
    # Neither the voice nor the screen role is configured here: not run, not passed.
    assert steps["voice"]["ok"] is None and steps["tools_screen"]["ok"] is None
    assert steps["server"]["ok"] is True and "0.32.15" in steps["server"]["detail"]
    assert steps["chat"] == {
        "id": "chat",
        "ok": True,
        "model": "qwen3.5:4b",
        "detail": "Answered.",
        "ms": steps["chat"]["ms"],
    }
    assert steps["embedding"]["ok"] is True and "768" in steps["embedding"]["detail"]
    assert _record(tmp_path)["status"] == "ok"


@pytest.mark.asyncio
async def test_verify_names_the_failing_step(tmp_path: Path) -> None:
    async def _generate(_cfg: Any, _model: str) -> _Result:
        return _Result("ok")

    result = await hm.verify_setup(
        _cfg_with_embedding("qwen3.5:4b", "embeddinggemma"),
        probe=_probe(True),
        generate=_generate,
        embed=_embed(RuntimeError("/api/embed for 'embeddinggemma' failed: not found")),
    )
    assert result["ok"] is False and result["status"] == "error"
    assert result["reason"].startswith("embeddinggemma: ")
    assert "not found" in result["reason"]
    assert _record(tmp_path)["status"] == "error"


@pytest.mark.asyncio
async def test_verify_marks_unconfigured_roles_as_not_run_and_a_down_server_first(
    tmp_path: Path,
) -> None:
    async def _generate(_cfg: Any, _model: str) -> _Result:
        raise AssertionError("must not generate")

    nothing = await hm.verify_setup(_cfg(), probe=_probe(True), generate=_generate)
    assert nothing["status"] == "needs_setup"
    assert [s["ok"] for s in nothing["steps"]] == [True, None, None, None, None]

    down = await hm.verify_setup(_cfg("qwen3.5:4b"), probe=_probe(False), generate=_generate)
    assert down["status"] == "error" and "No Ollama answered" in down["reason"]
    assert [s["ok"] for s in down["steps"]] == [False, None, None, None, None]
    assert down["steps"][1]["model"] == "qwen3.5:4b"
    assert _record(tmp_path)["status"] == "error"


def test_interval_reads_the_one_config_field() -> None:
    assert hm.interval_hours(_cfg()) == 6.0
    assert hm.interval_hours(_cfg(hours=1.5)) == 1.5
    assert hm.interval_hours(_cfg(hours=0)) == 6.0
    assert hm.interval_hours(JarvisConfig()) == 6.0


async def test_skips_when_the_server_is_down_and_nothing_is_configured(tmp_path: Path) -> None:
    record = await hm.check_once(_cfg(), probe=_probe(False))
    assert record is None
    assert not (tmp_path / "state" / "local_models_health.json").exists()


async def test_down_server_with_a_role_is_an_error(tmp_path: Path) -> None:
    record = await hm.check_once(_cfg(chat="qwen3.5:4b"), probe=_probe(False))
    assert record == {"status": "error", "reason": "No Ollama answered."}
    assert _record(tmp_path)["status"] == "error"


async def test_running_server_without_roles_needs_setup() -> None:
    record = await hm.check_once(_cfg(), probe=_probe(True), persist=False)
    assert record is not None and record["status"] == "needs_setup"


async def test_one_generation_decides_ok_or_error(tmp_path: Path) -> None:
    calls: list[str] = []

    async def generate(_cfg: Any, model: str) -> Any:
        calls.append(model)
        return _Result("ok")

    record = await hm.check_once(_cfg(chat="qwen3.5:4b"), probe=_probe(True), generate=generate)
    assert record == {"status": "ok", "reason": ""} and calls == ["qwen3.5:4b"]
    written = _record(tmp_path)
    assert written["status"] == "ok" and written["last_ok"] == written["checked_at"]
    assert written["since"] == written["checked_at"]

    async def failing(_cfg: Any, model: str) -> Any:
        return _Result("model_unavailable", "model not found")

    record = await hm.check_once(_cfg(chat="qwen3.5:4b"), probe=_probe(True), generate=failing)
    assert record == {"status": "error", "reason": "qwen3.5:4b: model not found"}
    written = _record(tmp_path)
    assert written["since"] == written["checked_at"], "the status changed, so 'since' restarts"
    assert written["last_ok"] is not None, "the last good stamp survives"

    async def hangs(_cfg: Any, model: str) -> Any:
        await asyncio.sleep(3600)

    hm_cap = hm.GENERATION_CAP_S
    hm.GENERATION_CAP_S = 0.05
    try:
        record = await hm.check_once(
            _cfg(chat="qwen3.5:4b"), probe=_probe(True), generate=hangs, persist=False
        )
    finally:
        hm.GENERATION_CAP_S = hm_cap
    assert record is not None and record["status"] == "error"
    assert "did not answer" in record["reason"]


def test_read_record_defaults_without_a_file() -> None:
    assert hm.read_health_record() == {
        "status": "unknown",
        "reason": "",
        "since": None,
        "last_ok": None,
        "checked_at": None,
    }


async def test_schedule_first_run_after_ten_minutes_then_every_n_hours() -> None:
    sleeps: list[float] = []
    checks: list[Any] = []
    gate = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            gate.set()
            await asyncio.sleep(3600)  # park until stop()
        await asyncio.sleep(0)

    async def fake_check(cfg: Any) -> None:
        checks.append(cfg)

    cfg = _cfg(hours=2.0)
    monitor = hm.HealthMonitor(lambda: cfg, sleep=fake_sleep, check=fake_check)
    monitor.start()
    assert monitor.running
    await asyncio.wait_for(gate.wait(), timeout=2.0)
    await monitor.stop()
    assert not monitor.running
    assert sleeps[:3] == [hm.FIRST_RUN_DELAY_S, 7200.0, 7200.0]
    assert monitor.runs == 2 and checks == [cfg, cfg]


async def test_a_failing_check_never_ends_the_schedule() -> None:
    sleeps: list[float] = []
    gate = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            gate.set()
            await asyncio.sleep(3600)
        await asyncio.sleep(0)

    async def boom(_cfg: Any) -> None:
        raise RuntimeError("probe exploded")

    monitor = hm.HealthMonitor(lambda: _cfg(), sleep=fake_sleep, check=boom)
    monitor.start()
    await asyncio.wait_for(gate.wait(), timeout=2.0)
    await monitor.stop()
    assert monitor.runs == 2
