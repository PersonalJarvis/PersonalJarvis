# Phase 2 — Integration Test Report

**Date**: 2026-04-21
**Test runner**: main model (without sub-agents, direct implementation)
**Plan reference**: `<USER_HOME>\.claude\plans\also-er-muss-auch-lexical-pond.md` §20  <!-- i18n-allow -->

---

## Summary

- **68/68 Phase-2 tests green** on the first run (no revisions needed)
- **224/226 across the whole project green** — 2 pre-existing failures in `test_launcher_headless.py` (belong to Phase 1a/1b, not Phase 2)
- **All 8 brain provider plugins** are discovered via `importlib.metadata`
- **All 5 Phase-2 tools** are discovered and match their risk tier
- **FTS5 + BM25 + WAL mode** works, trigger sync between `messages` and `messages_fts` verified
- **No Phase-1b files touched** (jarvis/audio, jarvis/speech, jarvis/plugins/stt|tts|wake, jarvis/trigger/hotkey, jarvis/__main__.py, jarvis/ui/tray.py all unchanged)

**Result**: Phase 2 is **COMPLETE** and ready for the one-line swap in `__main__.py:474` (GeminiTestBrain → BrainManager) once Phase 1b is finished.

---

## Acceptance Criteria (Plan §20.6)

| ID | Check | Status |
|---|---|---|
| `ac_imports` | `from jarvis.brain.manager import BrainManager; from jarvis.memory import RecallStore, CoreMemory; from jarvis.safety import ToolExecutor, RiskTierEvaluator` | ✅ PASS |
| `ac_brain_plugins_8` | `python -m jarvis --plugins` lists claude-subscription, claude-api, openrouter, openai, gemini, grok, ollama-cloud, ollama-local | ✅ PASS |
| `ac_tool_plugins_5` | Lists open-app, type-text, run-shell, search-web, remember | ✅ PASS |
| `ac_fts5_schema` | `CREATE VIRTUAL TABLE messages_fts USING fts5(...)` in `data/jarvis.db` | ✅ PASS |
| `ac_core_memory_file` | `CoreMemory.load()` produces a valid `data/core_memory.json` | ✅ PASS |
| `ac_brain_contract` | `pytest tests/contract/test_brain_protocol.py` | ✅ PASS (17 tests) |
| `ac_tool_contract` | `pytest tests/contract/test_tool_protocol.py` | ✅ PASS (11 tests) |
| `ac_risk_tier` | `pytest tests/unit/test_risk_tier.py` | ✅ PASS (7 tests) |
| `ac_recall_bm25` | `pytest tests/unit/test_recall_store.py` | ✅ PASS (7 tests) |
| `ac_core_memory` | `pytest tests/unit/test_core_memory.py` | ✅ PASS (5 tests) |
| `ac_cache_heartbeat` | `pytest tests/unit/test_cache_heartbeat.py` | ✅ PASS (3 tests) |
| `ac_iteration_budget` | `pytest tests/unit/test_iteration_budget.py` | ✅ PASS (4 tests) |
| `ac_streaming_aggregate` | `pytest tests/unit/test_streaming_aggregate.py` | ✅ PASS (4 tests) |
| `ac_e2e_brain_to_tool` | `pytest tests/integration/test_phase2_e2e.py` | ✅ PASS (2 tests) |
| `ac_provider_switch` | `pytest tests/integration/test_provider_switch.py` | ✅ PASS (4 tests) |
| `ac_safety_blacklist` | `pytest tests/integration/test_safety_blacklist.py` | ✅ PASS (2 tests) |
| `ac_memory_recall` | `pytest tests/integration/test_memory_recall.py` | ✅ PASS (2 tests) |
| `ac_launcher_snapshot` | `python -m jarvis.brain.launcher --snapshot --no-memory` | ✅ PASS |
| `ac_launcher_list_providers` | `python -m jarvis.brain.launcher --list-providers` | ✅ PASS |
| `ac_launcher_echo` (live) | `python -m jarvis.brain.launcher --provider ollama-local --prompt "..."` | ⏸ SKIPPED (needs a local Ollama server + model) |

**Note**: `ac_launcher_echo` as a live test was replaced by static AC variants. The real live test can be run manually when the user has an Ollama server or API key ready.

---

## Test Coverage by Area

### Brain layer
- `tests/unit/test_streaming_aggregate.py` — 4 tests (text-concat, tool-collect, usage-sum, tee_text)
- `tests/unit/test_iteration_budget.py` — 4 tests (turns-cap, tokens-cap, snapshot)
- `tests/unit/test_cache_heartbeat.py` — 3 tests (periodic-fire, clean-stop, error-swallow)
- `tests/contract/test_brain_protocol.py` — 17 tests (8 providers × 2 checks + 1 discovery)
- `tests/integration/test_phase2_e2e.py` — 2 tests (tool-use-loop, simple-text)
- `tests/integration/test_provider_switch.py` — 4 tests (switch-event, idempotent, voice-intent, alias)

### Memory layer
- `tests/unit/test_recall_store.py` — 7 tests (schema, BM25-ranking, kv-crud, recent, role-filter)
- `tests/unit/test_core_memory.py` — 5 tests (defaults, facts, dedup, system-prompt, corrupt-recovery)
- `tests/integration/test_memory_recall.py` — 2 tests (auto-log, empty-skip)

### Safety layer
- `tests/unit/test_risk_tier.py` — 7 tests (default, whitelist-downgrade, blacklist-hard, priority, case-insensitive, needs-confirm)
- `tests/integration/test_safety_blacklist.py` — 2 tests (format-denied, git-status-whitelisted)

### Tool layer
- `tests/contract/test_tool_protocol.py` — 11 tests (5 tool-discovery + attribute-validation + risk-tier-match)

