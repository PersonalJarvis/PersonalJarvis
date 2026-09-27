import json
from types import SimpleNamespace

import pytest

from jarvis.core.protocols import BrainDelta
from jarvis.society.capabilities import CapabilityKind, CapabilityRow
from jarvis.society.semantic_routing import _validated_ids, infer_task_focus


def test_only_exact_connected_catalog_ids_survive_a_model_hint():
    allowed = {"plugin:gmail", "core:search-web"}
    assert _validated_ids(
        '{"capability_ids":["plugin:gmail","plugin:unknown","plugin:gmail","core:search-web"]}',
        allowed,
    ) == ["plugin:gmail", "core:search-web"]
    assert _validated_ids('{"capability_ids":"plugin:gmail"}', allowed) == []
    assert _validated_ids("not JSON", allowed) == []


@pytest.mark.parametrize("task_text", ["Resume mi correo.", "今日のメールを要約して。"])
async def test_provider_receives_original_script_and_returns_valid_catalog_hint(
    monkeypatch, task_text: str
):
    import jarvis.agent_chat.runner_brain as runner_brain
    import jarvis.core.config as config
    import jarvis.local_models.assistant_session as assistant_session

    seen = []

    class Provider:
        async def complete(self, request):
            seen.append(json.loads(request.messages[0].content))
            yield BrainDelta(content='{"capability_ids":["plugin:gmail","plugin:unknown"]}')

    class Manager:
        def __init__(self):
            self._brain_cache = {}

        def _get_brain(self, name, model, *, scope):
            provider = Provider()
            self._brain_cache[(f"{name}@{scope}", model)] = provider
            return provider

    manager = Manager()
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: manager)
    monkeypatch.setattr(
        assistant_session,
        "agents_tier",
        lambda _cfg: SimpleNamespace(ready=True, provider="test-provider", model="test-model"),
    )
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda _provider: "")
    catalog = [
        CapabilityRow(
            id="plugin:gmail",
            kind=CapabilityKind.PLUGIN,
            tool_name="gmail",
            label="Gmail",
            one_liner="Read mail",
            risk_tier="safe",
            connected=True,
            aliases=(),
        )
    ]
    runtime = SimpleNamespace(config=lambda: object())

    assert await infer_task_focus(runtime, task_text, catalog) == ["plugin:gmail"]
    assert seen[0]["task"] == task_text
    assert manager._brain_cache == {}


async def test_agents_model_recovers_from_an_unavailable_provider_default(monkeypatch):
    import jarvis.agent_chat.runner_brain as runner_brain
    import jarvis.core.config as config
    import jarvis.local_models.assistant_session as assistant_session

    tried = []

    class Provider:
        def __init__(self, model):
            self.model = model

        async def complete(self, _request):
            if self.model == "unavailable-default":
                raise RuntimeError("unavailable")
            yield BrainDelta(content='{"capability_ids":["plugin:gmail"]}')

    class Manager:
        _brain_cache = {}

        def _get_brain(self, name, model, *, scope):
            tried.append(model)
            provider = Provider(model)
            self._brain_cache[(f"{name}@{scope}", model)] = provider
            return provider

    manager = Manager()
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: manager)
    monkeypatch.setattr(
        assistant_session,
        "agents_tier",
        lambda _cfg: SimpleNamespace(
            ready=True, provider="test-provider", model="working-agent-model"
        ),
    )
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda _provider: "")
    cfg = SimpleNamespace(
        brain=SimpleNamespace(
            providers={"test-provider": SimpleNamespace(model="unavailable-default")}
        )
    )
    catalog = [
        CapabilityRow(
            id="plugin:gmail",
            kind=CapabilityKind.PLUGIN,
            tool_name="gmail",
            label="Gmail",
            one_liner="Read mail",
            risk_tier="safe",
            connected=True,
            aliases=(),
        )
    ]

    assert await infer_task_focus(SimpleNamespace(config=lambda: cfg), "Read my mail", catalog) == [
        "plugin:gmail"
    ]
    assert tried == ["unavailable-default", "working-agent-model"]
    assert manager._brain_cache == {}
