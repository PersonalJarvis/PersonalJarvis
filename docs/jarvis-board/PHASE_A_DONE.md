# Phase A — Status Report

> Commits: `21902072` (aggregator + tests), `fa06110d` (api + lifecycle), `573e5ef8` (frontend).
> Branch: `router-permanent-vision`.
> Date: 2026-04-24.

---

## 1. Which done-criteria from PLAN.md §5-A are met?

- `/board` returns its own stats after `npm run build` + server restart (✓).
- The aggregator runs in the background without EventBus blocking — `run_forever()` hands the batch job to `asyncio.to_thread`, exceptions are swallowed and logged (✓).
- No external network calls — verified by `tests/board/test_aggregator.py::test_no_network`, which monkeypatches `socket.socket` and raises an `AssertionError` as soon as the aggregator would attempt to open a socket (✓).
- Layout responsive: stat cards stack from `grid-cols-4` to `md:grid-cols-2` on mobile, tool chart + records side by side on `xl`, below each other underneath (✓ — visually testing the production build is not possible without a UI screenshot, but the TailwindCSS classes are set).

## 2. Which achievements from PLAN.md §4 are evaluable today?

Building on the RECON.md §6 mapping — with the aggregate data now available in `daily_stats` + `personal_records`, Phase B can unlock these achievements straight from the DB, **without** introducing new events:

| Achievement | Signal in `daily_stats` / live events | Status |
|---|---|---|
| `first_mcp` | EventBus subscriber on `HarnessCompleted` with `harness == "mcp-remote"` and `result.success` | Directly evaluable (live event, not from DB) |
| `tool_dabbler` (5), `tool_journeyman` (15), `tool_master` (30) | Cumulative `DISTINCT tool` from the `daily_stats.tools_used` union across all days | **Yes** — the aggregator delivers the `tools_used` JSON list per day; the Phase-B evaluator does a `SELECT DISTINCT` and counts. |
| `triple_combo` | Per-`trace_id` grouping from the live EventBus (`ActionExecuted`) | Directly live evaluable; the aggregator needs nothing extra for it. |
| `sub_jarvis_summoner` | EventBus `SubJarvisCompleted.success=True` min(ts) | Directly live evaluable. |
| `ten_x_engineer` | `SUM(hours_saved_estimate) WHERE date >= today-7` | **Yes** — derived from `daily_stats.hours_saved_estimate`. |
| `one_year_with_jarvis` | `MIN(date)` in `daily_stats` + day difference | **Yes**. |
| `centennial`, `kilo_club` | `SUM(tasks_completed)` in `daily_stats` | **Yes** — we count `TaskCompleted + SubJarvisCompleted(success=True)` toward `tasks_completed`, as discussed in RECON. |

**The only blocker** (unchanged from RECON.md §6.4):

- `clear_speaker` (95 %+ voice-first-try rate) — Phase A delivers `daily_stats.voice_first_try_rate` via a **retry heuristic** (two `TranscriptFinal` events < 8 s = the second is a retry). That is enough for a first dashboard display, but not yet for reliable unlock logic. Phase-B recommendation: emit a new `VoiceAttemptResult` event in the speech pipeline, switch the aggregator over to it (the only code change: replacing the retry branch in `_aggregate_events`), and then base the achievement cleanly on `voice_first_try_rate >= 0.95 AND voice_commands_count >= 100`.

## 3. Deviations from the plan — what did I decide differently?

### 3.1 `sqlite3` stdlib instead of `aiosqlite`

The plan leans heavily on the existing `TaskStore`/`WorkflowStore` style (aiosqlite). I implemented the aggregator **synchronously with the `sqlite3` stdlib** and run it in `asyncio.to_thread`.

Rationale:
- The aggregator is a **batch job** (every 6 h), not a hot path. A synchronous SQLite upsert over a few thousand events is done in milliseconds — async overhead would be a luxury without payoff.
- This exact pattern is established in the project for batch/sync code (`jarvis/control/cost.py`, `jarvis/clis/usage_log.py`, `jarvis/skills/local_search.py`). The store is `sqlite3`, the API routes call it via `asyncio.to_thread(store.summary, ...)` — this keeps the event loop free and the store logic easy to test (see the plan smoke tests, which work synchronously with `agg.db.execute(...)`).
- WAL mode + short read connections make reader and writer independent — no deadlock risk.

It stays compatible with Phase B: if the `AchievementEvaluator` runs as a bus subscriber and writes in **real time**, it can certainly use `aiosqlite` — the two stores speak to the same DB, because SQLite handles multiple connections transactionally.

### 3.2 Aggregator lifecycle via `WebServer.start()`, not the FastAPI `lifespan`

Plan §5-A Decision #2 says "implement as an async task in the FastAPI lifespan". Instead I used the **project pattern**: `asyncio.create_task(agg.run_forever(...), name="board-aggregator")` directly in `WebServer.start()`, shutdown in `WebServer.stop()`.

Rationale:
- The FastAPI `lifespan=` context manager is used **nowhere** in the project (see RECON.md §2.3) — CLI registry bootstrap, skill watcher, etc. all follow this pattern.
- `WebServer.stop()` is the only place that does `self._pty.close_all()` and `set_active_registry(None)` — cancelling the aggregator there keeps the shutdown order consistent in one place.
- No semantic difference: both ways run within the same Uvicorn lifespan window.

### 3.3 Additional `/api/board/personal/refresh` route (the plan says "manual refresh button", but the route was missing from the tasks list)

Plan §5-A "Frontend Decision #1" allows a manual refresh button. It only works if the backend has an endpoint that triggers the aggregator manually. I added `POST /api/board/personal/refresh`; it calls `agg.run()` synchronously in a thread and invalidates the entire `["board"]` query tree on the frontend.

