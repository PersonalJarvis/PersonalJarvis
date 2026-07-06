# Fresh-Machine Honesty Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every feature that silently depends on maintainer-only machine state must, on a fresh machine, degrade to a clear actionable message instead of a hang, a fake success, or a raw RuntimeError.

**Architecture:** Five surgical fixes at the exact silent-failure sites proven in
`docs/diagnostics/fresh-machine-forensics-2026-07.md`. No new subsystems; each
fix converts an existing dishonest path (fire placeholder OAuth client, dispatch
CU without context, report no-op as success, send tools to a tool-less model,
show green badge without probe) into an honest, user-actionable failure. This is
Level 1 of the 3-level portability program; Level 3 (fresh-install release gate)
and Level 2 (in-app doctor) get their own plan files after this lands — the gate
consumes the honest signals this plan creates.

**Tech Stack:** Python 3.11, FastAPI, pytest (asyncio_mode=auto, fakes over mocks).

## Global Constraints

- All committed artifacts (code, comments, tests, messages) are English (CLAUDE.md §1).
- The working tree is SHARED with other agent sessions: stage ONLY the files this plan touches, by explicit path; never `git add -A` / `git add .` (CLAUDE.md §9).
- AP-21: gate on capability, never provider/model NAME; an UNKNOWN capability must proceed (fail open), only an explicit "cannot" gates.
- ADR-0011: `ROUTER_TOOLS` stays untouched — the computer-use fix must NOT remove the tool from the router set; it makes the tool itself honest.
- Tool-result/HTTP error strings are English; the brain rephrases them into the turn language (never bake German into ToolResults).
- Run tests via `pytest <path> -v` from the repo root (`C:\Users\Administrator\Desktop\Personal Jarvis`).
- Conventional commits, each ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: OAuth placeholder guard in connect_start

Fresh installs ship literal `REPLACE_WITH_JARVIS_*` client ids
(`jarvis/marketplace/seed_catalog.json:116,253,278`). `connect_start` currently
fires them at Google/Slack → browser error page + 300 s pending spinner. The
predicate `is_placeholder_client_id` already exists
(`jarvis/marketplace/connect_helpers.py:53-61`) but is never called on this path.

**Files:**
- Modify: `jarvis/ui/web/marketplace_routes.py` (connect_start, ~line 342-391)
- Test: `tests/unit/marketplace/test_connect_placeholder_guard.py` (create)

**Interfaces:**
- Consumes: `is_placeholder_client_id(value) -> bool`, `resolve_pkce_client(plugin_id, cid, csec) -> (cid, csec)` from `jarvis.marketplace.connect_helpers`.
- Produces: `POST /api/marketplace/plugins/{id}/connect/start` returns HTTP 409 with a `detail` beginning `"oauth client not configured"` for placeholder PKCE/device-flow clients. Frontend already renders `detail` on non-2xx (no frontend change).

- [ ] **Step 1: Read the route file section** `jarvis/ui/web/marketplace_routes.py:320-440` to confirm local names (`spec`, `plugin_id`, `_pkce_client_id`) and the existing import of `resolve_pkce_client` (~line 359).

- [ ] **Step 2: Write the failing test**

```python
"""Placeholder OAuth clients must be rejected at connect/start (fresh-machine honesty).

A fresh install ships REPLACE_WITH_* client ids; firing them at the provider
produces a browser error page and a 300 s pending spinner. connect_start must
instead fail fast with an actionable 409.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from jarvis.ui.web import marketplace_routes as mr


def _pkce_spec(client_id: str):
    for spec in mr.load_catalog().plugins:
        if spec.auth is not None and getattr(spec.auth, "mode", "") == "oauth_pkce_loopback":
            return spec.model_copy(
                update={"auth": spec.auth.model_copy(update={"client_id": client_id})}
            )
    pytest.skip("no oauth_pkce_loopback plugin in catalog")


async def test_connect_start_rejects_placeholder_client(monkeypatch):
    spec = _pkce_spec("REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID")
    monkeypatch.setattr(mr, "_catalog_spec", lambda pid: spec, raising=False)
    with pytest.raises(HTTPException) as exc_info:
        await mr.connect_start(spec.id)
    assert exc_info.value.status_code == 409
    assert "oauth client not configured" in str(exc_info.value.detail).lower()


async def test_connect_start_secret_override_beats_placeholder(monkeypatch):
    """A downloader-supplied <family>_oauth_client_id must pass the guard."""
    spec = _pkce_spec("REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID")
    monkeypatch.setattr(mr, "_catalog_spec", lambda pid: spec, raising=False)
    monkeypatch.setattr(
        "jarvis.marketplace.connect_helpers.resolve_pkce_client",
        lambda pid, cid, csec: ("real-client-id.apps.example", None),
    )
    # The flow proceeds past the guard; it may fail LATER for unrelated
    # reasons (no browser/port in CI) — anything but the 409 guard is fine.
    try:
        await mr.connect_start(spec.id)
    except HTTPException as exc:
        assert exc.status_code != 409
```

