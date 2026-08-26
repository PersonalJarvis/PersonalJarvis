"""Autostart: the local server comes up with Jarvis only when wanted AND used.

What would lie if it broke: a fresh install that never chose local models
spawning ``ollama serve`` at boot; a switched-off autostart starting anyway;
a remote server being "started"; an uninstalled one being installed; a boot
task that raises into the lifespan.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.core.config import BrainProviderConfig, JarvisConfig, OllamaModelOptions
from jarvis.local_models import autostart


def _cfg(
    *,
    primary: str = "openrouter",
    chat: str = "",
    embedding: str = "",
    flag: bool | None = None,
) -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.primary = primary
    provider = BrainProviderConfig(model=chat)
    if flag is not None:
        provider.autostart = flag
    cfg.brain.providers["ollama"] = provider
    if embedding:
        cfg.ultrawiki.embedding_provider = "ollama"
        cfg.ultrawiki.embedding_model = embedding
    return cfg


def test_nothing_is_in_use_on_a_fresh_install() -> None:
    used, why = autostart.in_use(_cfg())
    assert used is False
    assert "no role" in why
    start, reason = autostart.should_autostart(_cfg())
    assert start is False and reason == why


def test_the_active_brain_or_a_configured_role_counts_as_in_use() -> None:
    assert autostart.in_use(_cfg(primary="ollama")) == (True, "the active brain")
    used, why = autostart.in_use(_cfg(chat="qwen3.5:4b"))
    assert used and why == "the chat role"
    used, why = autostart.in_use(_cfg(chat="qwen3.5:4b", embedding="embeddinggemma"))
    assert used and why == "the chat, embedding roles"


def test_the_switch_off_wins_over_use() -> None:
    start, reason = autostart.should_autostart(_cfg(primary="ollama", flag=False))
    assert start is False and "switched off" in reason
    start, reason = autostart.should_autostart(_cfg(primary="ollama"))
    assert start is True and "the active brain" in reason


def test_the_default_is_on_even_without_an_ollama_card() -> None:
    from jarvis.core.config import ollama_autostart

    cfg = JarvisConfig()
    cfg.brain.providers.pop("ollama", None)
    assert ollama_autostart(cfg) is True
    assert autostart.should_autostart(cfg)[0] is False  # on, but nothing uses it


@pytest.mark.asyncio
async def test_run_once_starts_a_stopped_server_and_warms_the_chat_pick() -> None:
    cfg = _cfg(chat="qwen3.5:4b")
    cfg.brain.providers["ollama"].models["qwen3.5:4b"] = OllamaModelOptions(keep_alive="2h")
    started: list[str] = []
    warmed: list[tuple[str, str, Any]] = []

    async def _warm(root: str, model: str, keep_alive: Any) -> bool:
        warmed.append((root, model, keep_alive))
        return True

    record = await autostart.run_once(
        cfg,
        status=lambda: {"installed": True, "running": False},
        start=lambda: (started.append("go") or True, "Ollama started."),
        warm=_warm,
    )
    assert started == ["go"]
    assert record == {"started": True, "warmed": "qwen3.5:4b", "detail": "Ollama started."}
    # The pick's own keep-alive is what the warm ping asks for.
    assert warmed[0][1:] == ("qwen3.5:4b", "2h")


@pytest.mark.asyncio
async def test_run_once_only_warms_a_running_server() -> None:
    warmed: list[str] = []

    async def _warm(_root: str, model: str, keep_alive: Any) -> bool:
        warmed.append(f"{model}:{keep_alive}")
        return True

    record = await autostart.run_once(
        _cfg(chat="qwen3.5:4b"),
        status=lambda: {"installed": True, "running": True},
        start=lambda: (_ for _ in ()).throw(AssertionError("must not start")),
        warm=_warm,
    )
    assert record["started"] is False and record["warmed"] == "qwen3.5:4b"
    assert warmed == [f"qwen3.5:4b:{autostart.DEFAULT_KEEP_ALIVE}"]


@pytest.mark.asyncio
async def test_run_once_never_installs_and_never_starts_a_remote_server() -> None:
    absent = await autostart.run_once(
        _cfg(chat="qwen3.5:4b"),
        status=lambda: {"installed": False, "running": False},
        start=lambda: (_ for _ in ()).throw(AssertionError("must not start")),
    )
    assert absent["started"] is False and "not installed" in absent["detail"]

    cfg = _cfg(chat="qwen3.5:4b")
    cfg.brain.providers["ollama"].base_url = "http://box.lan:11434"
    remote = await autostart.run_once(
        cfg,
        status=lambda: {"installed": False, "running": False},
        start=lambda: (_ for _ in ()).throw(AssertionError("must not start")),
    )
    assert remote["started"] is False and "remote" in remote["detail"]


@pytest.mark.asyncio
async def test_run_once_reports_a_server_that_will_not_come_up() -> None:
    record = await autostart.run_once(
        _cfg(chat="qwen3.5:4b"),
        status=lambda: {"installed": True, "running": False},
        start=lambda: (False, "Ollama did not come up within 20 seconds."),
        warm=lambda *_a: (_ for _ in ()).throw(AssertionError("must not warm")),
    )
    assert record["started"] is False and "did not come up" in record["detail"]


@pytest.mark.asyncio
async def test_schedule_waits_then_decides_and_never_raises() -> None:
    slept: list[float] = []
    ran: list[str] = []

    async def _sleep(s: float) -> None:
        slept.append(s)

    async def _run(cfg: Any) -> dict[str, Any]:
        ran.append(cfg.brain.primary)
        return {"started": True, "warmed": "", "detail": ""}

    # Not in use: the run is skipped after the delay.
    await autostart.schedule(lambda: _cfg(), sleep=_sleep, run=_run)
    assert slept == [autostart.BOOT_DELAY_S] and ran == []

    # In use: the run happens.
    await autostart.schedule(lambda: _cfg(primary="ollama"), delay_s=0.0, sleep=_sleep, run=_run)
    assert ran == ["ollama"]

    # A failing run is logged, not raised into the lifespan.
    async def _boom(_cfg: Any) -> dict[str, Any]:
        raise RuntimeError("no binary")

    task = autostart.schedule(lambda: _cfg(primary="ollama"), delay_s=0.0, sleep=_sleep, run=_boom)
    await task
    assert task.exception() is None


@pytest.mark.asyncio
async def test_kick_runs_now_inside_a_loop() -> None:
    ran: list[str] = []

    async def _run(cfg: Any) -> dict[str, Any]:
        ran.append(cfg.brain.primary)
        return {"started": False, "warmed": "", "detail": ""}

    task = autostart.kick(_cfg(primary="ollama"), run=_run)
    assert task is not None
    await task
    assert ran == ["ollama"]


def test_kick_is_a_no_op_outside_a_loop() -> None:
    assert autostart.kick(_cfg(primary="ollama")) is None
    # Nothing left behind for the next loop to trip over.
    assert not any(not t.done() for t in autostart._tasks)


@pytest.mark.asyncio
async def test_the_brain_switch_readies_the_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.brain import app_control

    kicked: list[str] = []
    monkeypatch.setattr(autostart, "kick", lambda cfg, **_kw: kicked.append(cfg.brain.primary))
    app_control._ready_local_server("ollama", _cfg(primary="ollama"))
    app_control._ready_local_server("openrouter", _cfg())
    assert kicked == ["ollama"]
    await asyncio.sleep(0)