Not marked as a "Decision" in the plan, but a necessary consequence of the button requirement.

### 3.4 Personal records catalog slightly extended

Plan §3 mentions `fastest_task_completion`, `most_tools_in_session` as records. Neither is trivially derivable from today's event stream:

- `fastest_task_completion` — for which task type? `TaskCompleted.duration_ms` measures runtime, but "fastest" without a category is low in meaning and could be misinterpreted.
- `most_tools_in_session` — "session" here is not a clearly defined time frame; `trace_id` works but is narrow.

Instead I implemented 4 records that are **directly derivable from `daily_stats`** and have a consistent per-day granularity:

- `most_tasks_in_a_day`
- `most_unique_tools_in_a_day`
- `most_voice_commands_in_a_day`
- `most_hours_saved_in_a_day`

The plan records can be backfilled in Phase B or later, once the session/task semantics are defined more clearly.

### 3.5 The heatmap shows tasks per day, not "streak_level"

The plan mentions a "GitHub-style contribution grid". The only pitfall here is Plan §0 ("no breaking streaks"). I render the intensity solely from `tasks_completed` — a cell with 0 is simply "less", not "you lost your streak". The streak badge (a small info label "5-day streak") disappears without fanfare when the user skips a day.

## 4. Concrete plan constraints — how were they baked in?

| Constraint | Implementation |
|---|---|
| No push notifications | Nothing in the frontend registers the `Notification` API. No service worker. |
| No breakable streaks | `streak_days` is only rendered as an info badge, no popup when 0. Heatmap intensity is directly proportional to `tasks_completed`. |
| No time-on-site | Nowhere a "minutes talked" counter. `voice_commands_count` counts commands (output), not time. |
| No like counts | Phase A has no social layer; Phase D relevant. |
| No online indicators | Phase D relevant, N/A here. |
| No algorithmic feed sorting | The aggregator sorts everything deterministically (by date, day counts, tool days). |
| No voice transcripts to the backend | `export_all_for_federation()` does NOT touch `transcript.text`, `MessageSent.text`, `SubJarvisCompleted.summary`, `tool_args`, `output_preview` AT ALL — only aggregate counts and tool names. The smoke test `test_no_pii_in_aggregated_stats` verifies the 6 phrases from the plan. |
| No pull-to-refresh slot machine | React-Query polling: `summary` 30 s, `heatmap` 5 min, `tools` 2 min, `records` 2 min. A manual refresh button invalidates all `["board"]` queries — no endless-pull gesture. |

## 5. Smoke-test output

```
$ python -m pytest tests/board/ -v

============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.0.2, pluggy-1.6.0 -- Python 3.11.9
rootdir: C:\Users\Administrator\Desktop\Personal Jarvis
configfile: pyproject.toml
plugins: anyio-4.12.1, flaky-3.8.1, hydra-core-1.3.2, langsmith-0.6.6,
         asyncio-1.3.0, mock-3.15.1, typeguard-4.4.4
asyncio: mode=Mode.AUTO, debug=False,
         asyncio_default_fixture_loop_scope=None,
         asyncio_default_test_loop_scope=function
collecting ... collected 12 items

tests/board/test_aggregator.py::test_aggregator_groups_events_by_day PASSED [  8%]
tests/board/test_aggregator.py::test_voice_first_try_rate_excludes_retries PASSED [ 16%]
tests/board/test_aggregator.py::test_no_pii_in_aggregated_stats PASSED   [ 25%]
tests/board/test_aggregator.py::test_no_network PASSED                   [ 33%]
tests/board/test_aggregator.py::test_personal_records_populated PASSED   [ 41%]
tests/board/test_aggregator.py::test_aggregator_skips_broken_lines PASSED [ 50%]
tests/board/test_routes.py::test_summary_returns_totals_and_window PASSED [ 58%]
tests/board/test_routes.py::test_heatmap_fills_every_day PASSED          [ 66%]
tests/board/test_routes.py::test_tools_histogram_contains_used_tools PASSED [ 75%]
tests/board/test_routes.py::test_records_are_set PASSED                  [ 83%]
tests/board/test_routes.py::test_refresh_reruns_aggregator PASSED        [ 91%]
tests/board/test_routes.py::test_503_when_store_missing PASSED           [100%]

============================== 12 passed in 0.80s ==============================
```

```
$ npm run build

> jarvis-frontend@0.1.0 build
> tsc -b && vite build --outDir ../dist --emptyOutDir

vite v5.4.21 building for production...
transforming...
✓ 2929 modules transformed.
rendering chunks...
computing gzip size...
../dist/index.html                    0.45 kB │ gzip:   0.29 kB
../dist/assets/index-CjIuo0WH.css    77.06 kB │ gzip:  14.42 kB
../dist/assets/index-Y4EUsX_r.js  1,520.34 kB │ gzip: 432.20 kB

(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rollupOptions.output.manualChunks to improve chunking

✓ built in 11.19s
```

## 6. Open items for Phase B (not blocking the Phase-A ship)

1. Introduce a `VoiceAttemptResult` event and switch the aggregator over — then the <8 s heuristic falls away and `clear_speaker` becomes unlockable.
2. Create the `AchievementEvaluator` as a bus subscriber; the `achievements` table is already in `schema.sql`, it is just waiting for a writer.
3. `BioGenerator` via `BrainManager`, prompt template from PLAN.md Appendix A.
4. Bundle size: `index-Y4EUsX_r.js` is 1.5 MB (432 kB gzip), Vite warns. Phase B or later: manualChunks for `recharts` + `@tanstack/react-query`, code-split per view.

## 7. No ADR needed

All decisions are either covered by the plan or documented in §3 of this file. No architectural incompatibility, no structural deviation that would warrant an ADR.

---

_Phase A: delivered._
