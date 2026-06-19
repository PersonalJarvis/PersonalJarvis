# Live Plugin Hands — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a connected Marketplace plugin (GitHub, Notion, Google Calendar…) a first-class, callable tool of the *live* router-brain — on voice and chat — by mirroring the proven `cli-tools` virtual-loader + `BrainToolsChanged` live-reload pattern, plus per-plugin usage cards and a relevance gate.

**Architecture:** A new `plugin-tools` virtual-loader expands every *connected* plugin into in-process `MCPToolAdapter` tools (real `MCPClient` connections). It rides the exact same shared-registry + `BrainToolsChanged` machinery `cli-tools` already uses, so connecting a plugin re-expands the live brain with no restart. Usage cards (markdown + frontmatter) supply per-plugin guidance and relevance keywords. Reads run inline in the turn; heavy jobs stay on `spawn-worker`.

**Tech Stack:** Python 3.11, asyncio, the in-repo `jarvis/mcp/` MCP client, `jarvis/marketplace/` catalog+keyring, pytest (asyncio_mode=auto), `tests/fakes/`.

**Spec:** `docs/superpowers/specs/2026-06-01-live-plugin-tools-design.md`

---

## Conventions (read once)

- **All artifacts English** (code, comments, tests, commit messages) — CLAUDE.md Output-Language policy.
- Run tests with the **Jarvis Python**, not a venv: `& "C:\Program Files\Python311\python.exe" -m pytest …` (memory: pytest-venv ≠ Jarvis-Python). If a plain `pytest` works in your shell, fine — just confirm the same interpreter that imports `jarvis`.
- After editing `pyproject.toml` entry-points: `pip install -e . --no-deps` (BUG-006/014).
- Subprocess hygiene is handled inside `MCPClient` already; do not add new `subprocess` calls.
- Commit after every green step. Never `git add -A` — add the exact files (memory: save-to-github).

## File Structure

**Create:**
- `jarvis/marketplace/plugin_mcp.py` — pure: map a connected `PluginSpec` + `Tokens` → `(MCPServerSpec, env_overrides)`.
- `jarvis/marketplace/plugin_registry.py` — `PluginToolRegistry`: bootstrap connected plugins into live `MCPClient`s + `MCPToolAdapter`s; `active_tools()`; `refresh_plugin()`; publishes `BrainToolsChanged`. Mirror of `jarvis/clis/registry.py`.
- `jarvis/marketplace/plugin_shared.py` — module-global `get/set_active_plugin_registry`. Mirror of `jarvis/clis/shared.py`.
- `jarvis/marketplace/plugin_loader.py` — `PluginToolLoader` virtual loader. Mirror of `jarvis/clis/loader.py`.
- `jarvis/marketplace/usage_cards/__init__.py` + `jarvis/marketplace/usage_cards/loader.py` — usage-card load + parse (frontmatter).
- `jarvis/marketplace/usage_cards/google-calendar.md` (+ later: github.md, notion.md, …).
- Tests under `tests/unit/marketplace/` and `tests/integration/marketplace/`.

**Modify:**
- `pyproject.toml` — add entry-point `plugin-tools` (near `cli-tools`, line ~232).
- `jarvis/brain/factory.py` — add `"plugin-tools"` to `ROUTER_TOOLS` (the frozenset at line 40).
- `jarvis/ui/web/server.py` — construct + background-bootstrap `PluginToolRegistry`, publish via `set_active_plugin_registry` (mirror the CLI registry bootstrap).
- `jarvis/ui/web/marketplace_routes.py` — call `refresh_plugin()` after a successful connect (pat + oauth) and after disconnect (DELETE).
- `data/plugin_catalog.json` (or `jarvis/marketplace/seed_catalog.json`) — add the Google Calendar entry with its `mcp_server` spec (Wave 1) and update existing plugins' `mcp_server` if needed (Wave 3).
- `jarvis/brain/manager.py` — Wave 2: relevance filter in the per-turn tool assembly; Wave 3: usage-card prompt injection.
- `jarvis/brain/router.py` — Wave 4: system-prompt framing for inline-read-vs-delegate.
- `jarvis/ui/web/frontend/src/views/PluginsView.tsx` — Wave 4: "live-callable" badge.

---

# WAVE 1 — Live bridge (the unlock)

After Wave 1, a connected plugin's tools appear in the live router surface with no restart, and a manual call returns real data. This is the make-or-break wave.

### Task 1.1: Plugin → MCPServerSpec resolver (pure)

**Files:**
- Create: `jarvis/marketplace/plugin_mcp.py`
- Test: `tests/unit/marketplace/test_plugin_mcp.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_plugin_mcp.py
"""plugin_to_mcp_server_spec maps a connected plugin's mcp_server dict + token
into the in-process MCPServerSpec the MCPClient consumes."""
from jarvis.marketplace.plugin_mcp import plugin_to_mcp_server_spec
from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.token_store import Tokens


def _spec(mcp_server) -> PluginSpec:
    return PluginSpec(
        id="google-calendar",
        display_name="Google Calendar",
        description="Calendar",
        category="Productivity",
        logo_slug="googlecalendar",
        auth={"mode": "pat_paste", "token_creation_url": "x", "token_prefix": "ya29",
              "validation_endpoint": "x", "instruction_md": "x"},
        mcp_server=mcp_server,
    )


def test_http_transport_resolves_bearer_header():
    plugin = _spec({
        "transport": "http",
        "url": "https://cal.example/mcp",
        "auth_header_template": "Authorization: Bearer $plugin_google-calendar_access_token",
    })
    result = plugin_to_mcp_server_spec(plugin, Tokens(access="TOK123"))
    assert result is not None
    server_spec, env_overrides = result
    assert server_spec.transport == "http"
    assert server_spec.url == "https://cal.example/mcp"
    assert server_spec.headers == {"Authorization": "Bearer TOK123"}
    assert env_overrides == {}


def test_stdio_transport_resolves_install_and_env():
    plugin = _spec({
        "transport": "stdio",
        "install": ["npx", "-y", "@calendar/mcp", "--token", "$plugin_google-calendar_access_token"],
        "env_template": {"CAL_TOKEN": "${plugin_google-calendar_access_token}"},
    })
    result = plugin_to_mcp_server_spec(plugin, Tokens(access="TOK123"))
    assert result is not None
    server_spec, env_overrides = result
    assert server_spec.transport == "stdio"
    assert server_spec.install_command == ["npx", "-y", "@calendar/mcp", "--token", "TOK123"]
    assert env_overrides == {"CAL_TOKEN": "TOK123"}


def test_unsupported_transport_returns_none():
    plugin = _spec({"transport": "rest_wrapper", "url": "x"})
    assert plugin_to_mcp_server_spec(plugin, Tokens(access="TOK123")) is None


def test_no_mcp_server_returns_none():
    plugin = _spec(None)
    assert plugin_to_mcp_server_spec(plugin, Tokens(access="TOK123")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/marketplace/test_plugin_mcp.py -v`