NOTE: adapt the two `monkeypatch` targets to the route's real spec-lookup
symbol after Step 1 (if the route inlines the catalog lookup, patch
`mr.load_catalog` to return a one-plugin catalog instead — keep the asserts).

- [ ] **Step 3: Run the test — expect FAIL** (`pytest tests/unit/marketplace/test_connect_placeholder_guard.py -v`): the 409 is not raised yet.

- [ ] **Step 4: Implement the guard.** In `connect_start`, inside the `OAuthPkceLoopbackAuth` branch directly AFTER `resolve_pkce_client(...)` (~line 363), and in the `OAuthDeviceFlowAuth` branch BEFORE building the handler (~line 345):

```python
from jarvis.marketplace.connect_helpers import is_placeholder_client_id

# PKCE branch — after resolve_pkce_client:
if is_placeholder_client_id(_pkce_client_id):
    raise HTTPException(
        status_code=409,
        detail=(
            f"oauth client not configured for plugin {plugin_id!r}: the shipped "
            "catalog carries a placeholder client_id and no "
            "<family>_oauth_client_id secret is set. Open the connect dialog's "
            "'Use your own OAuth client' section (or follow the plugin's setup "
            "hint) and paste your own client id — then retry."
        ),
    )

# Device-flow branch — before DeviceFlowHandler(...):
if is_placeholder_client_id(spec.auth.client_id):
    raise HTTPException(status_code=409, detail=(
        f"oauth client not configured for plugin {plugin_id!r}: placeholder "
        "client_id in the catalog. Supply your own OAuth client first."
    ))
```

- [ ] **Step 5: Run the test — expect PASS**, plus the neighbours: `pytest tests/unit/marketplace/test_connect_placeholder_guard.py tests/unit/marketplace/test_connect_helpers.py tests/unit/marketplace/test_pkce_resource.py -v`.

- [ ] **Step 6: Commit**

```bash
git add jarvis/ui/web/marketplace_routes.py tests/unit/marketplace/test_connect_placeholder_guard.py
git commit -m "fix(marketplace): reject placeholder OAuth client ids at connect/start with an actionable 409"
```

---

### Task 2: Computer-Use tool answers honestly when the context is unset

`computer-use` is always in `ROUTER_TOOLS` (`jarvis/brain/factory.py:158`) but the
context is wired only when `[computer_use].enabled` AND a vision engine exist
(`factory.py:1008-1092`). Fresh install ⇒ every call raises
`RuntimeError: ComputerUseHarness context not set` (`jarvis/harness/computer_use_context.py:303-311`).

**Files:**
- Modify: `jarvis/harness/computer_use_context.py` (add peek helper, after line 311)
- Modify: `jarvis/plugins/tool/computer_use_tool.py` (gate in `execute`, line 159)
- Test: `tests/unit/plugins/tool/test_computer_use_tool.py` (extend)

