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
import dataclasses
import logging
from collections.abc import Callable
from typing import Any

from jarvis.marketplace.catalog import PluginCatalog, PluginSpec
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.plugin_mcp import plugin_to_mcp_server_spec
from jarvis.marketplace.token_store import TokenStore
from jarvis.mcp.adapter import MCPToolAdapter

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level capability lifecycle helpers (called from connect/disconnect)
# ---------------------------------------------------------------------------


def _register_plugin_capability(cap_registry, plugin_id, skills) -> None:
    """Register the paired-skill capability for a freshly connected plugin.

    Finds the skill whose frontmatter plugin_id matches and registers its
    capability so resolve_intent reaches the connected plugin's tools."""
    from jarvis.skills.plugin_coupling import capability_from_skill

    for sk in skills:
        fm = getattr(sk, "frontmatter", None)
        if fm is not None and getattr(fm, "plugin_id", None) == plugin_id:
            cap = capability_from_skill(sk)
            if cap is not None:
                cap_registry.register(cap)
            return


def _deregister_plugin_capability(cap_registry, plugin_id) -> None:
    """Withdraw the paired capability when a plugin disconnects."""
    from jarvis.skills.plugin_coupling import PAIRED_CAP_PREFIX

    cap_registry.deregister(f"{PAIRED_CAP_PREFIX}{plugin_id}")


# Upper bound for one plugin's connect (transport handshake + list_tools).
# A wedged remote handshake must degrade to a per-plugin connect error, never
# stall the bootstrap loop: live 2026-07-13, mcp.linear.app answered a stale
# token with 401 and then never completed the stream — bootstrap hung there on
# every boot, so the completion BrainToolsChanged never fired and the github
# plugin's tools only reached the brain when their connect happened to beat
# the last unrelated tool refresh (the intermittent "tool not available" bug).
_CONNECT_TIMEOUT_S_DEFAULT = 15.0


def _connect_needs_reauth(message: str) -> bool:
    """True iff a connect failure indicates a dead credential, not a flaky host.

    Reuses the refresh scheduler's canonical OAuth marker list and adds the
    connect-time shapes (an HTTP 401/unauthorized on the MCP endpoint itself).
    Conservative on purpose: 'Connection closed', timeouts, and 5xx stay
    retryable so one flaky boot never flags a healthy plugin."""
    try:
        from jarvis.marketplace.refresh_scheduler import _refresh_needs_reauth

        if _refresh_needs_reauth(message):
            return True
    except Exception:  # noqa: BLE001 — classifier is best-effort
        log.debug("connect reauth classifier unavailable", exc_info=True)
    m = message.lower()
    return "401" in m or "unauthorized" in m or "invalid_token" in m


