"""Main-Jarvis integration. Plan §4.3 + §9.

Public API for the main-Jarvis main entry point::

    from jarvis.overlay.integration import start_overlay, stop_overlay

    async def main():
        await start_overlay()
        try:
            ... # FastAPI / DesktopApp / etc.
        finally:
            await stop_overlay()

Hooks:
- reads the ``[overlay]`` section from jarvis.toml.
- spawns ``OverlaySupervisor`` when ``overlay.enabled = true``.
- wires OverlayBridge into the WS-server pipeline (dedicated WS listener
  on ``ws_port``). The subprocess connects as a client.
- Sub-agent detection (JARVIS_DEPTH > 0) -> everything becomes a no-op.

Idempotent: calling ``start_overlay()`` more than once does nothing after
the first call (singleton pattern). ``stop_overlay()`` can be called
multiple times.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .bridge import (
    NoOpOverlayBridge,
    OverlayBridge,
    is_sub_agent_process,
)
from .server import IPCServerHandle, start_ipc_server
from .supervisor import OverlaySupervisor

logger = logging.getLogger(__name__)


# Singleton state.
_bridge: Optional[OverlayBridge | NoOpOverlayBridge] = None
_supervisor: Optional[OverlaySupervisor] = None
_ws_server_task: Optional[asyncio.Task] = None
_ipc_server_handle: Optional[IPCServerHandle] = None


def _read_overlay_config() -> dict:
    """Reads ``[overlay]`` directly from jarvis.toml (tomllib).

    Not via JarvisConfig (Pydantic), because the top-level model does not
    yet whitelist the overlay section — it would be silently swallowed.
    Direct TOML parsing bypasses that.
    """
    import tomllib
    from pathlib import Path

    try:
        from jarvis.core.config import DEFAULT_CONFIG_FILE

        toml_path = Path(DEFAULT_CONFIG_FILE)
    except Exception:  # noqa: BLE001
        toml_path = Path("jarvis.toml")

    if not toml_path.is_file():
        return {"enabled": False}

    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        logger.debug("jarvis.toml not parseable", exc_info=True)
        return {"enabled": False}

    section = data.get("overlay") or {}
    return {
        "enabled": bool(section.get("enabled", False)),
        "ws_port": int(section.get("ws_port", 7842)),
        "ws_host": str(section.get("ws_host", "127.0.0.1")),
    }


def is_overlay_enabled() -> bool:
    """Plan §9.1 public API."""
    if is_sub_agent_process():
        return False
    cfg = _read_overlay_config()
    return cfg.get("enabled", False)


def get_overlay() -> Optional[OverlayBridge | NoOpOverlayBridge]:
    """Plan §9.1 public API. Singleton accessor.

    In sub-agent processes (JARVIS_DEPTH>0) returns a NoOp stub.
    Production: returns None when ``start_overlay`` has never been called.
    """
    if is_sub_agent_process():
        # Sub-agent: every get_overlay() call returns a fresh stub.
        # Idempotent — the stub has no state.
        return NoOpOverlayBridge()
    return _bridge


def get_overlay_supervisor() -> Optional[OverlaySupervisor]:
    """Plan §9.1 — singleton accessor for the OverlaySupervisor.

    Returns ``None`` when overlay is disabled, when running as a
    sub-agent (JARVIS_DEPTH > 0), or when ``start_overlay`` has not yet
    been called. Used by the ``respawn_mascot`` voice-recovery tool to
    force a fresh subprocess spawn after a hide / hang / cap-fire.
    """
    if is_sub_agent_process():
        return None
    return _supervisor


def set_overlay(bridge: Optional[OverlayBridge | NoOpOverlayBridge]) -> None:
    """Test hook. Allows test code to inject a mock bridge.

    Do not call from production code.
    """
    global _bridge
    _bridge = bridge


async def start_overlay(
    bus: Optional[Any] = None,
) -> Optional[OverlayBridge | NoOpOverlayBridge]:
    """Plan §9.1 — Idempotent startup hook for the main-Jarvis main entry.

    Returns:
      - ``None`` when overlay is disabled or the process is a sub-agent.
      - ``NoOpOverlayBridge`` when running as a sub-agent (interface-symmetric).
      - ``OverlayBridge`` when enabled and the subprocess has been spawned.

    ``bus`` — optional. When supplied, mascot-originated user events
    (currently: doubleClick → mute toggle) are republished onto the
    EventBus as ``VoiceMuteToggleRequested`` so the speech pipeline can
    react. If omitted (e.g. unit tests, sub-agent processes) the inbound
    mascot events are still logged but not acted upon.
    """
    global _bridge, _supervisor, _ipc_server_handle

    if is_sub_agent_process():
        logger.info("Overlay: sub-agent process -> NoOp stub")
        _bridge = NoOpOverlayBridge()
        return _bridge

    cfg = _read_overlay_config()
    if not cfg.get("enabled", False):
        logger.info("Overlay: disabled via config")
        return None

    if _bridge is not None and _supervisor is not None:
        return _bridge  # idempotent

    bridge = OverlayBridge()
    # bridge.start() is idempotent — start_ipc_server calls it itself.
    # The WS server MUST be listening before the subprocess is spawned,
    # otherwise the subprocess IPC client can never connect and will not
    # send heartbeats. Consequence: the supervisor sees a 3 s heartbeat
    # timeout and kill+respawns; after 6 attempts the cap fires and the
    # overlay goes completely offline — the user would then see no visual
    # feedback even when the voice backend triggers.
    ws_host = cfg.get("ws_host", "127.0.0.1")
    ws_port = cfg.get("ws_port", 7842)
    try:
        ipc_handle = await start_ipc_server(
            host=ws_host,
            port=ws_port,
            bridge=bridge,
        )
        _ipc_server_handle = ipc_handle
        logger.info(
            "Overlay WS server live on ws://%s:%d", ipc_handle.host, ipc_handle.port
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Overlay WS server failed to start -> subprocess will report heartbeat timeouts"
        )
        # Bridge stays active; supervisor will kill+respawn.
        # We don't give up, because _read_overlay_config might try a different port.

    supervisor = OverlaySupervisor(ws_port=ws_port)

    # Heartbeat wiring: the WS incoming handler calls notify_heartbeat.
    # Defensive: every inbound message counts as a lifesign, not
    # just HeartbeatEnvelopes. This way a discriminator mismatch or
    # schema drift can't blind the supervisor — as long as something
    # arrives from the subprocess, it counts as alive. Pragmatic fallback:
    # the type check stays in for forensics (logging), the notify is universal.
    async def _on_inbound(envelope) -> None:
        type_name = type(envelope).__name__
        # Lifesign notify FIRST (always, not just for heartbeat) —
        # so the supervisor stays alive even if the schema/discriminator
        # accidentally rotates.
        supervisor.notify_heartbeat()
        if type_name != "HeartbeatEnvelope":
            logger.debug(
                "Overlay WS inbound: %s (lifesign-notify done)", type_name
            )

        # Mascot-originated user interaction: doubleClick → mute toggle.
        # We republish on the EventBus so the speech pipeline can decide
        # the new mute state and broadcast it to other listeners.
        if type_name == "MascotEventEnvelope" and bus is not None:
            kind = getattr(getattr(envelope, "payload", None), "kind", None)
            if kind == "mute_toggle":
                try:
                    from jarvis.core.events import VoiceMuteToggleRequested

                    await bus.publish(
                        VoiceMuteToggleRequested(source="mascot_dblclick")
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("VoiceMuteToggleRequested publish failed")

    bridge.add_inbound_handler(_on_inbound)

    try:
        await supervisor.start()
    except Exception:  # noqa: BLE001
        logger.exception("OverlaySupervisor.start failed -> bridge stays running")
        # Leave the bridge as-is; the user can manually re-enable it.

    _bridge = bridge
    _supervisor = supervisor
    return bridge


async def stop_overlay() -> None:
    """Plan §9.1 — idempotent shutdown."""
    global _bridge, _supervisor, _ipc_server_handle

    if _supervisor is not None:
        try:
            await _supervisor.stop()
        except Exception:  # noqa: BLE001
            logger.exception("supervisor.stop raised")
        _supervisor = None

    if _ipc_server_handle is not None:
        try:
            await _ipc_server_handle.stop()
        except Exception:  # noqa: BLE001
            logger.exception("ipc_server.stop raised")
        _ipc_server_handle = None

    if _bridge is not None and isinstance(_bridge, OverlayBridge):
        try:
            await _bridge.stop()
        except Exception:  # noqa: BLE001
            logger.exception("bridge.stop raised")
    _bridge = None


__all__ = [
    "get_overlay",
    "get_overlay_supervisor",
    "is_overlay_enabled",
    "set_overlay",
    "start_overlay",
    "stop_overlay",
]