**Interfaces:**
- Produces: `peek_computer_use_context() -> ComputerUseContext | None` (non-raising) in `jarvis.harness.computer_use_context`; `ComputerUseTool.execute` returns `ToolResult(success=False, error="computer-use is not active …")` instead of raising when the context is unset.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/plugins/tool/test_computer_use_tool.py`; reuse that file's existing `ExecutionContext` fixture/construction style):

```python
async def test_execute_without_context_returns_honest_failure(ctx):
    """Fresh machine: [computer_use].enabled=false -> context never wired.
    The tool must degrade to an actionable ToolResult, never raise."""
    from jarvis.harness.computer_use_context import set_computer_use_context
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    set_computer_use_context(None)
    tool = ComputerUseTool(bus=None)
    result = await tool.execute({"goal": "open the browser"}, ctx)
    assert result.success is False
    assert "not active" in (result.error or "").lower()
    assert "settings" in (result.error or "").lower()
```

- [ ] **Step 2: Run — expect FAIL** (currently raises RuntimeError from the dispatch path, or dispatch errors differently): `pytest tests/unit/plugins/tool/test_computer_use_tool.py -v -k without_context`.

- [ ] **Step 3: Add the peek helper** at the end of `jarvis/harness/computer_use_context.py`:

```python
def peek_computer_use_context() -> "ComputerUseContext | None":
    """Non-raising read of the global CU context (None when never wired).

    Lets callers degrade honestly on machines where [computer_use].enabled is
    false or the vision engine failed to build, instead of hitting the
    RuntimeError in get_computer_use_context().
    """
    return _CONTEXT
```

- [ ] **Step 4: Gate `ComputerUseTool.execute`** — in `jarvis/plugins/tool/computer_use_tool.py`, right after the empty-goal check (line ~162), BEFORE both the no-bus dispatch and the background-task path:

```python
from jarvis.harness.computer_use_context import peek_computer_use_context  # top of file

if peek_computer_use_context() is None:
    return ToolResult(
        success=False,
        output=None,
        error=(
            "computer-use is not active on this machine: [computer_use].enabled "
            "is false (the shipped default) or no vision engine could be built. "
            "Tell the user desktop control is currently OFF and can be switched "
            "on in Settings; do not retry this tool in this turn."
        ),
    )
```

- [ ] **Step 5: Run the whole file — expect PASS, no regressions**: `pytest tests/unit/plugins/tool/test_computer_use_tool.py tests/unit/harness/test_native_computer_use.py -v`. If an existing test wires a context via `set_computer_use_context(...)`, add `set_computer_use_context(None)` cleanup in the new test's teardown (autouse fixture already handles it if present — check the file).

- [ ] **Step 6: Commit**

```bash
git add jarvis/harness/computer_use_context.py jarvis/plugins/tool/computer_use_tool.py tests/unit/plugins/tool/test_computer_use_tool.py
git commit -m "fix(computer-use): honest ToolResult instead of RuntimeError when the CU context is not wired"
```

---

### Task 3: wiki-ingest reports a no-op as failure, not success

`jarvis/plugins/tool/wiki_ingest.py:175-185` returns `success=True` with "No
pages were modified." — the model paraphrases that as "I stored it" (forensics
Bug 12/18).

**Files:**
- Modify: `jarvis/plugins/tool/wiki_ingest.py:175-185`
- Test: `tests/unit/plugins/tool/test_wiki_ingest_honesty.py` (create; if a wiki-ingest test file already exists under `tests/unit/`, extend it instead — check with `Glob tests/unit/**/test_*wiki*.py` first)

**Interfaces:**
- Produces: the no-op branch returns `ToolResult(success=False, error=...)`; callers (brain tool loop) already handle failed ToolResults.

- [ ] **Step 1: Write the failing test**

```python
"""wiki-ingest must not report success when nothing was written (Bug 12/18)."""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.plugins.tool.wiki_ingest import WikiIngestTool


class _NoopCurator:
    async def ingest(self, text: str, source: str):
        return SimpleNamespace(applied=[], skipped_due_to_recent_edit=[], failed_validation=[])


async def test_noop_ingest_is_reported_as_failure(ctx):
    tool = WikiIngestTool()
    tool._resolve_curator = lambda: _NoopCurator()  # match the tool's real hook name
    result = await tool.execute({"text": "user's flight details", "source": "chat"}, ctx)
    assert result.success is False
    assert "not" in (result.error or "").lower() and "stored" in (result.error or "").lower()
