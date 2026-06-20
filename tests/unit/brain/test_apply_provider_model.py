"""BrainManager.apply_provider_model — live in-memory model override.

The API-Keys model picker writes the chosen model to jarvis.toml (durable) AND
calls this method so the running brain uses it on the very next turn without a
restart. The method mutates the manager's OWN config (the brain builds its config
separately from app.state.config) and drops cached brain instances for that
provider so the new model is instantiated. It returns whether the provider is the
currently active brain — the route reports that as ``applied_live``.
"""
from __future__ import annotations

from typing import Any

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig


class _FakeTool:
    name = "noop"
    schema: dict[str, Any] = {}


class _InertExecutor:
    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("executor must not run here")


def _manager(primary: str = "gemini") -> BrainManager:
    config = JarvisConfig()
    config.brain.primary = primary
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={},
        tool_executor=_InertExecutor(),  # type: ignore[arg-type]
    )


def test_active_provider_returns_true_and_sets_model() -> None:
    mgr = _manager(primary="gemini")
    is_active = mgr.apply_provider_model("gemini", "gemini-3.1-pro-preview")
    assert is_active is True
    assert mgr._config.brain.providers["gemini"].model == "gemini-3.1-pro-preview"


def test_inactive_provider_returns_false_but_still_persists_in_memory() -> None:
    mgr = _manager(primary="gemini")
    is_active = mgr.apply_provider_model("openai", "gpt-5.5")
    assert is_active is False
    assert mgr._config.brain.providers["openai"].model == "gpt-5.5"


def test_creates_provider_block_when_absent() -> None:
    mgr = _manager(primary="gemini")
    assert "grok" not in mgr._config.brain.providers
    mgr.apply_provider_model("grok", "grok-4.3")
    assert mgr._config.brain.providers["grok"].model == "grok-4.3"


def test_existing_block_is_updated_not_replaced_fields() -> None:
    mgr = _manager(primary="gemini")
    mgr._config.brain.providers["gemini"] = BrainProviderConfig(
        model="old", deep_model="keep-me"
    )
    mgr.apply_provider_model("gemini", "gemini-3-flash-preview")
    pc = mgr._config.brain.providers["gemini"]
    assert pc.model == "gemini-3-flash-preview"
    assert pc.deep_model == "keep-me"  # untouched


def test_empty_model_resets_to_none() -> None:
    mgr = _manager(primary="gemini")
    mgr._config.brain.providers["gemini"] = BrainProviderConfig(model="pinned")
    mgr.apply_provider_model("gemini", "")
    assert mgr._config.brain.providers["gemini"].model is None


def test_drops_cached_brain_instances_for_that_provider() -> None:
    mgr = _manager(primary="gemini")
    mgr._brain_cache[("gemini", "old-model")] = object()  # type: ignore[assignment]
    mgr._brain_cache[("openai", "gpt-5.5")] = object()  # type: ignore[assignment]
    mgr.apply_provider_model("gemini", "gemini-3-flash-preview")
    assert not any(k[0] == "gemini" for k in mgr._brain_cache)
    # Other providers' caches are left alone.
    assert ("openai", "gpt-5.5") in mgr._brain_cache
