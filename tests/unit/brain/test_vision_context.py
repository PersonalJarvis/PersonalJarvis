"""Tests fuer ``jarvis.brain.vision_context`` (Phase 5).

Mandat-Anforderungen:
  - 3 Cases: VS Code Foreground / Browser Foreground / Vision-Context disabled
  - Failure-Mode 4: pywinauto-Crash -> kein Hint, kein Spawn-Block
  - Latenz-Budget 250 ms — Timeout muss greifen
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from jarvis.brain.vision_context import (
    get_active_window_hint,
    is_enabled,
)
from jarvis.core.config import VisionContextConfig
from jarvis.core.protocols import Observation


# ---------------------------------------------------------------------------
# Helper: Fake VisionEngine mit kontrollierbaren Ergebnissen
# ---------------------------------------------------------------------------


class _FakeEngine:
    def __init__(
        self,
        *,
        window_title: str = "",
        active_pid: int | None = None,
        delay_s: float = 0.0,
        crash: Exception | None = None,
    ) -> None:
        self.window_title = window_title
        self.active_pid = active_pid
        self.delay_s = delay_s
        self.crash = crash
        self.observe_calls = 0

    async def observe(self, *, mode: str = "auto", **_: object) -> Observation:
        self.observe_calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.crash is not None:
            raise self.crash
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=0,
            screenshot_path=None,
            screenshot_hash="x",
            nodes=(),
            window_title=self.window_title,
            active_pid=self.active_pid or 0,
            source="ui_tree_only",
        )


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


def test_is_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: kein ENV, kein Config-Flag -> deaktiviert."""
    monkeypatch.delenv("JARVIS_VISION_CONTEXT", raising=False)
    assert is_enabled() is False
    assert is_enabled(VisionContextConfig()) is False


def test_is_enabled_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """``JARVIS_VISION_CONTEXT=1`` aktiviert Phase 5 ueber den Config-Flag hinaus."""
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "1")
    assert is_enabled() is True
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "true")
    assert is_enabled() is True
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "no")
    assert is_enabled() is False


def test_is_enabled_config_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """``[vision].context_hint_on_spawn = true`` aktiviert Phase 5."""
    monkeypatch.delenv("JARVIS_VISION_CONTEXT", raising=False)
    cfg = VisionContextConfig(context_hint_on_spawn=True)
    assert is_enabled(cfg) is True


# ---------------------------------------------------------------------------
# 3 Mandat-Cases: VS Code, Browser, Disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vscode_foreground_yields_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """VS Code Foreground + 'Bau ne Landingpage' -> hint enthaelt 'code'/'Visual Studio Code'."""
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "1")
    engine = _FakeEngine(
        window_title="phase5.py - Visual Studio Code",
        active_pid=4242,
    )

    # psutil mocken — wir wollen kein echtes psutil-Lookup auf 4242.
    import jarvis.brain.vision_context as mod
    monkeypatch.setattr(mod, "_process_name_for_pid", lambda pid: "Code.exe")

    hint = await get_active_window_hint(engine=engine)
    assert hint is not None
    low = hint.lower()
    assert "code" in low or "visual studio" in low
    assert engine.observe_calls == 1


@pytest.mark.asyncio
async def test_browser_foreground_yields_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chrome Foreground + 'Recherchier X' -> hint enthaelt 'chrome'/'firefox'."""
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "1")
    engine = _FakeEngine(
        window_title="ChatGPT - Google Chrome",
        active_pid=1234,
    )
    import jarvis.brain.vision_context as mod
    monkeypatch.setattr(mod, "_process_name_for_pid", lambda pid: "chrome.exe")

    hint = await get_active_window_hint(engine=engine)
    assert hint is not None
    low = hint.lower()
    assert "chrome" in low or "firefox" in low


@pytest.mark.asyncio
async def test_vision_context_disabled_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default (kein ENV, kein Config-Flag) -> None, kein Engine-Call.

    Backward-Compat: ohne explizite Aktivierung kostet Phase 5 keinen
    Latenz-Cycle.
    """
    monkeypatch.delenv("JARVIS_VISION_CONTEXT", raising=False)
    engine = _FakeEngine(window_title="something", active_pid=1)
    hint = await get_active_window_hint(engine=engine)
    assert hint is None
    assert engine.observe_calls == 0


# ---------------------------------------------------------------------------
# Failure-Mode 4: pywinauto-Crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pywinauto_crash_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """observe() crasht (Failure-Mode 4) -> kein Hint, kein Re-Raise."""
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "1")
    engine = _FakeEngine(crash=RuntimeError("pywinauto: GetForegroundWindow failed"))

    hint = await get_active_window_hint(engine=engine)
    assert hint is None


# ---------------------------------------------------------------------------
# Latenz-Cap (Mandat 250 ms)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """observe() haengt > timeout_s -> None, kein Hang."""
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "1")
    # Fake-Engine mit 500 ms-Delay, Timeout auf 50 ms gesetzt.
    engine = _FakeEngine(window_title="Slow App", active_pid=1, delay_s=0.5)
    cfg = VisionContextConfig(context_hint_on_spawn=True, timeout_s=0.05)

    hint = await get_active_window_hint(engine=engine, config=cfg)
    assert hint is None


# ---------------------------------------------------------------------------
# Edge-Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_window_no_pid_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observation ohne window_title und ohne pid -> None statt Pseudo-Hint."""
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "1")
    engine = _FakeEngine(window_title="", active_pid=None)
    hint = await get_active_window_hint(engine=engine)
    assert hint is None


@pytest.mark.asyncio
async def test_window_only_no_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nur window_title verfuegbar -> hint ohne Process-Name."""
    monkeypatch.setenv("JARVIS_VISION_CONTEXT", "1")
    engine = _FakeEngine(window_title="Nur Titel", active_pid=None)
    hint = await get_active_window_hint(engine=engine)
    assert hint is not None
    assert "Nur Titel" in hint