```

Adapt the class/ctor/args to the tool's real signature after reading
`jarvis/plugins/tool/wiki_ingest.py:1-140` (tool class name, execute args
schema, curator resolution hook). Keep the two asserts unchanged.

- [ ] **Step 2: Run — expect FAIL**: `pytest tests/unit/plugins/tool/test_wiki_ingest_honesty.py -v`.

- [ ] **Step 3: Replace the no-op branch** (`wiki_ingest.py:175-185`):

```python
if not applied and not skipped and not failed:
    # Curator judged the content not salient. The old success=True here made
    # the model tell users "stored" when NOTHING was written (fresh-machine
    # forensics Bug 12/18) — a no-op is a failure from the caller's intent.
    log.info("wiki-ingest: curator returned no updates (salience filter)")
    return ToolResult(
        success=False,
        output="",
        error=(
            "nothing was stored: the curator judged the content not salient "
            "enough for the wiki. Tell the user the wiki was NOT updated; if "
            "they explicitly asked to store this, apologize and suggest they "
            "add it via the Wiki view instead."
        ),
    )
```

- [ ] **Step 4: Run + sweep for old expectations — expect PASS**: `pytest tests/unit/plugins/tool/test_wiki_ingest_honesty.py -v`, then `rg -l "No pages were modified" tests/ jarvis/` and update any test asserting the old success contract.

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/tool/wiki_ingest.py tests/unit/plugins/tool/test_wiki_ingest_honesty.py
git commit -m "fix(wiki): report a curator no-op as tool failure so the brain cannot claim a phantom write"
```

---

### Task 4: Mission worker gates on tool-calling capability

`ApiAgentWorker` sends `tools=WORKER_TOOL_SPECS` unconditionally
(`jarvis/missions/workers/api_agent_worker.py:229-235`). A tool-less model ⇒
text-only reply ⇒ empty diff ⇒ 3 critic loops ⇒ every mission "fails
mysteriously" (forensics Bug 10).

**Files:**
- Modify: `jarvis/missions/workers/api_agent_worker.py` (spawn path, where the brain instance is built, ~line 130-215)
- Test: `tests/missions/test_api_agent_worker_capability_gate.py` (create; mirror the fixture style of the existing api-agent worker tests — find them with `rg -l "ApiAgentWorker" tests/`)

**Interfaces:**
- Consumes: the brain protocol's capability probe `can_call_tools()` (may be absent on a fake — treat absent/raising as "unknown → proceed", AP-21).
- Produces: a mission against an explicitly tool-incapable model yields ONE `ClaudeResult(is_error=True)` whose `result` text contains `"cannot call tools"` and `"Jarvis-Agents"` — emitted BEFORE any brain.complete round-trip.

- [ ] **Step 1: Read `api_agent_worker.py:130-215`** to confirm the local names in `spawn` (brain construction, `model`, `session_id`, `_emit_line`, first yield).

- [ ] **Step 2: Write the failing test**

```python
"""A tool-incapable worker model must fail the mission EARLY with an actionable
message — not via empty-diff critic exhaustion (fresh-machine Bug 10)."""
from __future__ import annotations


class _NoToolsBrain:
    def can_call_tools(self) -> bool:
        return False

    async def complete(self, req):  # must never be reached
        raise AssertionError("complete() called despite can_call_tools()==False")
        yield  # pragma: no cover


async def test_tool_incapable_model_fails_early(monkeypatch, tmp_path):
    from jarvis.missions.workers import api_agent_worker as mod

    monkeypatch.setattr(mod, "_build_brain", lambda *a, **k: _NoToolsBrain(), raising=False)
    worker = mod.ApiAgentWorker("openrouter")
    events = [ev async for ev in worker.spawn(prompt="build x", cwd=str(tmp_path))]
    errors = [e for e in events if getattr(e, "is_error", False)]
    assert errors, "expected an early error result"
    msg = (errors[-1].result or "").lower()
    assert "cannot call tools" in msg
    assert "jarvis-agents" in msg
```

Adapt the brain-construction monkeypatch target and `spawn(...)` signature to
what Step 1 revealed (the worker may build the brain via a module-level helper
or inline import; patch THAT symbol). Keep the three asserts.

- [ ] **Step 3: Run — expect FAIL**: `pytest tests/missions/test_api_agent_worker_capability_gate.py -v`.

