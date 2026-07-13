"""Tool-surface fingerprint + self-heal reconcile for the BrainManager.

The BrainManager's tool snapshot is refreshed event-driven: a CLI, plugin, or
MCP source that connects publishes ``BrainToolsChanged`` and the manager
reloads. Event-driven alone is fragile — a source that becomes ready AFTER the
last refresh of a boot, or whose completion event is lost (a wedged plugin
bootstrap, live 2026-07-13), leaves the snapshot stale for the WHOLE session.
The model then refuses with "tool not available" while the very same request
works after the next restart — the intermittent-availability bug class.

``maybe_reconcile_tool_surface`` runs at the head of every turn: it compares a
cheap names-only fingerprint of the live sources (in-process caches, no IO, no
LLM — AP-11 safe) against the fingerprint stamped at the last tool load, and
triggers one ``refresh_tools()`` on drift. Whatever upstream event went
missing, the tool surface converges at the next turn.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def live_tool_surface_fingerprint() -> frozenset[str] | None:
    """Names-only snapshot of the dynamic tool sources (CLI / plugin / MCP).

    Reads the shared in-process registries' caches synchronously — never
    starts a client, never awaits network IO — so it is cheap enough for the
    per-turn hot path. Each source is guarded on its own: a broken source
    contributes nothing but never blinds the others. Returns ``None`` when no
    source registry is reachable at all (headless test builds), which disables
    the reconcile instead of comparing empty-vs-empty forever.
    """
    readable = False
    names: set[str] = set()

    try:
        from jarvis.clis.shared import get_active_registry

        cli_registry = get_active_registry()
        if cli_registry is not None:
            readable = True
            for tool in cli_registry.active_tools():
                names.add(f"cli:{getattr(tool, 'name', '')}")
    except Exception:  # noqa: BLE001 — one source must never blind the rest
        log.debug("tool-surface: CLI source read failed", exc_info=True)

    try:
        from jarvis.marketplace.plugin_shared import get_active_plugin_registry

        plugin_registry = get_active_plugin_registry()
        if plugin_registry is not None:
            readable = True
            for tool in plugin_registry.active_tools():
                names.add(f"plugin:{getattr(tool, 'name', '')}")
    except Exception:  # noqa: BLE001
        log.debug("tool-surface: plugin source read failed", exc_info=True)

    try:
        from jarvis.core import runtime_refs

        mcp_registry = runtime_refs.get_mcp_registry()
        if mcp_registry is not None:
            readable = True
            for server_name, client in mcp_registry.active_clients().items():
                for tool_def in getattr(client, "_tools_cache", None) or []:
                    tool_name = (
                        tool_def.get("name") if isinstance(tool_def, dict) else None
                    )
                    if tool_name:
                        names.add(f"mcp:{server_name}:{tool_name}")
    except Exception:  # noqa: BLE001
        log.debug("tool-surface: MCP source read failed", exc_info=True)

    if not readable:
        return None
    return frozenset(names)


def stamp_tool_surface(manager) -> None:
    """Record the sources' fingerprint on the manager after a tool load."""
    try:
        manager._tool_surface_fp = live_tool_surface_fingerprint()
    except Exception:  # noqa: BLE001 — stamping must never break a load
        log.debug("tool-surface: stamp failed", exc_info=True)


def maybe_reconcile_tool_surface(manager) -> None:
    """Self-heal a stale tool snapshot before the turn touches tools.

    Never raises: any fault degrades to "no reconcile this turn" — the
    event-driven refresh path stays authoritative and untouched.
    """
    try:
        fingerprint = live_tool_surface_fingerprint()
        if fingerprint is None:
            return
        previous = getattr(manager, "_tool_surface_fp", None)
        if previous is None:
            # First observation: adopt without a refresh — at this point the
            # event-driven path has had no chance to be missed yet.
            manager._tool_surface_fp = fingerprint
            return
        if fingerprint == previous:
            return
        log.warning(
            "tool-surface drift: +%d/-%d source tools since the last load — "
            "refreshing the brain tool registry",
            len(fingerprint - previous),
            len(previous - fingerprint),
        )
        # Stamp BEFORE the refresh: refresh_tools() re-stamps on success, and
        # if it early-outs (no tier yet) this still prevents a warning storm —
        # the next genuine drift re-triggers.
        manager._tool_surface_fp = fingerprint
        manager.refresh_tools()
    except Exception:  # noqa: BLE001 — reconcile must never break a turn
        log.debug("tool-surface reconcile failed", exc_info=True)


__all__ = [
    "live_tool_surface_fingerprint",
    "maybe_reconcile_tool_surface",
    "stamp_tool_surface",
]