Expected: FAIL with `ModuleNotFoundError: jarvis.marketplace.plugin_mcp`

- [ ] **Step 3: Write the implementation**

```python
# jarvis/marketplace/plugin_mcp.py
"""Map a connected Marketplace plugin into the in-process MCPServerSpec.

This is the live-brain analogue of marketplace.mcp_bridge (which targets the
claude-cli worker). Here we build a jarvis.mcp.MCPServerSpec + env_overrides
so an in-process MCPClient can connect and expose the plugin's tools to the
router-brain directly. Token placeholders reuse mcp_bridge's resolver so the
two paths stay byte-identical on placeholder semantics.
"""
from __future__ import annotations

from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.mcp_bridge import _resolve_placeholders, _token_replacements
from jarvis.marketplace.token_store import Tokens
from jarvis.mcp.registry import MCPServerSpec


def plugin_to_mcp_server_spec(
    plugin: PluginSpec, tokens: Tokens
) -> tuple[MCPServerSpec, dict[str, str]] | None:
    """Return (MCPServerSpec, env_overrides) for a connected plugin, or None.

    None when the plugin has no mcp_server block or an MCP-incompatible
    transport (rest_wrapper / unknown). stdio + http are supported — the same
    two transports the worker bridge speaks.
    """
    spec = plugin.mcp_server
    if not spec:
        return None
    repl = _token_replacements(plugin.id, tokens.access)
    transport = str(spec.get("transport") or "").lower()

    if transport == "http":
        url = spec.get("url")
        if not url:
            return None
        headers: dict[str, str] = {}
        header_template = spec.get("auth_header_template")
        if header_template:
            resolved = _resolve_placeholders(str(header_template), repl)
            key, sep, val = resolved.partition(":")
            if sep:
                headers[key.strip()] = val.strip()
        server_spec = MCPServerSpec(
            name=plugin.id,
            display=plugin.display_name,
            description=plugin.description,
            install_command=[],
            transport="http",
            url=str(url),
            headers=headers,
        )
        return server_spec, {}

    if transport == "stdio":
        install = spec.get("install") or []
        if not install:
            return None
        resolved_install = [_resolve_placeholders(str(a), repl) for a in install]
        env_template = spec.get("env_template") or {}
        env_overrides = {
            str(k): _resolve_placeholders(str(v), repl) for k, v in env_template.items()
        }
        server_spec = MCPServerSpec(
            name=plugin.id,
            display=plugin.display_name,
            description=plugin.description,
            install_command=resolved_install,
            transport="stdio",
        )
        return server_spec, env_overrides

    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/marketplace/test_plugin_mcp.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/plugin_mcp.py tests/unit/marketplace/test_plugin_mcp.py
git commit -m "feat(marketplace): plugin->MCPServerSpec resolver for in-process bridge"
```

---

### Task 1.2: `PluginToolRegistry` — bootstrap connected plugins into live tools

**Files:**
- Create: `jarvis/marketplace/plugin_registry.py`
- Test: `tests/unit/marketplace/test_plugin_registry.py`

This mirrors `jarvis/clis/registry.py`: a `bootstrap()` that builds tool instances for connected plugins and publishes `BrainToolsChanged`, an `active_tools()`, and a `refresh_plugin()` for connect/disconnect. `MCPClient.start()` is injected via a factory so tests use a fake (no network).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_plugin_registry.py
import pytest

from jarvis.marketplace.catalog import PluginCatalog, PluginSpec
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


def _calendar_plugin() -> PluginSpec:
    return PluginSpec(
        id="google-calendar", display_name="Google Calendar", description="Calendar",
        category="Productivity", logo_slug="googlecalendar",
        auth={"mode": "pat_paste", "token_creation_url": "x", "token_prefix": "ya29",
              "validation_endpoint": "x", "instruction_md": "x"},
        mcp_server={"transport": "http", "url": "https://cal/mcp",
                    "auth_header_template": "Authorization: Bearer $plugin_google-calendar_access_token"},
    )


class _FakeClient:
    """Stands in for jarvis.mcp.MCPClient — no network."""
    def __init__(self, spec, env_overrides=None):
        self.spec = spec
        self._tools = [{"name": "list_events", "description": "List events", "inputSchema": {}}]
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def list_tools(self): return list(self._tools)


class _RecordingBus:
    def __init__(self): self.events = []
    async def publish(self, ev): self.events.append(ev)


def _store_with_calendar() -> TokenStore:
    store = TokenStore(InMemoryBackend())
    store.save("google-calendar", Tokens(access="TOK"))
    return store


@pytest.mark.asyncio
async def test_bootstrap_exposes_connected_plugin_tools():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    bus = _RecordingBus()
    reg = PluginToolRegistry(
        catalog=catalog, token_store=_store_with_calendar(),
        client_factory=_FakeClient, bus=bus,
    )
    await reg.bootstrap()
    names = [t.name for t in reg.active_tools()]
    assert "google-calendar/list_events" in names
    # Live-reload: a BrainToolsChanged is published so the live brain re-expands.
    assert any(type(e).__name__ == "BrainToolsChanged" for e in bus.events)


