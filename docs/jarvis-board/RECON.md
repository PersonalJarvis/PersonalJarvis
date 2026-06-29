# Jarvis Board — Recon Report

> Goal: document the current state of the repo at the touch points with the board plan (see `Aufgaben/Sozalmidea/JARVIS_BOARD_PLAN.md`) before Phase A starts. No code change.
>
> Conducted: 2026-04-24, branch `router-permanent-vision`.

---

## 0. Status flag: plan discrepancies that must be accounted for in the board build

No **fundamental incompatibility** — the plan is implementable. But three plan references point to outdated/non-existent artifacts. Details below; here the short version:

| Plan line | Claim | Reality |
|---|---|---|
| §5-A pre-flight #2 | "Know the FlightRecorder event schema (see `jarvis/telemetry/recorder.py` → `_FEATURED_FIELDS`)." | The constant `_FEATURED_FIELDS` does not exist. Today: `_TOP_LEVEL_FIELDS = frozenset({"trace_id", "timestamp_ns", "source_layer"})`. All other fields end up in `payload`. |
| Appendix B | "`jarvis/telemetry/replay.py` — event schema reference." | `jarvis/telemetry/replay.py` does not exist. The telemetry directory contains only `recorder.py`. The event schema reference is `jarvis/core/events.py`. |
| Appendix B | "`jarvis/memory/templates.py` — MEMORY.md/SOUL.md schema." | Exists as `jarvis/memory/templates.py` (file confirmed, content not checked in this recon). |

None of these discrepancies block Phase A. They only mean the pre-flight checks must be adjusted accordingly at the Phase A start.

---

## 1. FlightRecorder — what actually lands on disk?

### 1.1 Serialization envelope

`jarvis/telemetry/recorder.py` writes **one JSON line** per event with this structure:

```json
{
  "ts_ns":     <timestamp_ns>,
  "trace_id":  "<uuid-hex>",
  "event":     "<ClassName>",     // e.g. "HarnessCompleted"
  "layer":     "<source_layer>",  // often "", when not set
  "payload":   { ...all remaining dataclass fields... }
}
```

