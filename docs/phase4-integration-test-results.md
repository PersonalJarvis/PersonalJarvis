# Phase 4 — Harness Dispatch + MCP Server

**Date**: 2026-04-21
**Plan reference**: §17.2, §9-Phase-4

---

## What Works Now

Your brain can **let real Jarvis-Agents do the work**:

```
User (Voice): "Jarvis, frag OpenClaw ob der Build durchläuft" <!-- i18n-allow -->
   ↓
BrainManager (Phase 2) → detects tool-use intent
   ↓
dispatch_to_harness(harness="openclaw", prompt="Check build")
   ↓
HarnessManager → openclaw harness → subprocess: openclaw agent --output-format stream-json ...
   ↓
stream-JSON parsed → HarnessResult events to the bus → WebChannel → UI
   ↓
Final exit=0, stdout trimmed to max 4000 chars → ToolResult
   ↓
Brain Turn 2: replies "Der Build läuft durch. Alle Tests grün." <!-- i18n-allow -->
   ↓
TTS speaks
```

**Live verified**:
```
$ python -c "… OpenClawHarness.invoke(HarnessTask(prompt='Sag pong')) …"
Health: True
FINAL exit=0 duration=14922ms cost=$0.0
Last 500 chars of stdout:
pong
```

Real OpenClaw CLI call against your subscription: ✅ pong.

## Tests

- **248/250 passed** (+24 new Phase-4 tests). The 2 pre-existing failures in `test_launcher_headless.py` remain unchanged (Phase-1a launcher, not Phase 4).
- **Contract tests**: all 5 harness plugins structurally satisfy the `Harness` protocol.
- **Unit tests**: HarnessManager dispatch, event publishing, parallel merge, fail handling.
- **Integration**: dispatch_to_harness tool with FakeHarness (5 tests), live Python subprocess (3 tests).

## New Files

```
jarvis/harness/
  __init__.py                                 # exports HarnessManager, SubprocessHarness
  base.py            (220 LOC)                # SubprocessHarness base + stream pumping
  manager.py         (155 LOC)                # discovery, health, dispatch, parallel

jarvis/plugins/harness/
  openclaw.py     (100 LOC)                # openclaw agent --output-format stream-json
  codex.py            (55 LOC)                # codex exec --json
  python_script.py    (35 LOC)                # python -c / python file.py
  mcp_remote.py      (110 LOC)                # wraps jarvis/mcp/client.py as a harness
  open_interpreter.py (45 LOC, stub)          # health=False while package is missing

jarvis/plugins/tool/
  dispatch_to_harness.py (180 LOC)            # tool — single + parallel + trim

jarvis/mcp/
  server.py          (180 LOC)                # FastMCP server, 5 tools + 2 resources

jarvis/core/config.py  (+ HarnessConfig, MCPServerConfig)
jarvis/brain/factory.py (+ dispatch-to-harness in the active_tools filter)

tests/
  fixtures/harness/fake_harness.py
  contract/test_harness_protocol.py           (11 tests)
  unit/test_harness_manager.py                (5 tests)
  integration/test_dispatch_to_harness.py     (5 tests)
  integration/test_python_script_harness.py   (3 tests, live subprocess)
```

## Harness Roster

| Plugin | Status | Invocation |
|---|---|---|
| `openclaw` | ✅ live verified | `openclaw agent --output-format stream-json --include-partial-messages --max-turns 10 <prompt>` |
| `codex` | ✅ binary found, SDK flow | `codex exec --json --sandbox workspace-write <prompt>` |
| `python-script` | ✅ live verified | `python -X utf8 -c <code>` or `python <@file.py>` |
| `mcp-remote` | ✅ wrapper around the Phase-1c MCPClient | uses `BOOTSTRAP_SERVERS` |
| `open-interpreter` | ⏸ stub (package optional) | `pip install open-interpreter` then active |
| `hermes` | ⏭ Plan Phase-4 stretch | needs WSL2 — not yet implemented |

## Jarvis-as-MCP-Server

Start:
```bash
python -m jarvis.mcp.server --transport stdio
```

Register in OpenClaw:
```bash
claude mcp add jarvis python -m jarvis.mcp.server
```

Exposed capabilities:
- `memory_search(query, k)` — BM25-FTS5 recall
- `memory_recent(limit, role)` — last N messages
- `memory_add_fact(fact, category)` — core-memory addition
- `skills_list()` — all registered Jarvis skills
- Resource `jarvis://core-memory/persona` — persona + user facts as text
- Resource `jarvis://core-memory/all` — core memory as JSON

**Loop detection**: the `JARVIS_MCP_DEPTH` env var is incremented on each server start. From `max_call_depth = 3` (from `jarvis.toml`) onward, the server rejects further tool calls — this prevents `dispatch_to_harness → openclaw → jarvis-mcp → dispatch_to_harness → …` infinite loops.

## Safety Integration

The `dispatch_to_harness` tool has `risk_tier = "monitor"`. The user can downgrade specific harness patterns to `safe` in `jarvis.toml` under `[safety.whitelist]`:

```toml
commands = [
    "dispatch_to_harness openclaw *",     # auto-approve OpenClaw
    "dispatch_to_harness python-script *",   # auto-approve Python scripts
]
```

## Voice Integration (already active)

Because `BrainManager` already has the `dispatch_to_harness` tool in its tools registry and the voice pipeline is cleanly connected via `jarvis/brain/factory.py`, the next voice call is enough:

**User**: *"Jarvis, lass Python ausrechnen wie viel 2 hoch 64 ist"*
**Brain**: calls dispatch_to_harness(harness="python-script", prompt="print(2**64)")
**Claude-Opus response** (after tool result): *"2 hoch 64 ist 18 446 744 073 709 551 616."*

## Known Issues

1. **Open-Interpreter** is a stub — needs `pip install open-interpreter` + an in-process loader
2. **Hermes WSL2** not implemented — not a user priority
3. **2 pre-existing failures** in `test_launcher_headless.py` — belong to the Phase-1a launcher, not Phase 4