- [ ] **Step 4: Implement the gate** in `spawn`, immediately after the brain instance exists and BEFORE the turn loop:

```python
# AP-21: gate on CAPABILITY, and only on an explicit "no". Unknown -> proceed.
_can_tools = True
try:
    _can_tools = bool(brain.can_call_tools())
except Exception:  # noqa: BLE001 — capability probe must never brick a mission
    _can_tools = True
if not _can_tools:
    res = ClaudeResult(
        subtype="error_during_execution",
        is_error=True,
        session_id=session_id,
        result=(
            f"worker model {model!r} (provider {self._provider!r}) cannot call "
            "tools, and missions deliver work exclusively through tool calls. "
            "Pick a tool-capable model under Settings -> Jarvis-Agents "
            "(brain.worker), then retry the mission."
        ),
    )
    _emit_line(res)
    yield res
    return
```

Additionally, wrap the FIRST `brain.complete(req)` round-trip: if it raises and
the exception text matches `"tool" and ("support" or "404")` (OpenRouter's
"No endpoints found that support tool use"), emit the SAME honest ClaudeResult
(reuse the message above with the caught text appended) instead of letting the
raw error propagate.

- [ ] **Step 5: Run — expect PASS, plus the existing worker tests**: `pytest tests/missions/ -v -k "api_agent"`.

- [ ] **Step 6: Commit**

```bash
git add jarvis/missions/workers/api_agent_worker.py tests/missions/test_api_agent_worker_capability_gate.py
git commit -m "fix(missions): fail fast with an actionable message when the worker model cannot call tools"
```

---

### Task 5: Honest MCP live badge — probe the live registry instead of hardcoding http=live

`_mcp_live` returns `(True, None)` for every http transport
(`jarvis/ui/web/marketplace_routes.py:161-180`); a connect-time `list_tools()`
401 is swallowed (`jarvis/marketplace/plugin_registry.py:147-153`) ⇒ "CONNECTED ·
LIVE" with ZERO tools (forensics Bug 14).

**Files:**
- Modify: `jarvis/marketplace/plugin_registry.py` (`__init__`, `_connect_plugin`; add two read accessors)
- Modify: `jarvis/ui/web/marketplace_routes.py` (`_mcp_live` + its call site in `list_plugins`, lines 161-216)
- Test: `tests/unit/marketplace/test_mcp_liveness_runtime_gate.py` (extend), `tests/unit/marketplace/test_plugin_registry.py` (extend)

**Interfaces:**
- Produces on `PluginRegistry`: `live_tool_count(plugin_id: str) -> int` and `last_connect_error(plugin_id: str) -> str | None`.
- Produces in the route: an http-transport plugin with status `"connected"` but a live registry reporting 0 tools gets `live_callable=False` and `runtime_missing=<last error or "no tools loaded — reconnect">`. When NO live registry is reachable (early boot/headless), behaviour is unchanged (fail open, True).

- [ ] **Step 1: Extend `PluginRegistry`** — in `__init__` add `self._last_errors: dict[str, str] = {}`; in `_connect_plugin`'s `except` branch (line ~151-153) add `self._last_errors[plugin.id] = str(exc)` before the `return`; on success (`self._clients[plugin.id] = client`) add `self._last_errors.pop(plugin.id, None)`. Then add:

```python
def live_tool_count(self, plugin_id: str) -> int:
    """Number of live tool adapters currently registered for this plugin."""
    prefix = f"{plugin_id}/"
    return sum(1 for name in self._tools if name.startswith(prefix))

def last_connect_error(self, plugin_id: str) -> str | None:
    """The swallowed connect/list_tools error of the last attempt, if any."""
    return self._last_errors.get(plugin_id)
```

- [ ] **Step 2: Write the failing registry test** (extend `tests/unit/marketplace/test_plugin_registry.py`, reusing its existing fake client/plugin fixtures):

```python
async def test_connect_failure_is_recorded_and_tool_count_zero(registry_with_failing_client):
    reg, plugin = registry_with_failing_client  # fake client whose start()/list_tools() raises 401
    await reg._connect_plugin(plugin)
    assert reg.live_tool_count(plugin.id) == 0
    assert "401" in (reg.last_connect_error(plugin.id) or "")
```

