"""Owner commands expand through the existing router loader without recursive grants."""

from types import SimpleNamespace

import pytest

from jarvis.brain.factory import ROUTER_TOOLS, _load_tools_for_tier
from jarvis.brain.manager import BrainManager
from jarvis.commands.registry import is_swarm_owner_request
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


def test_router_expands_swarm_owner_commands_under_existing_loader():
    tools = _load_tools_for_tier(
        "router",
        bus=EventBus(),
        executor=None,
        harness_manager=None,
        user_profile=None,
        people=None,
        config=JarvisConfig(),
    )
    assert "app-command" in ROUTER_TOOLS
    assert not any(name.startswith("swarm-") for name in ROUTER_TOOLS)
    assert "app-command" not in tools
    assert tools["swarm-create"].risk_tier == "ask"
    assert tools["swarm-control"].risk_tier == "ask"
    assert tools["swarm-list"].risk_tier == "monitor"
    assert "spawn_worker" not in tools["swarm-control"].schema["properties"]["action"]["enum"]


@pytest.mark.parametrize("mode", ["strict", "balanced", "permissive"])
@pytest.mark.parametrize(
    "utterance",
    [
        "Start an Ultra Agent Swarm to research suppliers, compare their prices, "
        "and write a report with sources",
        "Create a swarm with three workers to research suppliers and write a report",
        "Starte einen Ultra Agent Swarm für eine Recherche mit Quellen",  # i18n-allow
        "Crea un Swarm para investigar proveedores y escribir un informe",  # i18n-allow
    ],
)
def test_explicit_swarm_request_never_becomes_a_generic_mission(monkeypatch, mode, utterance):
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = mode
    manager = BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": SimpleNamespace(name="spawn_worker", schema={})},
        tool_executor=SimpleNamespace(),
    )
    monkeypatch.setattr(manager, "_heavy_worker_provider_viable", lambda: True)
    assert is_swarm_owner_request(utterance)
    assert not manager._should_force_spawn(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        "Research suppliers, compare their prices and write a report with sources",
        "Create a swarm simulator for a biology lesson",
        "Explain what Ultra Agent Swarm does",
        "Do not create a swarm; answer directly",
        "Implement a button that lets users create a swarm",
    ],
)
def test_stand_down_never_infers_swarm_opt_in_from_ordinary_content(utterance):
    assert not is_swarm_owner_request(utterance)