@pytest.mark.asyncio
async def test_no_token_means_no_tools():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    reg = PluginToolRegistry(
        catalog=catalog, token_store=TokenStore(InMemoryBackend()),
        client_factory=_FakeClient, bus=_RecordingBus(),
    )
    await reg.bootstrap()
    assert reg.active_tools() == []


@pytest.mark.asyncio
async def test_refresh_plugin_disconnect_removes_tools():
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_calendar_plugin()])
    store = _store_with_calendar()
    reg = PluginToolRegistry(catalog=catalog, token_store=store,
                             client_factory=_FakeClient, bus=_RecordingBus())
    await reg.bootstrap()
    assert reg.active_tools()
    store.delete("google-calendar")
    await reg.refresh_plugin("google-calendar")
    assert reg.active_tools() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/marketplace/test_plugin_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: jarvis.marketplace.plugin_registry`

- [ ] **Step 3: Write the implementation**

```python
# jarvis/marketplace/plugin_registry.py
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
        # client_factory(spec, env_overrides=...) -> MCPClient-like. Injected
        # so tests use a fake; production uses the real MCPClient.
        self._client_factory = client_factory or _default_client_factory
        self._bus = bus
        self._risk_tier = default_risk_tier
        self._clients: dict[str, Any] = {}            # plugin_id -> client
        self._tools: dict[str, MCPToolAdapter] = {}   # adapter.name -> adapter
        self._bootstrapped = False

    def active_tools(self) -> list[MCPToolAdapter]:
        return list(self._tools.values())

    def is_bootstrapped(self) -> bool:
        return self._bootstrapped

    async def bootstrap(self) -> None:
        for plugin in self._catalog.plugins:
            await self._connect_plugin(plugin)
        self._bootstrapped = True
        log.info("plugin-registry: %d plugin tools exposed", len(self._tools))
        if self._tools:
            await self._publish_brain_tools_changed("*", connected=True)

    async def refresh_plugin(self, plugin_id: str) -> None:
        """Re-evaluate a single plugin after connect/disconnect."""
        plugin = self._catalog.by_id(plugin_id)
        had_tools = any(t.name.startswith(f"{plugin_id}/") for t in self._tools.values())
        # Drop any existing client/tools for this plugin first.
        await self._disconnect_plugin(plugin_id)
        if plugin is not None:
            await self._connect_plugin(plugin)
        now_has_tools = any(t.name.startswith(f"{plugin_id}/") for t in self._tools.values())
        if had_tools != now_has_tools:
            await self._publish_brain_tools_changed(plugin_id, connected=now_has_tools)

    async def stop(self) -> None:
        for pid in list(self._clients):
            await self._disconnect_plugin(pid)

    # ---- internals ----------------------------------------------------

    async def _connect_plugin(self, plugin: PluginSpec) -> None:
        try:
            tokens = self._store.load(plugin.id)
        except Exception as exc:  # noqa: BLE001 — corrupt token must not nuke the rest
            log.warning("plugin-registry: token load failed for %s: %s", plugin.id, exc)
            return
        if tokens is None:
            return  # not connected
        resolved = plugin_to_mcp_server_spec(plugin, tokens)
        if resolved is None:
            return  # no mcp_server / unsupported transport (graceful)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/marketplace/test_plugin_registry.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/plugin_registry.py tests/unit/marketplace/test_plugin_registry.py
git commit -m "feat(marketplace): PluginToolRegistry bridges connected plugins into live tools"
```

---

### Task 1.3: Shared registry + `PluginToolLoader` virtual loader

**Files:**
- Create: `jarvis/marketplace/plugin_shared.py`
- Create: `jarvis/marketplace/plugin_loader.py`
- Test: `tests/unit/marketplace/test_plugin_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_plugin_loader.py
from jarvis.marketplace import plugin_shared
from jarvis.marketplace.plugin_loader import PluginToolLoader


class _FakeRegistry:
    def __init__(self, tools): self._tools = tools
    def active_tools(self): return list(self._tools)


def teardown_function():
    plugin_shared.set_active_plugin_registry(None)


def test_loader_is_virtual():
    loader = PluginToolLoader()
    assert loader.is_virtual_loader is True


def test_expand_returns_shared_registry_tools():
    plugin_shared.set_active_plugin_registry(_FakeRegistry(["t1", "t2"]))
    assert PluginToolLoader().expand() == ["t1", "t2"]


def test_expand_empty_when_no_shared_registry():
    plugin_shared.set_active_plugin_registry(None)
    assert PluginToolLoader().expand() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/marketplace/test_plugin_loader.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write both modules**

```python
# jarvis/marketplace/plugin_shared.py
"""Shared handle to the active PluginToolRegistry (mirror of jarvis.clis.shared).

The UI server constructs the registry, bootstraps it, and publishes it here so
the brain loader and the marketplace routes see the SAME instance on the SAME
bus — no split-brain registry.
"""
from __future__ import annotations

from typing import Any

_active_registry: Any = None


def set_active_plugin_registry(registry: Any) -> None:
    global _active_registry
    _active_registry = registry


def get_active_plugin_registry() -> Any:
    return _active_registry
```

```python
# jarvis/marketplace/plugin_loader.py
"""PluginToolLoader — the single static entry point for the plugin tool slot.

Registered once in pyproject.toml: plugin-tools = jarvis.marketplace.plugin_loader:PluginToolLoader.
The brain factory recognises is_virtual_loader=True and calls expand() -> list[Tool].
Mirror of jarvis/clis/loader.py: it returns the active tools of the shared
PluginToolRegistry, or [] when none is published yet (the BrainToolsChanged
live-reload re-runs expand() once bootstrap finishes / a plugin connects).
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)


class PluginToolLoader:
    is_virtual_loader: bool = True

    name: str = "plugin_tools_loader"
    description: str = (
        "Virtual plugin-tool loader. Never called by the brain directly — "
        "the factory expands it into N MCPToolAdapter instances."
    )
    risk_tier: str = "block"
    schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def expand(self) -> list[Any]:
        try:
            from jarvis.marketplace.plugin_shared import get_active_plugin_registry

            registry = get_active_plugin_registry()
        except Exception as exc:  # noqa: BLE001
            log.debug("plugin-loader: shared registry lookup failed: %s", exc)
            return []
        if registry is None:
            return []
        try:
            return list(registry.active_tools())
        except Exception as exc:  # noqa: BLE001
            log.debug("plugin-loader: active_tools() failed: %s", exc)
            return []

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return ToolResult(
            success=False,
            output=None,
            error="PluginToolLoader is a virtual loader; expand it, don't execute it.",
        )


__all__ = ["PluginToolLoader"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/marketplace/test_plugin_loader.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/plugin_shared.py jarvis/marketplace/plugin_loader.py tests/unit/marketplace/test_plugin_loader.py
git commit -m "feat(marketplace): PluginToolLoader virtual loader + shared registry handle"
```

