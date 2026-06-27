"""The deterministic subagent voice switch must validate + speak honestly.

Forensic 2026-06-27: the voice gate path persisted blindly (no credential check)
and said "Erledigt" even on failure — unlike the REST endpoint and
app_control._switch_subagent, which validate. This routes it through the one
validated apply_provider_switch and renders an honest readback.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


class _FakeExecutor:
    async def execute_confirmed(self, trace_id: UUID, **_):  # pragma: no cover
        return SimpleNamespace(success=True, output="ok", error=None)

    async def cancel_pending(self, trace_id: UUID):  # pragma: no cover
        return True


def _manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    return BrainManager(config=cfg, bus=EventBus(), tools={}, tool_executor=_FakeExecutor())


@pytest.mark.asyncio
async def test_voice_subagent_switch_success_speaks_real_target(monkeypatch) -> None:
    mgr = _manager()

    async def _ok(tier, provider, *, cfg, persist=True):
        assert tier == "subagent"
        assert provider == "openai-codex"  # "codex" mapped to canonical
        return {"ok": True, "new_provider": "openai-codex", "requires_restart": True}

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _ok)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_subagent_provider_switch("codex")
    assert "Codex" in out  # the DISPLAY name of the real target


@pytest.mark.asyncio
async def test_voice_subagent_switch_failure_is_honest(monkeypatch) -> None:
    mgr = _manager()

    async def _fail(tier, provider, *, cfg, persist=True):
        return {
            "ok": False, "error_kind": "missing_credential",
            "error": "Codex is not connected — run 'codex login' first.",
        }

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _fail)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_subagent_provider_switch("codex")
    assert out  # never silent
    low = out.lower()
    assert "erledigt" not in low and "done" not in low  # NOT a false success
    assert "codex" in low  # names what failed


@pytest.mark.asyncio
async def test_voice_subagent_switch_unknown_word_falls_through(monkeypatch) -> None:
    mgr = _manager()
    # an unmapped spoken word returns "" so the caller falls through to the brain
    assert await mgr._apply_subagent_provider_switch("flibberprovider") == ""