**Total**: 68 new tests, all green.

---

## Files Delivered

### jarvis/memory/
- `__init__.py` (exports)
- `schema.sql` (WAL + FTS5 + triggers)
- `recall.py` (RecallStore with aiosqlite, 200 LOC)
- `core_memory.py` (JSON persistence + system-prompt-block rendering, 135 LOC)
- `message_recorder.py` (bus subscriber, 42 LOC)

### jarvis/safety/
- `__init__.py` (exports)
- `risk_tier.py` (fnmatch-based evaluator with Blacklist>Whitelist>Default, 95 LOC)
- `approval.py` (dual-channel approval with asyncio.Future + timeout, 80 LOC)
- `tool_executor.py` (evaluate→approve→execute→log pipeline, 110 LOC)

### jarvis/brain/
- `__init__.py` (exports)
- `iteration_budget.py` (turns+tokens tracking, 40 LOC)
- `cache_heartbeat.py` (asyncio task with stop signal, 60 LOC)
- `streaming.py` (BrainDelta aggregator + tee-stream, 50 LOC)
- `provider_registry.py` (entry_points discovery, 55 LOC)
- `tool_use_loop.py` (multi-turn tool-execution loop, 135 LOC)
- `dispatcher.py` (BrainDispatcher: single-shot + streaming, 110 LOC)
- `manager.py` (BrainManager: pipeline adapter, switch, fallback chain, 230 LOC)
- `launcher.py` (standalone CLI, 145 LOC)

### jarvis/plugins/brain/
- `_anthropic_base.py` (shared Anthropic logic, 135 LOC)
- `_openai_base.py` (shared OpenAI-compatible logic, 145 LOC)
- `_ollama_base.py` (shared Ollama logic, 100 LOC)
- `claude_api.py` (45 LOC)
- `claude_subscription.py` (58 LOC — OAuth token OR API key)
- `openai.py` (40 LOC)
- `openrouter.py` (50 LOC)
- `grok.py` (40 LOC)
- `gemini.py` (125 LOC — google-genai SDK)
- `ollama_local.py` (38 LOC)
- `ollama_cloud.py` (45 LOC)

### jarvis/plugins/tool/
- `open_app.py` (45 LOC — os.startfile + subprocess fallback)
- `type_text.py` (35 LOC — pyautogui with graceful missing-dep)
- `run_shell.py` (60 LOC — asyncio.create_subprocess_exec, shell=False)
- `search_web.py` (60 LOC — DuckDuckGo Instant-Answer API)
- `remember.py` (35 LOC — CoreMemory wrapper)

### tests/
- `tests/fixtures/brain/fake_brain.py` + `__init__.py`
- 6 new unit-test files
- 2 new contract-test files
- 4 new integration-test files

**Total LOC**: ~3500 in production code + ~900 in tests.

---

## Known Issues & Deliberate Limitations

1. **`claude-subscription` OAuth is prepared, but not active**
   Anthropic officially does not support an OAuth flow for third-party apps outside of OpenClaw. The plugin loads the token via `claude_oauth_token` from the keyring if present, otherwise it falls back to `anthropic_api_key`. Once Anthropic opens OAuth, only the token swap in `keyring` needs to happen — no code change.

2. **2 pre-existing failures in `test_launcher_headless.py`**
   Verified via `git stash` test: the failures also exist without the Phase-2 changes. They belong to the Phase-1a/1b launcher and will be fixed by the parallel dev when 1b is completed.

3. **The `type_text` tool requires `pyautogui`**
   Graceful fail when not installed (returns a clear error message). `pyautogui` is a Phase-1b dep and will come with its completion.

4. **The `open_app` tool uses `os.startfile` instead of `pywin32`**
   `pywin32` is not immediately available; `os.startfile` is enough for standard apps and files. If we later need admin elevation or window-focus control, upgrade to `pywin32` in Phase 5.

5. **Live provider test not run**
   I have no API keys in this session's keyring. The user can test manually:
   ```bash
   python -m jarvis.brain.launcher --provider ollama-local --prompt "Hallo"
   python -m jarvis.brain.launcher --provider claude-api --prompt "Was ist 2+2?"
   python -m jarvis.brain.launcher --provider gemini --prompt "Hallo"
   ```

6. **ChromaDB / archival memory not in Phase 2**
   Per plan decision (2026-04-21): ChromaDB comes only in Phase 6. FTS5 + core memory is enough for Phase 2.

7. **Agent-Teams feature not in Phase 2**
   Per plan decision (2026-04-21): Agent-Teams (Plan §18) will be built separately in Phase 2.5.

---

## Integration Note for Phase-1b Completion

The Phase-1b dev needs to change exactly **one line** to wire in the BrainManager:

```python
# jarvis/__main__.py:~474  (before: GeminiTestBrain)
from jarvis.brain.manager import BrainManager
# ... in setup before the pipeline:
brain_manager = BrainManager(config=config, bus=bus, core_memory=..., ...)
# and pass as:
brain_callback=brain_manager   # BrainManager implements __call__(text)->str
```

`BrainManager.__call__(text: str) -> str` matches exactly the `BrainCallback` signature in `speech/pipeline.py:41`.

---

## Self-Correction-Loop Summary (Plan §20.8)

Revision Loop 1 was sufficient — all 68 tests green on the first run. No Revision Loop 2/3 needed.

This reflects:
- The detailed 5-agent research **before** writing proactively eliminated signature conflicts
- The protocol-first design of the Phase-0 infrastructure set clear boundaries
- The fake-brain + in-memory-SQLite pattern for tests eliminated API-key dependency

**Next Step**: User decision on whether Phase 2.5 (Agent-Teams) or Phase 3 (wake-word always-on) comes next.