Relevant for aggregation:
- `event` is the **class name** (not `type: "..."` or similar).
- `payload` contains the entire event minus `trace_id`/`timestamp_ns`/`source_layer`.
- Bytes fields > 64 KB are **offloaded** to `data/flight_recorder/blobs/<sha16>.<ext>` and referenced in the JSONL as `{"__file__": "..."}`. Irrelevant for Phase A (we don't read screenshot bytes), but good to know in case the aggregator ever JSON-parses and fails.
- Daily rotation + size rotation (500 MB → `YYYY-MM-DD-2.jsonl` etc.).
- `_serialize_event` uses `dataclasses.asdict`, i.e. nested `HarnessResult`/`HarnessTask`/`Transcript` dataclasses are also recursively dict-dumped. Pydantic objects (`.model_dump()`) and `UUID` are handled correctly by `_json_default`.

### 1.2 Event types published today (from `jarvis/core/events.py`)

Grouped by responsibility. Bold = directly relevant for board Phase A/B.

**Trigger + Speech**
- `HotkeyPressed(combo: str)`
- `WakeWordDetected(keyword: str, confidence: float)`
- `ListeningStarted()`
- `UtteranceCaptured(audio_ref: str, duration_ms: int)`
- `TranscriptPartial(transcript: Transcript | None)`
- **`TranscriptFinal(transcript: Transcript | None)`** — one per spoken voice command.

**Intent + Routing**
- `IntentClassified(intent: str, risk_tier: RiskTier, entities: dict)`
- `BrainProviderSwitched(from_provider: str, to_provider: str)`
- `SecretConfigured(key: str, action: str)`

**Action lifecycle (tool-call level)**
- `ActionProposed(tool_name, args, risk_tier)`
- `ActionApproved(tool_name, approved_by)`
- `ActionDenied(tool_name, reason)`
- **`ActionExecuted(tool_name: str, success: bool, duration_ms: int, error: str | None)`** — main signal for tool usage.

**Harness dispatch**
- `HarnessDispatched(harness: str, task: HarnessTask | None)`
- `HarnessProgress(harness, result)`
- **`HarnessCompleted(harness: str, result: HarnessResult | None)`** — signal for MCP/Code-CLI/Jarvis-Agent completion. `harness` is e.g. `"mcp-remote"`, `"openclaw"`, `"codex"`, `"open-interpreter"`.

**Response + Memory**
- `ResponseGenerated(text, language, audio_ref)`
- `MemoryUpdated(namespace, key, operation)`
- `ProfileUpdated(subject, cluster, field, operation, confidence, evidence)`

**Lifecycle**
- `SystemStarted(version: str)`
- `SystemStopping(reason: str)`
- **`SystemStateChanged(new_state, previous)`** — IDLE/LISTENING/THINKING/SPEAKING/ERROR/PAUSED.
- `ConfigReloaded(changed_keys)`

**UI / Chat**
- `ThreadCreated(thread_id, title)`
- **`MessageSent(thread_id, role, text)`** — **CONTAINS USER TEXT**. For federation export the aggregator must discard this payload (see plan §5-A smoke test `test_no_pii_in_aggregated_stats`).

**Terminal (PTY)**
- `TerminalSpawned(terminal_id, shell_id, pid)`
- `TerminalOutput(terminal_id, data)` — also PII-sensitive.
- `TerminalClosed(terminal_id, exit_code)`
- `TerminalCommandExecuted(terminal_id, shell_id, command)` — PII-sensitive.

**Phase 5 — Kill/Cost/Vision/Tasks/Admin**
- `AnnouncementRequested(text, priority, language)`
- `KillRequested(source, reason)`, `KillAcknowledged(holder, took_ms)`, `TaskCancelled(task_id, reason)`
- `BudgetWarning(scope, spent_eur, limit_eur)`, `BudgetExceeded(...)`, `CooldownStarted(until_ns, reason)`, `CooldownEnded()`
- `ObservationCaptured(source, window_title, node_count, screenshot_hash, screenshot_path)`
- `VisionInjected(screenshot_hash, bytes_size, capture_age_ms)`
- `ActionPlanned(action_kind, target_hint)`, `ActionVerified(action_kind, success, reason)`
- **`TaskScheduled(task_id, trigger_type, due_at_ns, title)`**
- **`TaskStarted(task_id)`**, `TaskStepRecorded(task_id, seq, kind)`
- **`TaskCompleted(task_id, duration_ms)`**, `TaskFailed(task_id, error, will_retry)`, `TaskInterrupted(task_id)`
- `AdminOperationRequested/Completed/Rejected(op_id, op_type, ...)`

**CLI integration**
- `CliStatusChanged(cli_name, old_status, new_status, version, error)`
- `CliInstallProgress`, `CliConnectProgress` (streaming)
- **`CliInvoked(cli_name, caller, command_preview)`**, **`CliInvocationFinished(cli_name, exit_code, duration_ms)`**

**Error**
- `ErrorOccurred(layer, error_type, message, recoverable)`

**Workflows (Phase 6)**
- `WorkflowScheduled/Started/StepStarted/StepCompleted/Completed(workflow_id, run_id, ...)`

**Jarvis-Agent Dashboard (Phase 5.5)** — central to board achievements:
- **`SubJarvisStarted(parent_trace_id, utterance, context_hints, provider, model, max_duration_s, depth)`** — `utterance` is user text, PII.
- `SubJarvisReviewTriggered(iteration)`
- **`SubJarvisCompleted(success, summary, full_log_len, duration_s, cost_estimate_usd, error)`** — `summary` is plaintext, may contain PII.
- `SubJarvisBackgroundCompleted(success, utterance, summary, error, duration_s)`
- `SubJarvisAnnouncement(action, target)`
- `BrainTurnStarted(parent_trace_id, provider, model, intent_level, system_prompt_preview)`
- **`BrainTurnCompleted(tokens_in, tokens_out, cost_usd, text_len, finish_reason)`**
- **`ToolCallStarted(parent_trace_id, tool_name, args_preview)`** — `args_preview` is presumably PII.
- **`ToolCallCompleted(success, duration_ms, output_preview, error)`** — `output_preview` is PII.

### 1.3 PII filter requirements (for plan §5-A smoke test)

On export for federation/friends, the aggregator must **whitelist the following fields and NEVER pass them through**:

| Event | Field | Why |
|---|---|---|
| `MessageSent` | `text` | User utterance |
| `TerminalOutput` | `data` | PTY output |
| `TerminalCommandExecuted` | `command` | Shell command |
| `SubJarvisStarted` | `utterance`, `context_hints` | User text |
| `SubJarvisCompleted` | `summary`, `error` | LLM output |
| `SubJarvisBackgroundCompleted` | `utterance`, `summary`, `error` | ditto |
| `BrainTurnStarted` | `system_prompt_preview` | System prompt contains personal context |
| `ToolCallStarted` | `args_preview` | Tool args |
| `ToolCallCompleted` | `output_preview`, `error` | Tool output |
| `ResponseGenerated` | `text` | TTS text |
| `Transcript*` | `transcript.text` (nested) | STT output |
| `AnnouncementRequested` | `text` | TTS |
| `ActionProposed` | `args` | Tool-args dict |
| `ObservationCaptured` | `window_title`, `screenshot_path` | Screen context |

Safe-to-export fields are generally: event class name, `ts_ns`, `tool_name`, `harness`, `success`, `duration_ms`, `cli_name`, `exit_code`, counts/rates.

---

## 2. FastAPI app — instantiation, routes, lifespan

**File:** `jarvis/ui/web/server.py`

### 2.1 App instantiation

- The `WebServer` class wraps `uvicorn.Server` + `FastAPI`. Constructor `WebServer(cfg: JarvisConfig, bus: EventBus | None)`.
- The app is created in the constructor via `self.app = self._build_app()`.
- `FastAPI(title="Personal Jarvis — Admin/UI API", docs_url="/api/docs", openapi_url="/api/openapi.json")`.
- CORS middleware: only `cfg.ui.vite_dev_url` allowed as origin.

### 2.2 Route registration

Routes are registered in `_build_app()` in two phases:

**Phase 1 — REST + WS directly in WebServer:**
- `_register_rest_routes(app)` — `/api/health`, `/api/config`, `/api/plugins`, `/api/debug/emit-test-event`, `/api/window/focus`, `/api/brain/status`, `/api/terminal/shells`.
- `_register_ws_route(app)` — `/ws` WebSocket.

**Phase 2 — external routers via `app.include_router(...)`** (lazy imports, avoids cycles):
- `from .cli_routes import router` → `/api/cli/*`
- `from .mcp_routes import router`
- `from .outputs_routes import router`
- `from .preview_routes import router`
- `from .profile_routes import router`
- `from .provider_routes import router`
- `from .skills_routes import router`
- `from .sub_agents_routes import router`
- `from .tasks_routes import router`
- `from .tools_routes import router`
- `from .workflows_routes import router`
- `from conductor.api import router` (Conductor is a sibling package, not in `jarvis/`)

→ **For Phase A: create a new router `jarvis/ui/web/board_routes.py` and include it following the same pattern.** Inserting it between `workflows_router` and `conductor_router` keeps the group alphabetical.

### 2.3 Lifespan hook for background tasks

**No `@app.on_event("startup")` and no `lifespan=` context manager is used in the project.**

Instead, `WebServer.start(host, port)` starts Uvicorn with `lifespan="on"` and starts background tasks itself directly in the async context:

```python
# jarvis/ui/web/server.py:~707
if self._cli_registry is not None:
    async def _bootstrap_clis() -> None: ...
    asyncio.create_task(_bootstrap_clis(), name="cli-registry-bootstrap")
```

The skill watcher is activated analogously in `start()` via `self._skill_registry.start_watcher(loop)`. Shutdown happens in `WebServer.stop()` (stop watcher → PTY close → uvicorn `should_exit=True`).

→ **For Phase A: `BoardAggregator` is started as another `asyncio.create_task(...)` in `WebServer.start()`, after CLI bootstrap, with its own name `"board-aggregator"` — analogous to the existing convention. Shutdown is triggered in `stop()` (e.g. aggregator.stop() + await task with timeout).**

### 2.4 Important `app.state` attributes (already wired)

- `app.state.config` — JarvisConfig
- `app.state.bus` — EventBus
- `app.state.brain` — set later by the launcher, `MockBrain` in the desktop path
- `app.state.skill_registry`, `app.state.cli_registry`, `app.state.sub_agent_registry`, `app.state.preview_registry`

→ For Phase A: add `app.state.board_aggregator`; `board_routes.py` can access it via `request.app.state.board_aggregator`.

---

## 3. Frontend data-fetching pattern

**Answer: TanStack React Query (`@tanstack/react-query` ^5.56.0).** SWR and custom fetch are **not** used.

`jarvis/ui/web/frontend/package.json` confirms the dependency. All hooks in `jarvis/ui/web/frontend/src/hooks/` follow the identical pattern:

```
useBrainStatus.ts
useClis.ts
useConductor.ts
useProviders.ts
useSkills.ts
useTheme.tsx            (Zustand/Context, no data fetching)
useWebSocket.ts         (WS bridge, no HTTP)
useWorkflows.ts
```

**Conventions (extracted from `useSkills.ts`, representative):**

1. **Plain `fetch()`** in a private function per endpoint (`fetchSkills`, `saveSkill`, `reloadSkills` …), not `axios` and no central API-client class.
2. **Error handling:** `if (!res.ok) throw new Error(body.detail ?? \`HTTP ${res.status}\`)` — lets React Query set the `error` status.
3. **Query keys** as structured arrays: `["skills"]`, `["skill", name]`, `["skill-link-health", name]` — allows targeted invalidation.
4. **Mutations** via `useMutation` + `useQueryClient.invalidateQueries`. Create/Update/Delete use `onSuccess` to invalidate caches or set them directly via `qc.setQueryData(...)`.
5. **Conditional fetching** via `enabled: !!name && !!kind` (see `useSkillResource`).
6. **Stale times** are set where server state rotates rarely: `staleTime: 30 * 1000` for search, `5 * 60 * 1000` for catalog meta / link health.
7. **Hooks are named `use<Noun>List`, `use<Noun>Detail`, `use<Verb><Noun>`** (e.g. `useSkillsList`, `useSkillDetail`, `useSaveSkill`).
8. **A Zustand store** (`zustand` ^5.0.0) additionally exists for **non-server-side** client state (events, chat). No overlap with React Query.

→ **For Phase A:**
- New file `jarvis/ui/web/frontend/src/hooks/useBoard.ts` with `useBoardSummary`, `useBoardHeatmap`, `useBoardTools`, `useBoardRecords` — all as `useQuery` with polling via `refetchInterval: 30_000` (corresponds to plan decision #1 in §5-A).
- Mutations (bio regenerate, manual refresh) as `useMutation` with `invalidateQueries`.
- `recharts` must be installed additionally (not in package.json).

---

## 4. Scheduler — apscheduler yes/no?

**Answer: APScheduler is NOT present as a dependency and is deliberately NOT used.**

Grep over `requirements.txt` and `pyproject.toml`: no hit for `apscheduler|APScheduler`. Explicitly rejected in the code:

```python
# jarvis/tasks/scheduler.py, Docstring
Keine Cron-Semantik, kein APScheduler, kein zweiter Thread.
Alles laeuft im Main-Async-Loop — das ist mit Absicht (ADR-0005).
```

### 4.1 What exists in the project instead

| Module | Technique | Usage |
|---|---|---|
| `jarvis/tasks/scheduler.py` | `asyncio` + `heapq` min-heap, single loop | Phase-5 task queue. Time-based (`after_delay`, `at_time`) + event-based (`on_event`). **No cron.** ADR-0005. |
| `jarvis/workflows/scheduler.py` | `croniter` + asyncio loop | Workflow cron triggers (Phase 6). |
| `jarvis/skills/trigger_matcher.py` | `croniter` | Skill `cron` trigger matching. |
| `conductor/core/scheduler.py` | `croniter` + asyncio poll loop (1 tick/s, `next_run_at_ns` in SQLite) | Standalone cron+interval runner for Conductor. A good model. |

`croniter>=6.0` is anchored in `requirements.txt` line 87.

### 4.2 Recommendation for board nightly jobs

**Do not introduce APScheduler — it breaks the project pattern.** Instead:

1. **Primary choice: Conductor** (`conductor/core/scheduler.py`). Is explicitly committed as an "OSS tool for schedule tasks + agentic workflows" (commit `532b172a`). Jobs live in SQLite with `next_run_at_ns`, evaluated by an asyncio poll loop with `croniter`. Board aggregation nightly = a cron job in Conductor that calls `BoardAggregator.run()`. If you need recon detail on this → a separate prompt; listed only as an option in this recon.
2. **Alternatively: lightweight in the `BoardAggregator` itself.** A dedicated asyncio loop with `asyncio.sleep` until the next 02:00 local-time slot. Analogous to `jarvis/tasks/scheduler.py` (heapq with a single entry). Minimal code, no external dependency. Recommended for Phase A, because the aggregator already runs as a background task anyway and nightly would simply be another `asyncio.create_task(_nightly_loop())`.
3. **Not recommended:** Duplicating a dedicated `croniter` loop in the board module when the workflow/Conductor pattern already exists. → If cron expressions are needed (user configures the bio-regenerate time in jarvis.toml), reuse `croniter` as in `workflows/scheduler.py`.

→ **Concretely for Phase A:** Option 2 (a dedicated sleep-until-next-slot loop in the aggregator), since the plan says "every 6h + on-startup" — that is a fixed interval, not a cron expression. For Phase B (Sunday 18:00 bio regen) the same loop can then be extended, or `croniter` brought in.

---

## 5. SQLite pattern

**Answer: `aiosqlite` for new async stores, `sqlite3` stdlib for synchronous special cases. No SQLAlchemy, no Alembic.**

Grep results (only `jarvis/` code, excluding `Aufgaben/`):

**`aiosqlite` users (3 modules, the mainstream):**
- `jarvis/memory/recall.py` — memory/facts
- `jarvis/tasks/store.py` — task queue (ADR-0003)
- `jarvis/workflows/store.py` — workflow runs

**`sqlite3` stdlib users (4 modules, special cases):**
- `jarvis/clis/usage_log.py` — invocation log
- `jarvis/control/cost.py` — CostMeter (synchronous call from tight loops)
- `jarvis/skills/link_health.py` — HTTP probe cache
- `jarvis/skills/local_search.py` — FTS index

**SQLAlchemy/Alembic:** Zero hits in `jarvis/`, `conductor/`. Board plan §2 requires SQLAlchemy + Alembic explicitly for the **backend** (Phase C), not for layer A/B. For Phase A+B, therefore, stay with `aiosqlite`.

### 5.1 Canonical pattern (from `jarvis/tasks/store.py:28-80`)

```python
class TaskStore:
    name: str = "sqlite-tasks"

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
        await self._conn.executescript(schema)
```

**Conventions distilled from it:**
- Schema as a separate `schema.sql` file next to the store (`jarvis/tasks/schema.sql`).
- Lazy init via `init()`, `async with` as context manager.
- `isolation_level=None` (autocommit) + explicit transactions via `BEGIN/COMMIT` where needed.
- WAL + busy_timeout + foreign_keys are **mandatory PRAGMAs**.
- The store class has `name: str` as a class attribute.
- The schema is **additive**: all `CREATE TABLE IF NOT EXISTS`. Migration-free through idempotency — no Alembic needed.
- DB files live under `data/` (convention): `data/memory/jarvis.db`, `data/tasks.db`, `data/workflows/workflows.db`, in future `data/board/personal.db`.

→ **For Phase A:** `jarvis/board/store.py` following the same pattern, schema as `jarvis/board/schema.sql` with the three tables from plan §3 (`daily_stats`, `achievements`, `personal_records`) + `bio` (Phase B) optionally prepared already with `CREATE TABLE IF NOT EXISTS`.

---

## 6. Achievement evaluability — which achievements are already derivable from existing events?

Reference: Plan §4, `AchievementEvaluator` from §5-B. Assumption: the aggregator reads **either** the live EventBus (as a subscriber) **or** re-parses JSONL. For derivation both are equivalent, so only the event mapping is given here.

### 6.1 Mastery tier

| Achievement | Evaluable from events that exist today? | Signal / formula | Gaps |
|---|---|---|---|
| `first_mcp` | **Yes** | `HarnessCompleted.harness == "mcp-remote" AND result.success == True`, min(`ts_ns`). Alternative: `ToolCallCompleted` where `tool_name` starts with `mcp_` + `success=True`. | The plan §5-B code example expects `HarnessCompleted(harness="mcp-remote", exit_code=0)` — but the field is named `result: HarnessResult`, not `exit_code`. The evaluator must look inside `result`. |
| `tool_dabbler` / `tool_journeyman` / `tool_master` | **Yes** | `COUNT(DISTINCT tool_name) FROM ActionExecuted WHERE success=True` ≥ 5/15/30. Alternative: `ToolCallCompleted`. | None. Decision needed: count only successful executions? (The plan says "used" — yes, only success). |
| `triple_combo` | **Yes (with a session definition)** | Group per `trace_id` → `COUNT(DISTINCT tool_name) >= 3`. | "Session" ≠ `trace_id` in the strict sense — but `trace_id` is the correlation key per turn intended by the plan. If the plan means "session" as a window (e.g. 10 min), additional heuristics are needed. Assumption: `trace_id` suffices as a "session". |
| `sub_jarvis_summoner` | **Yes** | `SubJarvisCompleted.success == True` min(`ts_ns`). | None. |
| `clear_speaker` (95 % voice first-try over 100) | **Partially — critical gap** | Per voice command one would need to know: "was that a retry?". Today there is no event `VoiceRetryDetected` or `UserRephrased`. `ActionExecuted.success` combined with `TranscriptFinal` only gives "the tool action worked", not "the user didn't have to speak again". | **A new event is needed**, e.g. `VoiceAttemptResult(attempt_idx: int, first_try: bool, …)` — or the aggregator derives retries heuristically (two `TranscriptFinal` events within X seconds on the same thread without `ActionExecuted.success=True` in between = retry). The heuristic is fragile. Recommendation: **introduce a new event in Phase B**, in parallel with the achievement-implementation PR. |
| `ten_x_engineer` (10+ Jarvis-Agent hours saved/week) | **Yes (with an assumption)** | `SUM(SubJarvisCompleted.duration_s) WHERE ts_ns ∈ last 7 days >= 10*3600`. | "Saved" ≠ "ran". Assumption: when the Jarvis-Agent runs in the background, its wall-clock time counts as saved user time. The plan text implies this ("via Jarvis-Agent duration tracking" in daily_stats.hours_saved_estimate). |

### 6.2 Reflection tier

| Achievement | Evaluable? | Signal |
|---|---|---|
| `one_year_with_jarvis` | **Yes** | min(`ts_ns`) over all events (or over `SystemStarted`) + `NOW - min_ts >= 365d`. |
| `centennial` (100 successful tasks) | **Yes, with a definition choice** | What = "task"? Options: (a) `TaskCompleted` (only the Phase-5 task queue, narrow), (b) `SubJarvisCompleted(success=True)` + `ActionExecuted(success=True)` summed (broad), (c) only `ActionExecuted(success=True)` (usable, but counts every single tool call). **Recommendation:** `SubJarvisCompleted(success=True) + TaskCompleted` as a "task" — mirrors the plan narrative ("successful tasks") and avoids counting every brain turn individually. |
| `kilo_club` | Like `centennial`, threshold 1000. | ditto. |

### 6.3 Social (Phase D+) — outside layer A/B

`paired_up`, `kudos_giver`, `inspiration` come from backend events (friends, reactions) — not from the local `FlightRecorder`. Irrelevant for the Phase A/B recon.

### 6.4 Summary — achievements by event gap

**Unlock possible directly with existing events (6/8 mastery + 3/3 reflection):**
- `first_mcp`, `tool_dabbler`, `tool_journeyman`, `tool_master`, `triple_combo`, `sub_jarvis_summoner`, `ten_x_engineer`, `one_year_with_jarvis`, `centennial`, `kilo_club`.

**Requires a new event (1/8 mastery):**
- `clear_speaker` — needs **`VoiceAttemptResult(first_try: bool, retry_of: UUID | None, ...)`** or a semantic equivalent. The publisher site would be the speech pipeline (`jarvis/speech/pipeline.py`) after a successful end-of-turn, either in `SpeechPipeline.run()` or at the router/brain-turn completion.

**Alternative heuristic** (if no new event is desired in Phase B):
- A retry heuristic over two `TranscriptFinal` events in the same thread within 8 seconds without an intervening `ActionExecuted(success=True)` or `ResponseGenerated`. Fragile, but sufficient for the MVP.

### 6.5 Payload fields that populate the stats aggregation from §3

Plan §3 schema `daily_stats` against event sources:

| Stats field | Event + field |
|---|---|
| `tasks_completed` | `TaskCompleted` + `SubJarvisCompleted(success=True)` (see §6.2) |
| `tasks_failed` | `TaskFailed` + `SubJarvisCompleted(success=False)` |
| `tools_used` | `SELECT DISTINCT tool_name FROM ActionExecuted WHERE success` |
| `unique_tools_count` | `len(tools_used)` |
| `voice_commands_count` | `COUNT(TranscriptFinal)` |
| `voice_first_try_rate` | **Missing** — see §6.4 |
| `hours_saved_estimate` | `SUM(SubJarvisCompleted.duration_s) / 3600` |

---

## 7. Concrete Phase A integration points (collected)

Extracted from the recon, without implementation:

1. **Module path:** `jarvis/board/` alongside `jarvis/workflows/` and `jarvis/tasks/`. Sub-files: `__init__.py`, `aggregator.py`, `store.py`, `schema.sql`, `achievements.py` (Phase B), `profile.py` (Phase B), `prompts.py` (Phase B).
2. **DB file:** `data/board/personal.db` (via `JarvisConfig.paths.data_dir / "board" / "personal.db"`).
3. **Event source:** FlightRecorder JSONL (`data/flight_recorder/*.jsonl`) — envelope see §1.1.
4. **FastAPI hook:** New router `jarvis/ui/web/board_routes.py`, included in `server.py:_build_app()`. `app.state.board_aggregator` as a handle.
5. **Background task:** `asyncio.create_task(self._board_aggregator.run_forever(), name="board-aggregator")` in `WebServer.start()`, after `cli-registry-bootstrap`. Stop in `WebServer.stop()`.
6. **Frontend route:** New view `jarvis/ui/web/frontend/src/views/BoardView.tsx`, registered in the router configuration alongside `ClisView`/`WorkflowsView`. Hooks in `hooks/useBoard.ts` with React Query + polling.
7. **Dependencies:** `npm i recharts` (frontend). No new Python deps for Phase A — `aiosqlite` is there, `croniter` is there, no `apscheduler`, no SQLAlchemy.
8. **New event introduction for Phase B:** `VoiceAttemptResult` in `jarvis/core/events.py` **or** a retry heuristic in the aggregator (§6.4).

---

## 8. Open questions for the user (non-blocking, but good to clarify)

1. **Scheduler choice:** Should the nightly aggregator use Conductor (the existing standalone cron) or a sleep-until loop in the board module itself? Recon recommendation: a dedicated loop for Phase A (see §4.2 option 2), Conductor only when user cron expressions are needed.
2. **`clear_speaker` evaluation:** Is a new event `VoiceAttemptResult` acceptable, or would you rather accept a retry heuristic? The plan says "daily_stats.voice_first_try_rate" — recommended: introduce a new event once the aggregator runs and we actually need the signal.
3. **"Task" definition for `centennial`/`kilo_club`:** `SubJarvisCompleted + TaskCompleted` as a union, or only one of them? Recon recommendation: union, but document it explicitly once in the achievement spec.
4. **`triple_combo` session window:** `trace_id` (strict) or a time window (broader, but fuzzy)? Recon recommendation: `trace_id`, because it matches the project's correlation concept and is deterministic.

None of these blocks the start of Phase A — they are all decision points for the B implementation.

---

_End of recon._