---

### Task 1.4: Register the entry-point + add to ROUTER_TOOLS

**Files:**
- Modify: `pyproject.toml` (entry-points, line ~232)
- Modify: `jarvis/brain/factory.py` (ROUTER_TOOLS frozenset, line 40)
- Test: `tests/unit/brain/test_routing.py` (extend the existing ROUTER_TOOLS test)

- [ ] **Step 1: Write the failing test** (append to the routing test file)

```python
# tests/unit/brain/test_routing.py  (add this test)
def test_plugin_tools_in_router_set():
    from jarvis.brain.factory import ROUTER_TOOLS
    assert "plugin-tools" in ROUTER_TOOLS
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/brain/test_routing.py::test_plugin_tools_in_router_set -v`
Expected: FAIL (`plugin-tools` not in set)

- [ ] **Step 3: Edit the two files**

In `jarvis/brain/factory.py`, inside the `ROUTER_TOOLS = frozenset({ … })` block, add after `"cli-tools",`:

```python
    # Marketplace plugins as live brain tools (2026-06-01). Virtual loader,
    # mirror of cli-tools: expands to one MCPToolAdapter per connected plugin
    # tool. Direct safe/risk-gated action, NEVER a spawn — must not enter any
    # worker tool-set (AP-5/AP-14). See docs/.../2026-06-01-live-plugin-tools.md.
    "plugin-tools",
```

In `pyproject.toml`, under `[project.entry-points."jarvis.tool"]`, after the `cli-tools = …` line (~232):

```toml
plugin-tools = "jarvis.marketplace.plugin_loader:PluginToolLoader"
```

- [ ] **Step 4: Re-install editable + run test**

Run:
```bash
pip install -e . --no-deps
pytest tests/unit/brain/test_routing.py::test_plugin_tools_in_router_set -v
```
Expected: PASS

- [ ] **Step 5: Verify the factory expands the loader (integration)**

Create `tests/integration/marketplace/test_plugin_loader_wired.py`:

```python
from importlib.metadata import entry_points


def test_plugin_tools_entry_point_registered():
    eps = {ep.name: ep for ep in entry_points(group="jarvis.tool")}
    assert "plugin-tools" in eps
    cls = eps["plugin-tools"].load()
    assert getattr(cls(), "is_virtual_loader", False) is True
```

Run: `pytest tests/integration/marketplace/test_plugin_loader_wired.py -v`
Expected: PASS (proves `pip install -e .` activated the entry point)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml jarvis/brain/factory.py tests/unit/brain/test_routing.py tests/integration/marketplace/test_plugin_loader_wired.py
git commit -m "feat(brain): register plugin-tools loader in ROUTER_TOOLS + entry-points"
```

---

### Task 1.5: Server bootstrap + connect/disconnect live-reload

**Files:**
- Modify: `jarvis/ui/web/server.py` (next to the existing CLI registry bootstrap)
- Modify: `jarvis/ui/web/marketplace_routes.py` (connect pat/oauth success + DELETE)

> **Orientation step (not code):** `grep -n "set_active_registry\|CliToolRegistry\|bootstrap" jarvis/ui/web/server.py` to find where the CLI registry is constructed, bus-attached, bootstrapped as a background task, and published. Place the plugin equivalent right beside it so both share the bus and lifecycle.

- [ ] **Step 1: Add the plugin-registry bootstrap to `server.py`**

Beside the CLI-registry bootstrap, add (adapt the variable name for the app's bus + the background-task scheduler the file already uses):

```python
# Live plugin tools: construct + publish the PluginToolRegistry so the
# plugin-tools loader (and the marketplace routes) see the same instance on
# the same bus. Bootstrap runs in the background; BrainToolsChanged re-expands
# the live brain once connected plugins finish probing.
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.plugin_shared import set_active_plugin_registry

plugin_registry = PluginToolRegistry(bus=bus)
app.state.plugin_registry = plugin_registry
set_active_plugin_registry(plugin_registry)

async def _bootstrap_plugin_registry() -> None:
    try:
        await plugin_registry.bootstrap()
    except Exception:  # noqa: BLE001
        log.warning("plugin-registry bootstrap failed", exc_info=True)

# schedule with the SAME mechanism the file uses for _start_enabled_mcps /
# the CLI bootstrap (e.g. asyncio.create_task(...) inside the startup hook).
asyncio.create_task(_bootstrap_plugin_registry())
```

- [ ] **Step 2: Trigger re-expand on connect/disconnect in `marketplace_routes.py`**

Add a small helper at module level:

```python
def _refresh_plugin_in_live_registry(plugin_id: str) -> None:
    """Best-effort: re-expand the live brain after a connect/disconnect.

    No-op when no shared registry is published (headless without web boot).
    """
    try:
        from jarvis.marketplace.plugin_shared import get_active_plugin_registry

        reg = get_active_plugin_registry()
        if reg is not None:
            import asyncio

            asyncio.create_task(reg.refresh_plugin(plugin_id))
    except Exception:  # noqa: BLE001
        log.debug("live plugin refresh failed for %s", plugin_id, exc_info=True)
```

Call `_refresh_plugin_in_live_registry(plugin_id)` at the end of every successful connect path (the `connect/pat` handler after the token is saved, and the OAuth completion where the token is persisted) and in the `DELETE` handler after `token_store.delete(...)`.

- [ ] **Step 3: Integration test — connect makes the tool live**

Create `tests/integration/marketplace/test_plugin_live_reload.py`:

```python
import pytest