Build `registry_with_failing_client` from the file's existing fake-client
factory pattern (a `client_factory` returning an object whose `start()` raises
`RuntimeError("HTTP 401 unauthorized")`).

- [ ] **Step 3: Run — expect FAIL, then PASS** after Step 1 lands: `pytest tests/unit/marketplace/test_plugin_registry.py -v -k recorded`.

- [ ] **Step 4: Make the route consult the live registry.** In `marketplace_routes.py`, find how the routes reach the live registry (the accessor used by `_refresh_plugin_in_live_registry`, ~line 420 — read it first). Change `_mcp_live` to accept the optional context it needs:

```python
def _mcp_live(
    mcp: dict[str, Any], *, plugin_id: str = "", status: str = ""
) -> tuple[bool, str | None]:
    transport = str(mcp.get("transport", "")).lower()
    if transport == "http":
        # Honest badge: "connected" + a live registry with ZERO tools for this
        # plugin means the session is dead (expired token, 401 at list_tools).
        # No registry reachable (early boot / headless) -> fail open as before.
        if status == "connected":
            reg = _live_plugin_registry()  # the same accessor refresh uses
            if reg is not None and reg.live_tool_count(plugin_id) == 0:
                hint = reg.last_connect_error(plugin_id) or "no tools loaded — reconnect"
                return False, hint
        return True, None
    if transport == "stdio":
        import shutil

        install = mcp.get("install") or []
        launcher = str(install[0]) if install else ""
        if launcher and shutil.which(launcher):
            return True, None
        return False, (launcher or None)
    return False, None
```

Update the single call site (`list_plugins`, line ~202) to
`_mcp_live(mcp, plugin_id=spec.id, status=status)`. If no module-level registry
accessor exists, add `_live_plugin_registry() -> PluginRegistry | None` next to
`_refresh_plugin_in_live_registry` using the same lookup that function performs,
returning None on any failure.

- [ ] **Step 5: Write the failing route test** (extend `tests/unit/marketplace/test_mcp_liveness_runtime_gate.py`):

```python
def test_http_connected_with_zero_live_tools_is_not_live(monkeypatch):
    from jarvis.ui.web import marketplace_routes as mr

    class _Reg:
        def live_tool_count(self, pid): return 0
        def last_connect_error(self, pid): return "HTTP 401 unauthorized"

    monkeypatch.setattr(mr, "_live_plugin_registry", lambda: _Reg())
    live, hint = mr._mcp_live({"transport": "http"}, plugin_id="notion", status="connected")
    assert live is False
    assert "401" in hint


def test_http_without_registry_stays_live(monkeypatch):
    from jarvis.ui.web import marketplace_routes as mr

    monkeypatch.setattr(mr, "_live_plugin_registry", lambda: None)
    live, hint = mr._mcp_live({"transport": "http"}, plugin_id="notion", status="connected")
    assert live is True and hint is None
```

- [ ] **Step 6: Run — expect PASS**: `pytest tests/unit/marketplace/test_mcp_liveness_runtime_gate.py tests/unit/marketplace/test_plugin_registry.py tests/unit/marketplace/test_plugin_status.py -v`.

- [ ] **Step 7: Commit**

```bash
git add jarvis/marketplace/plugin_registry.py jarvis/ui/web/marketplace_routes.py tests/unit/marketplace/test_plugin_registry.py tests/unit/marketplace/test_mcp_liveness_runtime_gate.py
git commit -m "fix(marketplace): live badge probes the live registry — connected-with-zero-tools is no longer shown as live"
```

---

### Task 6: Token refresh reaches the live MCP session

The refresh scheduler writes the fresh token to the keyring but never refreshes
the live `MCPClient` session (`jarvis/ui/web/launcher.py:698-703`); after the
first expiry (Notion TTL 3600 s) every call 401s while the badge stays green.
The connect path already has the right primitive:
`_refresh_plugin_in_live_registry(plugin_id)` (`marketplace_routes.py:420`).

