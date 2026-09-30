"""Jarvis' learning loop reviews on the Jarvis lead's own seat, and nothing else.

The seat is the front-page chat's: the Agents selection (``[brain.worker]``)
with its provider, model and auth mode. An explicit ``[memory.learning]``
provider overrides it. A seat that fails asks no other provider; the loop's
backoff retries and finally drops the turns. The registry records every
brain it builds, so "nothing else was asked" is checked by instantiation.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from jarvis.memory.learning import compact
from jarvis.memory.learning.review import ModelReviewer
from tests.fakes.fake_seat_registry import RecordingRegistry

ALL = ["openai", "gemini", "claude-api", "claude-cli", "codex", "antigravity", "ollama"]
CHANGES = '{"changes": []}'


@pytest.fixture
def registry(monkeypatch) -> RecordingRegistry:
    from jarvis.brain import resolver
    from jarvis.core import config, runtime_refs

    reg = RecordingRegistry(ALL, replies={name: CHANGES for name in ALL})
    monkeypatch.setattr(resolver, "_get_registry", lambda: reg)
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: f"agent-key-{provider}")
    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("the learning loop asked a subscription resolver")

    monkeypatch.setattr(resolver, "resolve_subscription_brain", forbidden)
    return reg


def _config(*, worker: str = "", model: str = "", provider: str = "", pinned_model: str = ""):
    return SimpleNamespace(
        brain=SimpleNamespace(
            worker=SimpleNamespace(provider=worker, model=model, reasoning_effort="")
            if worker
            else None,
            providers={},
        ),
        memory=SimpleNamespace(
            learning=SimpleNamespace(provider=provider, model=pinned_model, timeout_s=5.0)
        ),
    )


async def test_the_review_runs_on_the_agents_selection_api_seat(registry):
    reviewer = ModelReviewer(_config(worker="openai", model="gpt-chosen"))
    assert await reviewer("{}") == []
    assert [(b.name, b.kwargs) for b in registry.built] == [("openai", {"model": "gpt-chosen"})]
    [answer] = registry.answered
    assert answer.secrets == {"openai": "agent-key-openai"}
    assert answer.caller == "learning"


async def test_a_subscription_selection_reviews_on_that_subscription(registry):
    reviewer = ModelReviewer(_config(worker="codex", model="gpt-5.5"))
    await reviewer("{}")
    [built] = registry.built
    assert built.name == "codex"
    assert built.kwargs["model"] == "gpt-5.5"
    assert built.kwargs["structured_prompts"] is True
    assert registry.answered[0].secrets == {}


@pytest.mark.parametrize(("signed_in", "expected"), [(True, "claude-cli"), (False, "claude-api")])
async def test_the_claude_slot_follows_the_front_page_chat(
    registry, monkeypatch, signed_in, expected
):
    from jarvis.core import task_agent

    monkeypatch.setattr(task_agent, "_claude_subscription_ready", lambda: signed_in)
    await ModelReviewer(_config(worker="claude-api", model="claude-sonnet-5-5"))("{}")
    assert [(b.name, b.kwargs.get("model")) for b in registry.built] == [
        (expected, "claude-sonnet-5-5")
    ]


async def test_an_explicit_learning_provider_is_honoured(registry):
    cfg = _config(worker="openai", model="gpt-chosen", provider="gemini", pinned_model="g-pick")
    await ModelReviewer(cfg)("{}")
    assert [(b.name, b.kwargs) for b in registry.built] == [("gemini", {"model": "g-pick"})]
    # The override uses the provider's ordinary credential, not the Agents-tab slot.
    assert registry.answered[0].secrets == {}


async def test_a_failing_seat_asks_no_other_provider(registry, caplog):
    registry.failing = frozenset({"openai"})
    with caplog.at_level(logging.DEBUG):
        assert await ModelReviewer(_config(worker="openai", model="gpt-chosen"))("{}") is None
    assert registry.built_names() == ["openai"]
    assert "private provider body" not in caplog.text


async def test_no_seat_means_no_call(registry, monkeypatch):
    from jarvis.local_models import assistant_session

    tier = SimpleNamespace(provider="", model="", ready=False, reason="no agent is connected")
    monkeypatch.setattr(assistant_session, "agents_tier", lambda cfg: tier)
    assert await ModelReviewer(_config())("{}") is None
    assert registry.built == []


async def test_compaction_runs_on_the_same_seat(registry):
    registry.replies["gemini"] = '{"merged": [], "outdated": []}'
    compactor = ModelReviewer(
        _config(worker="gemini", model="g-chosen"),
        system=compact.system_prompt,
        parser=compact.parse,
        max_tokens=compact.COMPACT_MAX_TOKENS,
        caller="learning-compaction",
    )
    assert await compactor("{}") == {"merged": [], "outdated": []}
    assert [(b.name, b.kwargs) for b in registry.built] == [("gemini", {"model": "g-chosen"})]
    assert registry.answered[0].caller == "learning-compaction"
