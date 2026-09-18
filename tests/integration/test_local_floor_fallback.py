"""A hosted brain that is rate-limited / over quota falls back to the local server."""

from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from tests.fixtures.brain.fake_brain import FakeBrain


class _QuotaBrain(FakeBrain):
    async def complete(self, req):  # type: ignore[override]
        self.calls.append(req)
        raise RuntimeError("429 RESOURCE_EXHAUSTED: daily quota exceeded")
        yield  # pragma: no cover - makes this an async generator


def _manager(base_url: str) -> BrainManager:
    config = JarvisConfig()
    config.brain.primary = "gemini"
    config.brain.providers["gemini"] = BrainProviderConfig(model="g-fast")
    config.brain.providers["local-openai"] = BrainProviderConfig(
        model="Qwen3.5-4B-Q4_K_M", base_url=base_url
    )
    manager = BrainManager(config=config, bus=EventBus(), tools={})
    manager._registry._loaded = True
    manager._registry._classes["gemini"] = FakeBrain
    manager._registry._classes["local-openai"] = FakeBrain
    return manager


def test_local_floor_is_last_stage_when_a_server_is_configured() -> None:
    manager = _manager("http://127.0.0.1:18181")
    chain = manager._build_fallback_chain("fast")
    assert chain[-1] == ("local-openai", "Qwen3.5-4B-Q4_K_M")


def test_no_local_floor_without_a_server_url() -> None:
    manager = _manager("")
    chain = manager._build_fallback_chain("fast")
    assert all(name != "local-openai" for name, _ in chain)


@pytest.mark.asyncio
async def test_quota_error_on_hosted_brain_is_answered_locally() -> None:
    manager = _manager("http://127.0.0.1:18181")
    hosted = _QuotaBrain()
    local = FakeBrain(text_response="ローカルです")
    manager._brain_cache[("gemini", "g-fast")] = hosted
    manager._brain_cache[("local-openai", "Qwen3.5-4B-Q4_K_M")] = local
    manager._build_fallback_chain = lambda level: [
        ("gemini", "g-fast"),
        manager._local_floor(level),
    ]
    result = await manager.generate("こんにちは", use_history=False)
    assert "ローカル" in result
    assert len(hosted.calls) == 1 and len(local.calls) == 1


class _CompactBrain(FakeBrain):
    compact_prompt = True


@pytest.mark.asyncio
async def test_prewarm_sends_the_turn_prefix_once_for_compact_brains() -> None:
    manager = _manager("http://127.0.0.1:18181")
    manager._active_name = "local-openai"
    brain = _CompactBrain()
    manager._get_brain = lambda name, model=None, **_kw: brain
    assert await manager.prewarm_prompt_cache() is True
    assert len(brain.calls) == 1
    req = brain.calls[0]
    assert req.max_tokens == 1 and req.system


@pytest.mark.asyncio
async def test_prewarm_is_a_no_op_for_hosted_brains() -> None:
    manager = _manager("http://127.0.0.1:18181")
    brain = FakeBrain()
    manager._get_brain = lambda name, model=None, **_kw: brain
    assert await manager.prewarm_prompt_cache() is False
    assert brain.calls == []


def test_japanese_turn_pins_a_japanese_reply_directive() -> None:
    manager = _manager("http://127.0.0.1:18181")
    # i18n-allow: Japanese input under test
    manager._update_turn_language("おはよう。今日もよろしく。")  # i18n-allow
    assert "Always reply in Japanese" in manager._reply_language_directive()
    manager._update_turn_language("Good morning, how are you today?")
    assert "Japanese" not in manager._reply_language_directive()


def test_active_brain_without_a_configured_model_still_leads_the_chain() -> None:
    config = JarvisConfig()
    config.brain.primary = "local-openai"
    config.brain.providers["local-openai"] = BrainProviderConfig(base_url="http://127.0.0.1:18181")
    manager = BrainManager(config=config, bus=EventBus(), tools={})
    manager._registry._loaded = True
    manager._registry._classes["local-openai"] = FakeBrain
    chain = manager._build_fallback_chain("fast")
    assert chain and chain[0][0] == "local-openai"