class PluginToolRegistry:
    def __init__(
        self,
        *,
        catalog: PluginCatalog | None = None,
        token_store: TokenStore | None = None,
        client_factory: Callable[..., Any] | None = None,
        bus: Any = None,
        default_risk_tier: str = "monitor",
        connect_timeout_s: float = _CONNECT_TIMEOUT_S_DEFAULT,
    ) -> None:
        self._catalog = catalog or load_catalog()
        self._store = token_store or TokenStore()
        self._client_factory = client_factory or _default_client_factory
        self._bus = bus
        self._risk_tier = default_risk_tier
        self._connect_timeout_s = float(connect_timeout_s)
        self._clients: dict[str, Any] = {}
        self._tools: dict[str, MCPToolAdapter] = {}
        # Last swallowed connect/list_tools error per plugin (honest liveness
        # badge — see live_tool_count/last_connect_error below). Cleared on a
        # subsequent successful connect.
        self._last_errors: dict[str, str] = {}
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

    def live_tool_count(self, plugin_id: str) -> int:
        """Number of live tool adapters currently registered for this plugin."""
        prefix = f"{plugin_id}/"
        return sum(1 for name in self._tools if name.startswith(prefix))

    def last_connect_error(self, plugin_id: str) -> str | None:
        """The swallowed connect/list_tools error of the last attempt, if any."""
        return self._last_errors.get(plugin_id)

    async def bootstrap(self) -> None:
        # Idempotent: a second bootstrap() would leak the first run's MCP
        # clients (their AsyncExitStacks) by overwriting self._clients. Callers
        # use refresh_plugin() for incremental updates after the first boot.
        # (_connect_plugin additionally skips already-connected ids, so a rare
        # concurrent second bootstrap degrades to no-ops, never double-registers.)
        async with self._lock:
            if self._bootstrapped:
                return
            plugins = list(self._catalog.plugins)
        # Per-plugin lock + per-plugin publish: an early plugin's tools reach
        # the live brain IMMEDIATELY, even while a later plugin is still
        # connecting or timing out. Holding one lock across the whole loop let
        # a single wedged handshake starve every other plugin AND the
        # completion event (live 2026-07-13, see _CONNECT_TIMEOUT_S_DEFAULT).
        for plugin in plugins:
            async with self._lock:
                await self._connect_plugin(plugin)
                connected = self.live_tool_count(plugin.id) > 0
            # Publish outside the lock — refresh_tools() (the subscriber) must
            # be free to call active_tools() without contending for this lock.
            if connected:
                await self._publish_brain_tools_changed(plugin.id, connected=True)
        async with self._lock:
            self._bootstrapped = True
            log.info("plugin-registry: %d plugin tools exposed", len(self._tools))

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
        client: Any | None = None
        try:
            client = self._client_factory(server_spec, env_overrides=env_overrides)
            # Bounded connect: a handshake that never completes (dead host,
            # 401-that-hangs) must not stall the caller — see
            # _CONNECT_TIMEOUT_S_DEFAULT for the live forensic.
            tool_defs = await asyncio.wait_for(
                self._start_and_list(client), timeout=self._connect_timeout_s
            )
        except Exception as exc:  # noqa: BLE001 — graceful per-plugin degrade
            if isinstance(exc, asyncio.TimeoutError):
                msg = (
                    f"connect timed out after {self._connect_timeout_s:.0f}s "
                    "(handshake never completed)"
                )
            else:
                msg = str(exc) or type(exc).__name__
            log.warning("plugin-registry: %s connect failed: %s", plugin.id, msg)
            self._last_errors[plugin.id] = msg
            self._maybe_mark_needs_reauth(plugin.id, tokens, msg)
            if client is not None:
                # Best-effort cleanup; a cancelled/hung transport may not
                # close cleanly, so the stop itself is bounded too.
                try:
                    await asyncio.wait_for(client.stop(), timeout=5.0)
                except Exception:  # noqa: BLE001 — cleanup must never raise
                    log.debug(
                        "plugin-registry: %s post-failure stop failed", plugin.id,
                        exc_info=True,
                    )
            return
        self._clients[plugin.id] = client
        self._last_errors.pop(plugin.id, None)
        for tool_def in tool_defs:
            adapter = MCPToolAdapter(client, tool_def, risk_tier=self._risk_tier)
            self._tools[adapter.name] = adapter
        try:
            from jarvis.core.capabilities import get_registry as _get_cap_registry
            from jarvis.skills.skill_context import try_get_skill_context

            _ctx = try_get_skill_context()
            if _ctx is not None:
                _register_plugin_capability(
                    _get_cap_registry(), plugin.id, _ctx.registry.list()
                )
        except Exception as exc:  # noqa: BLE001 — capability is best-effort
            log.debug("paired cap register failed for %s: %s", plugin.id, exc)

    @staticmethod
    async def _start_and_list(client: Any) -> list[dict[str, Any]]:
        """Handshake + tool listing as one awaitable so wait_for bounds both."""
        await client.start()
        return await client.list_tools()

    def _maybe_mark_needs_reauth(self, plugin_id: str, tokens: Any, message: str) -> None:
        """Flag the stored token after an auth-shaped connect failure.

        Mirrors the refresh scheduler's marking: later boots then skip the
        doomed connect (no repeated timeout cost) and the plugins view shows
        the Reconnect affordance instead of a silent per-boot failure."""
        try:
            if not _connect_needs_reauth(message):
                return
            self._store.save(plugin_id, dataclasses.replace(tokens, needs_reauth=True))
            log.warning(
                "plugin-registry: %s connect needs re-auth — marked: %s",
                plugin_id, message,
            )
        except Exception as exc:  # noqa: BLE001 — marking is best-effort
            log.debug(
                "plugin-registry: needs_reauth save failed for %s: %s",
                plugin_id, exc,
            )

    async def _disconnect_plugin(self, plugin_id: str) -> None:
        for name in [n for n in self._tools if n.startswith(f"{plugin_id}/")]:
            self._tools.pop(name, None)
        client = self._clients.pop(plugin_id, None)
        if client is not None:
            try:
                await client.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("plugin-registry: %s stop failed: %s", plugin_id, exc)
        try:
            from jarvis.core.capabilities import get_registry as _get_cap_registry

            _deregister_plugin_capability(_get_cap_registry(), plugin_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("paired cap deregister failed for %s: %s", plugin_id, exc)

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