**Files:**
- Modify: the refresh scheduler class (locate with `rg -n "class .*RefreshScheduler" jarvis/`) — add an optional `on_refreshed: Callable[[str], None] | None = None` ctor param, called with the plugin id after a successful token save.
- Modify: `jarvis/ui/web/launcher.py:694-705` — pass `on_refreshed` wired to the same live-registry refresh the connect path uses (import lazily inside the lambda/closure to avoid boot-path imports, AP-26).
- Test: `tests/unit/marketplace/test_refresh_scheduler.py` (extend)

**Interfaces:**
- Produces: `RefreshScheduler(..., on_refreshed=callable)` — fired once per successfully refreshed plugin, exceptions swallowed and logged (a UI refresh hiccup must never kill the scheduler loop).

- [ ] **Step 1: Read the scheduler** (`rg -n "class .*RefreshScheduler" jarvis/` then read the refresh-success path where tokens are saved).

- [ ] **Step 2: Write the failing test** (extend `tests/unit/marketplace/test_refresh_scheduler.py`, reusing its fixtures for a successful refresh):

```python
async def test_on_refreshed_fires_after_successful_refresh(successful_refresh_env):
    seen: list[str] = []
    scheduler = make_scheduler(successful_refresh_env, on_refreshed=seen.append)
    await scheduler.refresh_once()  # use the file's existing single-pass entry point
    assert seen == [successful_refresh_env.plugin_id]


async def test_on_refreshed_exception_does_not_break_the_loop(successful_refresh_env):
    def _boom(pid: str) -> None:
        raise RuntimeError("ui refresh failed")

    scheduler = make_scheduler(successful_refresh_env, on_refreshed=_boom)
    await scheduler.refresh_once()  # must not raise
```

Adapt `make_scheduler` / `refresh_once` to the file's real construction and
single-pass helpers (they exist — the file already tests refresh outcomes).

- [ ] **Step 3: Run — expect FAIL** (`on_refreshed` unknown kwarg): `pytest tests/unit/marketplace/test_refresh_scheduler.py -v -k on_refreshed`.

- [ ] **Step 4: Implement** — scheduler ctor stores `self._on_refreshed = on_refreshed`; in the success branch right after the token save:

```python
if self._on_refreshed is not None:
    try:
        self._on_refreshed(plugin_id)
    except Exception as exc:  # noqa: BLE001 — a UI-refresh hiccup must not kill the loop
        log.warning("refresh: on_refreshed callback failed for %s: %s", plugin_id, exc)
```

In `launcher.py`, wire it:

```python
def _refresh_live_session(plugin_id: str) -> None:
    from jarvis.ui.web.marketplace_routes import _refresh_plugin_in_live_registry

    _refresh_plugin_in_live_registry(plugin_id)

scheduler = RefreshScheduler(..., on_refreshed=_refresh_live_session)
```

(match the existing construction — only ADD the kwarg).

- [ ] **Step 5: Run — expect PASS**: `pytest tests/unit/marketplace/test_refresh_scheduler.py -v`.

- [ ] **Step 6: Commit**

```bash
git add jarvis/marketplace/<scheduler-file>.py jarvis/ui/web/launcher.py tests/unit/marketplace/test_refresh_scheduler.py
git commit -m "fix(marketplace): propagate refreshed tokens into the live MCP session so plugins survive token expiry"
```

---

### Task 7: Full-suite verification sweep

- [ ] **Step 1: Run the fast suite**: `pytest tests/unit/ tests/missions/ -m "not slow" -q`. Expected: green (pre-existing unrelated failures: note them, do not fix in this plan).
- [ ] **Step 2: Run the targeted guards**: `pytest tests/unit/brain/test_routing.py tests/unit/marketplace/ tests/unit/plugins/tool/ -q`. Expected: green.
- [ ] **Step 3: Lint**: `ruff check jarvis/plugins/tool/ jarvis/marketplace/ jarvis/ui/web/marketplace_routes.py jarvis/missions/workers/api_agent_worker.py jarvis/harness/computer_use_context.py`. Expected: clean.
- [ ] **Step 4: Update the forensics doc** — append one line per fix under "Fix priorities" marking item 1 sub-items as done with commit hashes, and commit:

```bash
git add docs/diagnostics/fresh-machine-forensics-2026-07.md
git commit -m "docs(diagnostics): mark honesty-layer fixes landed"
```