from jarvis.marketplace import plugin_shared
from jarvis.marketplace.catalog import PluginCatalog, PluginSpec
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


class _FakeClient:
    def __init__(self, spec, env_overrides=None): self.spec = spec
    async def start(self): ...
    async def stop(self): ...
    async def list_tools(self):
        return [{"name": "list_events", "description": "List", "inputSchema": {}}]


def _plugin():
    return PluginSpec(
        id="google-calendar", display_name="Google Calendar", description="Cal",
        category="Productivity", logo_slug="googlecalendar",
        auth={"mode": "pat_paste", "token_creation_url": "x", "token_prefix": "ya29",
              "validation_endpoint": "x", "instruction_md": "x"},
        mcp_server={"transport": "http", "url": "https://cal/mcp",
                    "auth_header_template": "Authorization: Bearer $plugin_google-calendar_access_token"})


@pytest.mark.asyncio
async def test_connect_then_refresh_exposes_tool_via_loader():
    from jarvis.marketplace.plugin_loader import PluginToolLoader

    store = TokenStore(InMemoryBackend())
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[_plugin()])

    class _Bus:
        async def publish(self, ev): ...

    reg = PluginToolRegistry(catalog=catalog, token_store=store,
                             client_factory=_FakeClient, bus=_Bus())
    plugin_shared.set_active_plugin_registry(reg)
    try:
        await reg.bootstrap()
        assert PluginToolLoader().expand() == []          # nothing connected yet
        store.save("google-calendar", Tokens(access="TOK"))
        await reg.refresh_plugin("google-calendar")
        names = [t.name for t in PluginToolLoader().expand()]
        assert "google-calendar/list_events" in names     # now live, no restart
    finally:
        plugin_shared.set_active_plugin_registry(None)
