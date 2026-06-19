"""CLI-before-Computer-Use routing.

A connected CLI's capability must suppress the force-spawn that would otherwise
drive a Computer-Use worker (which, for a cloud console, gets stuck on a browser
login). The force-spawn guard historically checked only matching SKILLS
(plugin-* skills), never the connected-CLI capabilities that
``capability_provider.sync_registry`` already registers. The fix makes the guard
also step aside when ``resolve_intent()`` returns a ``source="cli"`` capability —
so a connected gcloud handles "zeig meine Google-Cloud-Kosten" via cli_gcloud
instead of a browser login. Safety invariants preserved: an explicit heavy-work
trigger still spawns, and an utterance no CLI covers still spawns.
"""
from __future__ import annotations

from typing import Any

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.capabilities import Capability, get_registry
from jarvis.core.config import JarvisConfig


class _FakeSpawnTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _InertExecutor:
    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("executor must not run in a classification test")


def _manager() -> BrainManager:
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "permissive"
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={"spawn_worker": _FakeSpawnTool()},
        tool_executor=_InertExecutor(),  # type: ignore[arg-type]
    )


_GCLOUD_CAP = Capability(
    id="cli.gcloud",
    source="cli",
    verbs=("zeig", "lies", "mach", "analysier"),
    objects=("google cloud", "gcp", "kosten", "projekte"),
    description="Google Cloud CLI",
    risk_tier="monitor",
    requires_evidence=True,
)


def test_connected_cli_capability_suppresses_force_spawn() -> None:
    reg = get_registry()
    reg.register(_GCLOUD_CAP)
    try:
        manager = _manager()
        # Permissive mode: without the fix "zeig" (a spawn_verb) → force-spawn
        # → Computer-Use. With the fix the connected cli.gcloud cap covers it.
        assert manager._should_force_spawn(
            "Zeig mir meine Google Cloud Kosten"
        ) is False
    finally:
        reg.deregister("cli.gcloud")


def test_explicit_trigger_still_spawns_despite_cli_capability() -> None:
    # Safety invariant: an explicit heavy-work trigger ("deep dive") wins over
    # the CLI cap — the user explicitly asked for heavy work.
    reg = get_registry()
    reg.register(_GCLOUD_CAP)
    try:
        manager = _manager()
        assert manager._should_force_spawn(
            "Mach einen Deep Dive in meine Google Cloud Kosten"
        ) is True
    finally:
        reg.deregister("cli.gcloud")


def test_action_without_matching_cli_cap_still_spawns() -> None:
    # No CLI cap covers "Bau mir eine Landingpage" → permissive spawn stays.
    # Proves the fix does not over-suppress: only cli-covered intents skip.
    reg = get_registry()
    reg.register(_GCLOUD_CAP)
    try:
        manager = _manager()
        assert manager._should_force_spawn(
            "Bau mir eine Landingpage für mein Startup"
        ) is True
    finally:
        reg.deregister("cli.gcloud")
