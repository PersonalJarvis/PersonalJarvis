"""Every deterministic spoken command must answer from the CHECKED result —
never silent, never a blind "done". Audit 2026-06-27 found provider_switch,
cancel and depth returned "" (silent) and language_switch spoke success on a
persist failure. These tests are the regression net for the whole class.
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
async def test_main_switch_success_speaks_real_target(monkeypatch) -> None:
    mgr = _manager()

    async def _ok(tier, provider, *, cfg, persist=True):
        assert tier == "brain"
        assert provider == "gemini"
        return {"ok": True, "new_provider": "gemini", "applied_live": True}

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _ok)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_main_provider_switch("gemini")
    assert out
    assert "Gemini" in out


@pytest.mark.asyncio
async def test_main_switch_failure_is_honest(monkeypatch) -> None:
    mgr = _manager()

    async def _fail(tier, provider, *, cfg, persist=True):
        return {
            "ok": False, "error_kind": "missing_credential",
            "error": "Gemini is not configured — its API key is missing.",
        }

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _fail)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_main_provider_switch("gemini")
    assert out
    low = out.lower()
    assert "erledigt" not in low and "done" not in low  # NOT a false success
    assert "gemini" in low  # names what failed


@pytest.mark.asyncio
async def test_main_switch_subagent_only_is_honest(monkeypatch) -> None:
    mgr = _manager()

    async def _fail(tier, provider, *, cfg, persist=True):
        return {"ok": False, "error_kind": "subagent_only", "error": "subagent only"}

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _fail)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    # "gemini" is a valid main-brain alias (so the method reaches the validated
    # switch); the mock returns subagent_only to assert the readback is honest.
    out = await mgr._apply_main_provider_switch("gemini")
    assert out
    assert "erledigt" not in out.lower() and "done" not in out.lower()


@pytest.mark.asyncio
async def test_main_switch_unknown_word_falls_through() -> None:
    mgr = _manager()
    assert await mgr._apply_main_provider_switch("flibberprovider") == ""


def test_cancel_readback_names_count() -> None:
    mgr = _manager()
    phrase = mgr._cancel_readback(2)
    assert phrase and "2" in phrase


def test_cancel_readback_honest_when_nothing_running() -> None:
    mgr = _manager()
    phrase = mgr._cancel_readback(0)
    assert phrase  # never silent
    assert "2" not in phrase  # does not claim it stopped something


@pytest.mark.parametrize("level", ["deep", "fast"])
def test_depth_readback_confirms(level: str) -> None:
    mgr = _manager()
    phrase = mgr._depth_readback(level)
    assert phrase  # never silent


def test_reply_language_persist_failure_is_honest(monkeypatch) -> None:
    mgr = _manager()
    # live switch succeeds, persist raises -> readback must NOT promise "from now on"
    monkeypatch.setattr(mgr, "set_reply_language", lambda code: None)

    def _boom(_lang):
        raise OSError("jarvis.toml is read-only")

    import jarvis.core.config_writer as cw
    monkeypatch.setattr(cw, "set_reply_language", _boom)

    out = mgr._apply_reply_language_switch("en")
    assert out  # never silent
    low = out.lower()
    assert "session" in low or "sitzung" in low or "sesión" in low  # scoped, honest