```

Run: `pytest tests/integration/marketplace/test_plugin_live_reload.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add jarvis/ui/web/server.py jarvis/ui/web/marketplace_routes.py tests/integration/marketplace/test_plugin_live_reload.py
git commit -m "feat(marketplace): bootstrap PluginToolRegistry + live-reload on connect/disconnect"
```

---

### Task 1.6: Google Calendar catalog entry (the real pilot)

**Files:**
- Modify: the live catalog file. Determine which: `grep -rl '"plugins"' data/plugin_catalog.json jarvis/marketplace/seed_catalog.json` — `data/plugin_catalog.json` overrides the seed; if it exists, edit it, else edit `seed_catalog.json`.

- [ ] **Step 1: Add the Calendar plugin entry**

Add to the `plugins` array (use the team's confirmed remote Google Calendar MCP endpoint + auth mode; `oauth_pkce_loopback` is the natural fit for Google. The `mcp_server.url`/`auth_header_template` below assume a remote HTTP MCP — swap to a `stdio` `install` block if the chosen server is a local npx/uvx package):

```json
{
  "id": "google-calendar",
  "display_name": "Google Calendar",
  "description": "See and manage your calendar events.",
  "category": "Productivity",
  "logo_slug": "googlecalendar",
  "featured": true,
  "auth": {
    "mode": "oauth_pkce_loopback",
    "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "client_id": "<REPLACE_WITH_GOOGLE_OAUTH_CLIENT_ID>",
    "callback_port": 0,
    "scopes": ["https://www.googleapis.com/auth/calendar"],
    "refresh_supported": true
  },
  "mcp_server": {
    "transport": "http",
    "url": "<REPLACE_WITH_REMOTE_CALENDAR_MCP_URL>",
    "auth_header_template": "Authorization: Bearer $plugin_google-calendar_access_token"
  }
}
```

> The two `<REPLACE…>` values are the only real-world unknowns (the open question from the spec §11). Resolve them with the maintainer before this task — do NOT invent a URL.

- [ ] **Step 2: Validate the catalog still loads**

Run:
```bash
python -c "from jarvis.marketplace.catalog_data import load_catalog; c=load_catalog(); print(c.by_id('google-calendar').display_name)"
```
Expected: `Google Calendar` (PluginSpec validation passed — `extra='forbid'`, so a typo fails loudly).

- [ ] **Step 3: Commit**

```bash
git add data/plugin_catalog.json   # or seed_catalog.json
git commit -m "feat(marketplace): add Google Calendar plugin (live-bridge pilot)"
```

**Wave 1 acceptance:** with Calendar connected, `PluginToolLoader().expand()` lists `google-calendar/*` tools; the live brain re-expands on connect with no restart; a real `list_events` call returns data through the `MCPToolAdapter` → `ToolExecutor` path.

---

# WAVE 2 — Relevance gate (keep the surface small)

Per-turn, inject only the plugins plausibly relevant to the utterance, so 6 plugins × 8 tools don't drown the router. Keyword match only — **no LLM, no IO** (AP-9).

### Task 2.1: Usage-card loader (frontmatter = relevance keywords + body = guidance)

**Files:**
- Create: `jarvis/marketplace/usage_cards/__init__.py` (empty), `jarvis/marketplace/usage_cards/loader.py`
- Create: `jarvis/marketplace/usage_cards/google-calendar.md`
- Test: `tests/unit/marketplace/test_usage_cards.py`

Card format (markdown with a tiny YAML-ish frontmatter we parse without a YAML dep):

```markdown
---
plugin_id: google-calendar
keywords: termin, termine, kalender, calendar, meeting, appointment, schedule, heute, morgen
---
Use the google-calendar/* tools to read and manage the user's calendar.
- For "today"/"heute": call list_events with timeMin/timeMax set to the user's
  local day boundaries (their timezone), not UTC midnight.
- Summarize: time + title only, chronological, no IDs.
- Create/delete events directly (full autonomy); state what you did afterwards.
```

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_usage_cards.py
from jarvis.marketplace.usage_cards.loader import UsageCard, load_usage_card


def test_load_calendar_card_parses_frontmatter_and_body():
    card = load_usage_card("google-calendar")
    assert card is not None
    assert card.plugin_id == "google-calendar"
    assert "kalender" in card.keywords
    assert "list_events" in card.body


def test_unknown_plugin_returns_none():
    assert load_usage_card("does-not-exist") is None


def test_keyword_match_is_case_insensitive_substring():
    card = UsageCard(plugin_id="x", keywords=["kalender", "termine"], body="...")
    assert card.matches("Was habe ich heute für TERMINE?") is True
    assert card.matches("erzähl mir einen witz") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/marketplace/test_usage_cards.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write the loader + the Calendar card**

```python
# jarvis/marketplace/usage_cards/loader.py
"""Load per-plugin usage cards: frontmatter (keywords) + markdown body.

A card co-locates two things: the relevance keywords (for the per-turn gate)
and the guidance prose (injected into the system prompt only when the plugin
is active this turn). No YAML dependency — we parse the tiny frontmatter by
hand. Missing card = None (a plugin without a card still works, just without
guidance/keyword gating; the relevance gate then falls back to always-include).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_CARDS_DIR = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class UsageCard:
    plugin_id: str
    keywords: list[str] = field(default_factory=list)
    body: str = ""

    def matches(self, text: str) -> bool:
        low = text.lower()
        return any(kw and kw.lower() in low for kw in self.keywords)


def load_usage_card(plugin_id: str) -> UsageCard | None:
    # plugin_id is a catalog id (validated elsewhere); guard path traversal.
    if not plugin_id or "/" in plugin_id or "\\" in plugin_id or ".." in plugin_id:
        return None
    path = _CARDS_DIR / f"{plugin_id}.md"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    keywords: list[str] = []
    body = raw
    if raw.startswith("---"):
        _, _, rest = raw.partition("---")
        front, sep, body = rest.partition("---")
        if not sep:
            front, body = "", raw
        for line in front.splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "keywords":
                keywords = [k.strip() for k in value.split(",") if k.strip()]
    return UsageCard(plugin_id=plugin_id, keywords=keywords, body=body.strip())
```

Create `jarvis/marketplace/usage_cards/__init__.py` (empty) and `jarvis/marketplace/usage_cards/google-calendar.md` with the card content shown above this task.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/marketplace/test_usage_cards.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/usage_cards/ tests/unit/marketplace/test_usage_cards.py
git commit -m "feat(marketplace): usage-card loader + Google Calendar card"
```

---

### Task 2.2: Relevance filter (pure function)

**Files:**
- Create: `jarvis/marketplace/plugin_relevance.py`
- Test: `tests/unit/marketplace/test_plugin_relevance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_plugin_relevance.py
from jarvis.marketplace.plugin_relevance import filter_plugin_tools


class _Tool:
    def __init__(self, name): self.name = name


def test_keeps_only_relevant_plugin_namespace():
    tools = [_Tool("google-calendar/list_events"), _Tool("github/create_issue")]
    kept = filter_plugin_tools("was habe ich heute für termine", tools)
    assert [t.name for t in kept] == ["google-calendar/list_events"]


def test_non_plugin_tools_always_kept():
    tools = [_Tool("run-shell"), _Tool("github/create_issue")]
    kept = filter_plugin_tools("erzähl einen witz", tools)
    assert any(t.name == "run-shell" for t in kept)        # non-namespaced survive
    assert all("github/" not in t.name for t in kept)      # irrelevant plugin dropped


def test_fallback_includes_all_when_nothing_matches_but_few_plugins():
    # With a single connected plugin and an ambiguous utterance, prefer
    # over-offering to missing — include it.
    tools = [_Tool("google-calendar/list_events")]
    kept = filter_plugin_tools("mach mal", tools, max_unfiltered=3)
    assert [t.name for t in kept] == ["google-calendar/list_events"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/marketplace/test_plugin_relevance.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write the implementation**

```python
# jarvis/marketplace/plugin_relevance.py
"""Per-turn relevance gate for plugin tools. Keyword-only, no LLM, no IO (AP-9).

A plugin tool is namespaced "<plugin_id>/<tool>". We keep a plugin's tools when
its usage-card keywords match the utterance. Non-namespaced tools (the native
router tools) are never touched. Fallback: when the number of distinct connected
plugins is <= max_unfiltered, include them all (over-offer beats missing).
"""
from __future__ import annotations

from typing import Any

from jarvis.marketplace.usage_cards.loader import load_usage_card


def _plugin_id_of(tool_name: str) -> str | None:
    pid, sep, _ = tool_name.partition("/")
    return pid if sep else None


def filter_plugin_tools(
    user_text: str, tools: list[Any], *, max_unfiltered: int = 2
) -> list[Any]:
    plugin_ids = {pid for t in tools if (pid := _plugin_id_of(t.name))}
    if not plugin_ids:
        return list(tools)
    if len(plugin_ids) <= max_unfiltered:
        return list(tools)  # few plugins — keep all, no risk of surface bloat

    relevant: set[str] = set()
    for pid in plugin_ids:
        card = load_usage_card(pid)
        # No card => can't gate it => keep it (conservative).
        if card is None or card.matches(user_text):
            relevant.add(pid)

    kept: list[Any] = []
    for t in tools:
        pid = _plugin_id_of(t.name)
        if pid is None or pid in relevant:
            kept.append(t)
    return kept
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/marketplace/test_plugin_relevance.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/plugin_relevance.py tests/unit/marketplace/test_plugin_relevance.py
git commit -m "feat(marketplace): keyword-only per-turn plugin relevance gate"
```

---

### Task 2.3: Wire the relevance filter into the brain turn

**Files:**
- Modify: `jarvis/brain/manager.py` (the per-turn tool assembly inside `generate()`)
- Test: `tests/unit/brain/test_plugin_relevance_wiring.py`

> **Orientation step:** in `manager.py::generate()` find where the per-turn tool dict is finalised before being handed to the provider (near the `_smalltalk_tool_override` application, ~line 2300). The filter is applied to that final dict's values.

- [ ] **Step 1: Add a small method on BrainManager**

```python
# in jarvis/brain/manager.py, BrainManager
def _apply_plugin_relevance(self, user_text: str, tools: dict[str, "Tool"]) -> dict[str, "Tool"]:
    """Drop plugin tools (namespaced '<id>/<tool>') irrelevant to this turn.

    Keyword-only (AP-9). Native tools are untouched. Defensive: any failure
    returns the unfiltered dict so a gate bug never blinds the brain.
    """
    try:
        from jarvis.marketplace.plugin_relevance import filter_plugin_tools

        kept = filter_plugin_tools(user_text, list(tools.values()))
        kept_names = {t.name for t in kept}
        return {name: t for name, t in tools.items() if t.name in kept_names}
    except Exception:  # noqa: BLE001
        log.debug("plugin relevance gate failed; using full tool set", exc_info=True)
        return tools
```

Call it where the final per-turn tool dict is assembled (right before it is passed to the provider), e.g.:

```python
turn_tools = self._apply_plugin_relevance(user_text, turn_tools)
```

- [ ] **Step 2: Test it (unit, against the method)**

```python
# tests/unit/brain/test_plugin_relevance_wiring.py
from jarvis.brain.manager import BrainManager


class _T:
    def __init__(self, name): self.name = name


def test_apply_plugin_relevance_drops_irrelevant(monkeypatch):
    mgr = BrainManager.__new__(BrainManager)  # no full init needed for this pure method
    tools = {
        "run-shell": _T("run-shell"),
        "google-calendar/list_events": _T("google-calendar/list_events"),
        "github/create_issue": _T("github/create_issue"),
        "notion/search": _T("notion/search"),
    }
    out = mgr._apply_plugin_relevance("was habe ich heute für termine", tools)
    assert "run-shell" in out
    assert "google-calendar/list_events" in out
    assert "github/create_issue" not in out
```

Run: `pytest tests/unit/brain/test_plugin_relevance_wiring.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add jarvis/brain/manager.py tests/unit/brain/test_plugin_relevance_wiring.py
git commit -m "feat(brain): apply per-turn plugin relevance gate in generate()"
```

**Wave 2 acceptance:** with 3+ plugins connected, an unrelated utterance injects 0 plugin tools; a calendar utterance injects only `google-calendar/*`.

---

# WAVE 3 — Usage cards into the prompt

### Task 3.1: Inject active plugins' usage cards into the system prompt

**Files:**
- Modify: `jarvis/brain/manager.py` (system-prompt assembly for the turn)
- Test: `tests/unit/brain/test_usage_card_injection.py`

- [ ] **Step 1: Add a helper that builds the card block for the turn's tools**

```python
# in jarvis/brain/manager.py
def _plugin_usage_cards_block(self, tools: dict[str, "Tool"]) -> str:
    """Markdown block of usage cards for the plugins present in this turn.

    Only the plugins that survived the relevance gate contribute, so the prompt
    stays small. Returns '' when no plugin tools are active.
    """
    from jarvis.marketplace.usage_cards.loader import load_usage_card

    plugin_ids: list[str] = []
    for name in tools:
        pid, sep, _ = name.partition("/")
        if sep and pid not in plugin_ids:
            plugin_ids.append(pid)
    blocks: list[str] = []
    for pid in plugin_ids:
        card = load_usage_card(pid)
        if card and card.body:
            blocks.append(f"### Plugin: {pid}\n{card.body}")
    if not blocks:
        return ""
    return "## Connected plugins — how to use them\n\n" + "\n\n".join(blocks)
```

Append the returned block to the per-turn system prompt where `self._system_prompt_extra` (the router prompt) is combined with the rest (find it in `generate()`; the block goes after the relevance gate so the same surviving plugins are described).

- [ ] **Step 2: Test it**

```python
# tests/unit/brain/test_usage_card_injection.py
from jarvis.brain.manager import BrainManager


class _T:
    def __init__(self, name): self.name = name


def test_card_block_includes_only_active_plugins():
    mgr = BrainManager.__new__(BrainManager)
    tools = {"run-shell": _T("run-shell"),
             "google-calendar/list_events": _T("google-calendar/list_events")}
    block = mgr._plugin_usage_cards_block(tools)
    assert "Plugin: google-calendar" in block
    assert "list_events" in block          # body mentions the tool


def test_card_block_empty_without_plugin_tools():
    mgr = BrainManager.__new__(BrainManager)
    assert mgr._plugin_usage_cards_block({"run-shell": _T("run-shell")}) == ""
```

Run: `pytest tests/unit/brain/test_usage_card_injection.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add jarvis/brain/manager.py tests/unit/brain/test_usage_card_injection.py
git commit -m "feat(brain): inject active plugins' usage cards into the turn prompt"
```

---

### Task 3.2: Author usage cards for the remaining catalog plugins

**Files:**
- Create: `jarvis/marketplace/usage_cards/{github,notion,slack,vercel,supabase}.md`
- Test: `tests/unit/marketplace/test_all_cards_parse.py`

- [ ] **Step 1: Write a guard test that every connected-capable plugin parses**

```python
# tests/unit/marketplace/test_all_cards_parse.py
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.usage_cards.loader import load_usage_card


def test_every_mcp_plugin_has_a_parsable_card():
    """A plugin with an mcp_server block should ship a usage card with keywords."""
    missing = []
    for p in load_catalog().plugins:
        if not p.mcp_server:
            continue
        card = load_usage_card(p.id)
        if card is None or not card.keywords or not card.body:
            missing.append(p.id)
    assert missing == [], f"plugins missing a usable card: {missing}"
```

- [ ] **Step 2: Run to see which cards are missing**

Run: `pytest tests/unit/marketplace/test_all_cards_parse.py -v`
Expected: FAIL listing the plugins still missing a card.

- [ ] **Step 3: Author one `.md` per listed plugin**

For each, mirror the Calendar card shape: frontmatter `plugin_id` + comma `keywords` (German + English the user is likely to say), then a short body (when to use, key tools, gotchas). Keep each ≤ 20 lines.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/marketplace/test_all_cards_parse.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/usage_cards/ tests/unit/marketplace/test_all_cards_parse.py
git commit -m "feat(marketplace): usage cards for all MCP-capable catalog plugins"
```

**Wave 3 acceptance:** the Calendar card is in the prompt only on calendar turns; a "today" query uses the correct day-range + timezone.

---

# WAVE 4 — Two-speed framing, audit visibility, UI honesty

### Task 4.1: Router-prompt framing for inline-read vs delegate

**Files:**
- Modify: `jarvis/brain/router.py` (`SYSTEM_PROMPT`)
- Test: `tests/unit/brain/test_router_prompt_plugins.py`

- [ ] **Step 1: Add a clause to the router system prompt**

Append to `SYSTEM_PROMPT` a short paragraph (English):

```
When a connected plugin tool (named "<plugin>/<action>") can answer a read
request — calendar, mail, notes, issues — call it directly and answer from the
result in this turn. Only delegate to spawn-worker for genuinely multi-step or
long-running jobs, not for a single read.
```

- [ ] **Step 2: Guard test**

```python
# tests/unit/brain/test_router_prompt_plugins.py
from jarvis.brain.router import SYSTEM_PROMPT


def test_router_prompt_mentions_plugin_inline_reads():
    low = SYSTEM_PROMPT.lower()
    assert "plugin" in low
    assert "spawn-worker" in low or "spawn_worker" in low
```

Run: `pytest tests/unit/brain/test_router_prompt_plugins.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add jarvis/brain/router.py tests/unit/brain/test_router_prompt_plugins.py
git commit -m "feat(brain): router prompt frames plugin reads as inline, not spawn"
```

---

### Task 4.2: Regression guard — plugin-tools never in a worker set (AP-5/AP-14)

**Files:**
- Test: `tests/unit/brain/test_routing.py` (add)

- [ ] **Step 1: Add the guard test**

```python
# tests/unit/brain/test_routing.py (add)
def test_plugin_tools_is_router_only_not_a_spawn():
    """plugin-tools is a direct safe-gated loader; it must never become a
    spawn-style tool in a worker set (AP-5/AP-14, D9 recursion guard)."""
    from jarvis.brain.factory import ROUTER_TOOLS
    # It lives in the router set...
    assert "plugin-tools" in ROUTER_TOOLS
    # ...and no SUB_TOOLS / worker set exists to leak it into.
    import jarvis.brain.factory as f
    assert not hasattr(f, "SUB_TOOLS")
```

Run: `pytest tests/unit/brain/test_routing.py::test_plugin_tools_is_router_only_not_a_spawn -v`
Expected: PASS

- [ ] **Step 2: Commit**

```bash
git add tests/unit/brain/test_routing.py
git commit -m "test(brain): guard plugin-tools stays router-only (AP-5/AP-14)"
```

---

### Task 4.3: "Live-callable" badge in the Plugins UI

**Files:**
- Modify: `jarvis/ui/web/marketplace_routes.py` (include a `live_callable` flag per plugin in `GET /api/marketplace/plugins`)
- Modify: `jarvis/ui/web/frontend/src/views/PluginsView.tsx`
- Test: `jarvis/ui/web/frontend/src/views/PluginsView.test.tsx`

- [ ] **Step 1: Backend — add `live_callable` to each catalog plugin payload**

In the `GET /api/marketplace/plugins` handler, set per plugin:

```python
live_callable = bool(plugin.mcp_server) and str(
    plugin.mcp_server.get("transport", "")
).lower() in ("http", "stdio")
```

and include it in the serialized plugin dict (mirror how `status` is added).

- [ ] **Step 2: Frontend — show the badge**

In `PluginsView.tsx`, extend `CatalogPlugin`/`Plugin` with `liveCallable?: boolean` (map it in `adapt`), and in `PluginRow` render a small badge next to the connected indicator when `plugin.liveCallable && plugin.status === "connected"`:

```tsx
{isConnected && plugin.liveCallable && (
  <span className="text-[9px] font-medium uppercase tracking-wider text-emerald-400">
    · Live
  </span>
)}
```

- [ ] **Step 3: Frontend test**

Add a case to `PluginsView.test.tsx` asserting a connected + `liveCallable` plugin renders the "Live" badge.

Run (in `jarvis/ui/web/frontend/`):
```bash
npm run test
npm run build
```
Expected: tests pass; build succeeds. **Then restart the app** (pywebview holds the RAM bundle — memory: verify UI visually).

- [ ] **Step 4: Commit**

```bash
git add jarvis/ui/web/marketplace_routes.py jarvis/ui/web/frontend/src/views/PluginsView.tsx jarvis/ui/web/frontend/src/views/PluginsView.test.tsx
git commit -m "feat(ui): live-callable badge for connected plugins"
```

**Wave 4 acceptance:** a read answers inline; a heavy request delegates; the Plugins tab honestly shows which connected plugins the live brain can call.

---

## Final verification (after all waves)

- [ ] Full relevant suite: `pytest tests/unit/marketplace tests/integration/marketplace tests/unit/brain/test_routing.py -v` → all green.
- [ ] Boot the app, connect Google Calendar, ask (voice AND chat): "Was habe ich heute für Termine?" → Jarvis calls `google-calendar/list_events` inline and answers from real data, through `scrub_for_voice`. (memory: verify UI/behavior, don't claim done blind.)
- [ ] Headless boot guard: `python -m jarvis.ui.web.launcher --headless` still boots with zero plugins connected (loader returns `[]`, no crash) — cloud-first base-install invariant.

---

## Self-Review (run before handing off)

- **Spec coverage:** §5.1 Live bridge → Tasks 1.1–1.5; §5.2 usage cards → 2.1/3.1/3.2; §5.3 relevance → 2.2/2.3; §5.4 two-speed → 4.1; §5.5 safety/audit → MCPToolAdapter reuse (1.2) + 4.2 guard; §7 transport → 1.1/1.6 + 4.3 badge; §8 Calendar → 1.6. ✅ all covered.
- **Placeholders:** the only `<REPLACE…>` are the Calendar OAuth client-id + remote MCP URL in Task 1.6 — flagged explicitly as the spec's §11 open question, to resolve with the maintainer, NOT to invent.
- **Type consistency:** `plugin_to_mcp_server_spec → (MCPServerSpec, dict)`; `PluginToolRegistry.active_tools() → list[MCPToolAdapter]`; `load_usage_card → UsageCard|None`; `filter_plugin_tools(text, list) → list`. Names match across tasks. ✅
