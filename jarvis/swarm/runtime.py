"""Composition and idempotent installed-state preparation for all launch modes."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def swarm_root(cfg: Any = None) -> Path:
    explicit = os.environ.get("JARVIS_DATA_DIR", "").strip()
    configured = getattr(getattr(cfg, "memory", None), "data_dir", None)
    return Path(explicit or configured or "data").expanduser().resolve() / "swarm"


def recovery_needed(cfg: Any = None) -> bool:
    """Detect prior opt-in without touching ordinary-agent storage or the network."""
    from .settings import DistributedSettings

    root = swarm_root(cfg)
    return (root / "catalog.sqlite3").is_file() or bool(DistributedSettings(root).load()["enabled"])


def prepare_install(cfg: Any = None, *, verify_sandbox: bool = True) -> dict[str, Any]:
    """Validate bundled prerequisites and migrate only already opted-in stores.

    Normal installs/updates call this automatically. Frozen and portable apps
    additionally call it after readiness. A new ordinary-agent install creates
    no team catalog, team database, memory collector or external infrastructure.
    """
    with sqlite3.connect(":memory:") as connection:
        if connection.execute("SELECT json_valid('{\"ready\":true}')").fetchone()[0] != 1:
            raise RuntimeError("The installed SQLite runtime needs JSON support")
    if verify_sandbox:
        from .sandbox import WasmSandbox

        capability = WasmSandbox().capability()
        if not capability["available"]:
            raise RuntimeError("Swarm sandbox installation is incomplete: " + capability["reason"])
    root = swarm_root(cfg)
    migrated = 0
    unavailable: list[str] = []
    if (root / "catalog.sqlite3").is_file():
        from .store import TeamRegistry

        registry = TeamRegistry(root)
        registry.provision()
        offset = 0
        while True:
            records = registry.list(limit=200, offset=offset)
            for team in records:
                if team.get("available") is False:
                    unavailable.append(team["id"])
                    continue
                try:
                    registry.open(team["id"]).check_schema()
                    migrated += 1
                except Exception:  # noqa: BLE001 - one damaged team cannot block healthy recovery
                    log.exception("Team %s needs storage recovery", team["id"])
                    unavailable.append(team["id"])
            if len(records) < 200:
                break
            offset += len(records)
    result: dict[str, Any] = {"local_ready": True, "migrated_teams": migrated}
    if unavailable:
        result["unavailable_teams"] = unavailable
    return result


def build_service(cfg: Any, *, control_bus: Any = None) -> Any:
    """Late composition only; no global mission bus or ordinary-agent store."""
    from jarvis.brain.swarm_factory import SwarmBrainFactory
    from jarvis.control.cancel import get_kill_switch
    from jarvis.core.bus import EventBus, get_default_bus
    from jarvis.safety.approval import ApprovalWorkflow
    from jarvis.safety.risk_tier import RiskTierEvaluator
    from jarvis.safety.tool_executor import ToolExecutor

    from .concurrency import SwarmExecutors
    from .dependencies import DependencyInstaller
    from .egress import EgressClient
    from .sandbox import WasmSandbox
    from .service import LocalSwarmService

    # Internal Action events stay off the ordinary application/Society bus.
    # Durable, scoped Swarm records provide the user-facing audit stream.
    bus = EventBus()
    kill_switch = get_kill_switch()
    kill_switch.bind(control_bus if control_bus is not None else get_default_bus())
    executor = ToolExecutor(bus, RiskTierEvaluator(cfg.safety), ApprovalWorkflow(bus))
    egress = EgressClient()
    storage = SwarmExecutors()
    return LocalSwarmService(
        swarm_root(cfg),
        brain_factory=SwarmBrainFactory(cfg, offload=storage.call),
        sandbox=WasmSandbox(offload=storage.sandbox),
        tool_executor=executor,
        egress=egress,
        dependencies=DependencyInstaller(egress),
        storage=storage,
        kill_switch=kill_switch,
    )
