"""PluginToolRegistry — connected Marketplace plugins as live in-process tools.

Mirror of jarvis/clis/registry.py for MCP plugins. bootstrap() opens an
in-process MCPClient per connected plugin, wraps each server tool in an
MCPToolAdapter (which already runs the risk-tier flow + capability
registration), and publishes BrainToolsChanged so the live brain re-expands.
refresh_plugin() handles a single connect/disconnect without a restart.

Never raises out of bootstrap()/refresh_plugin(): one broken plugin degrades
to "no tools for that plugin" instead of blocking the whole brain (cloud-first
graceful-degradation doctrine).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from jarvis.marketplace.catalog import PluginCatalog, PluginSpec
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.plugin_mcp import plugin_to_mcp_server_spec
from jarvis.marketplace.token_store import TokenStore
from jarvis.mcp.adapter import MCPToolAdapter

log = logging.getLogger(__name__)


class PluginToolRegistry:
    def __init__(
        self,
        *,
        catalog: PluginCatalog | None = None,
        token_store: TokenStore | None = None,
        client_factory: Callable[..., Any] | None = None,
        bus: Any = None,
        default_risk_tier: str = "monitor",
    ) -> None:
        self._catalog = catalog or load_catalog()
        self._store = token_store or TokenStore()
        self._client_factory = client_factory or _default_client_factory
        self._bus = bus
        self._risk_tier = default_risk_tier
        self._clients: dict[str, Any] = {}
        self._tools: dict[str, MCPToolAdapter] = {}
        self._bootstrapped = False
        # Serialises bootstrap()/refresh_plugin()/stop(): all three mutate
        # self._clients/_tools across await points and are fired as independent
        # asyncio tasks (server start fires bootstrap; a REST connect/disconnect
        # fires refresh_plugin), so without this they interleave and can leak a
        # tokenless client or double-register a plugin's tools. asyncio.Lock is
        # not loop-bound at construction (py3.10+), so building it here is safe.
        self._lock = asyncio.Lock()

    def active_tools(self) -> list[MCPToolAdapter]:
        return list(self._tools.values())

    def is_bootstrapped(self) -> bool:
        return self._bootstrapped

    async def bootstrap(self) -> None:
        # Idempotent: a second bootstrap() would leak the first run's MCP
        # clients (their AsyncExitStacks) by overwriting self._clients. Callers
        # use refresh_plugin() for incremental updates after the first boot.
        async with self._lock:
            if self._bootstrapped:
                return
            for plugin in self._catalog.plugins:
                await self._connect_plugin(plugin)
            self._bootstrapped = True
            log.info("plugin-registry: %d plugin tools exposed", len(self._tools))
            tools_present = bool(self._tools)
        # Publish outside the lock — refresh_tools() (the subscriber) must be
        # free to call active_tools() without contending for this lock.
        if tools_present:
            await self._publish_brain_tools_changed("*", connected=True)

    async def refresh_plugin(self, plugin_id: str) -> None:
        """Re-evaluate a single plugin after connect/disconnect."""
        async with self._lock:
            plugin = self._catalog.by_id(plugin_id)
            had_tools = any(t.name.startswith(f"{plugin_id}/") for t in self._tools.values())
            await self._disconnect_plugin(plugin_id)
            if plugin is not None:
                await self._connect_plugin(plugin)
            now_has_tools = any(t.name.startswith(f"{plugin_id}/") for t in self._tools.values())
            changed = had_tools != now_has_tools
        if changed:
            await self._publish_brain_tools_changed(plugin_id, connected=now_has_tools)

    async def stop(self) -> None:
        async with self._lock:
            for pid in list(self._clients):
                await self._disconnect_plugin(pid)

    async def _connect_plugin(self, plugin: PluginSpec) -> None:
        if plugin.id in self._clients:
            # Already connected (e.g. a refresh_plugin ran before bootstrap
            # reached this plugin in its loop). Skip to avoid overwriting and
            # leaking the live client; disconnect-then-reconnect is the job of
            # refresh_plugin, which clears the entry first.
            return
        try:
            tokens = self._store.load(plugin.id)
        except Exception as exc:  # noqa: BLE001 — corrupt token must not nuke the rest
            log.warning("plugin-registry: token load failed for %s: %s", plugin.id, exc)
            return
        if tokens is None:
            return
        if tokens.needs_reauth:
            # The refresh scheduler marks a revoked/un-healable token this way
            # and keeps it for the "Reconnect" affordance. Don't waste a connect
            # attempt (and emit a misleading "connect failed") on every boot.
            log.debug("plugin-registry: %s skipped — needs re-auth", plugin.id)
            return
        resolved = plugin_to_mcp_server_spec(plugin, tokens)
        if resolved is None:
            return
        server_spec, env_overrides = resolved
        try:
            client = self._client_factory(server_spec, env_overrides=env_overrides)
            await client.start()
            tool_defs = await client.list_tools()
        except Exception as exc:  # noqa: BLE001 — graceful per-plugin degrade
            log.warning("plugin-registry: %s connect failed: %s", plugin.id, exc)
            return
        self._clients[plugin.id] = client
        for tool_def in tool_defs:
            adapter = MCPToolAdapter(client, tool_def, risk_tier=self._risk_tier)
            self._tools[adapter.name] = adapter

    async def _disconnect_plugin(self, plugin_id: str) -> None:
        for name in [n for n in self._tools if n.startswith(f"{plugin_id}/")]:
            self._tools.pop(name, None)
        client = self._clients.pop(plugin_id, None)
        if client is not None:
            try:
                await client.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("plugin-registry: %s stop failed: %s", plugin_id, exc)

    async def _publish_brain_tools_changed(self, plugin_id: str, connected: bool) -> None:
        if self._bus is None:
            return
        from jarvis.core.events import BrainToolsChanged

        verb = "plugin_connected" if connected else "plugin_disconnected"
        event = BrainToolsChanged(
            source_layer="marketplace.plugin_registry",
            reason=f"{verb}:{plugin_id}",
        )
        try:
            if asyncio.iscoroutinefunction(self._bus.publish):
                await self._bus.publish(event)
            else:
                self._bus.publish(event)
        except Exception as exc:  # noqa: BLE001
            log.debug("BrainToolsChanged publish failed: %s", exc)


def _default_client_factory(spec: Any, *, env_overrides: dict[str, str] | None = None) -> Any:
    from jarvis.mcp.client import MCPClient

    return MCPClient(spec, env_overrides=env_overrides)


__all__ = ["PluginToolRegistry"]
