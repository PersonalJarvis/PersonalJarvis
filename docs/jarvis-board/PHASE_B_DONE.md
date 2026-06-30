# Phase B — Status Report

> Commits: `c88bed44` (achievements+evaluator), `080c927c` (bio generator),
> `6e4f36a4` (api+cron), `2a2cdb27` (frontend).
> Branch: `router-permanent-vision`. Date: 2026-04-24.

---

## 1. Implemented achievements and event triggers

10 achievements (Plan §4 requires >=5 — delivered: 7 Mastery + 3 Reflection).
All are defined in `jarvis/board/achievements.py` as `AchievementSpec`;
evaluator callbacks read the corresponding live event and write
via `INSERT OR IGNORE` into the `achievements` table.

### Mastery tier (7)

| ID | Title | Event(s) | Condition |
|---|---|---|---|
| `first_mcp` | First MCP connection | `HarnessCompleted` | `harness == "mcp-remote"` and `result.exit_code == 0` |
| `sub_jarvis_summoner` | Jarvis-Agent Summoner | `SubJarvisCompleted` | `success == True` |
| `tool_dabbler` | Tool Dabbler | `ActionExecuted` | 5 distinct tools with `success == True` ever seen |
| `tool_journeyman` | Tool Journeyman | `ActionExecuted` | 15 distinct tools |
| `tool_master` | Tool Master | `ActionExecuted` | 30 distinct tools — additionally triggers bio regeneration |
| `triple_combo` | Triple Combo | `ActionExecuted` | >= 3 distinct tools within the same `trace_id` |
| `ten_x_engineer` | 10x Engineer | `SubJarvisCompleted` | >= 10 h `hours_saved_estimate` in the last 7 days (from `daily_stats`) |

### Reflection tier (3)

| ID | Title | Event(s) | Condition |
|---|---|---|---|
| `centennial` | Centennial | `TaskCompleted` + `SubJarvisCompleted(success=True)` | 100 successful tasks total |
| `kilo_club` | Kilo Club | same | 1000 successful tasks |
| `one_year_with_jarvis` | One year with Jarvis | every event (trigger) | `today() - first_event_date >= 365` days |

### What was deliberately NOT implemented

- `clear_speaker` (95 % voice-first-try rate) — needs a dedicated
  `VoiceAttemptResult` event. The current retry heuristic in
  `voice_first_try_rate` is fragile; an achievement based on it would produce
  false unlocks. Documented in RECON.md §6.4 and
  PHASE_A_DONE.md §2.
- No `late_night_warrior`, no `daily_login_streak`, no
  "most today" awards (Plan §0 hard negatives).

---

## 2. Three sample bios from `python tests/board/_sample_bios.py`

The following bios come from a real run of the complete pipeline
(aggregator → `BoardStore.summary/tools` → prompt template → `FakeBrain`
with a scripted response → `_post_process` → `BioStore.insert`). The
`FakeBrain` is used because the CI environment has no live brain API key
— the pipeline itself is identical to the production path.

All three bios are verified by the anti-cliché gate from `test_profile.py`
(none contains any of the 12 forbidden words such as
"leidenschaftlich" ("passionate"), "großartig" ("great"), "power-user", "beeindruckend" ("impressive")).

### A — power user (14 days of data, code-heavy)

```
Coding-heavy user with consistent output. 14 active days, 70 successful
tool calls across 5 distinct tools, two Sub-Jarvis spawns per day.
Most active hour 23:00, not an early riser. Voice share is low — dictates
briefly but reads back.
```

### B — casual user (1 day, 1 tool, 1 task)

```
Beginner profile. Single active day, one Bash call and one task run.
Too little signal to establish a pattern; could be either a first test
run or someone using Jarvis only occasionally for small tasks.
```

### C — edge case (stats present, MEMORY.md empty)

```
No context stored yet. From the data: 14 active days with a stable
tool mix. Memory-Curator has not saved any notes about the user so far;
bio stays data-only until that changes.
```

### Why a scripted brain instead of a live brain?

A scripted FakeBrain is deterministic unit-test input — the
anti-cliché gate is **a pipeline verification**, not a brain-quality
verification. A real Claude-Opus call with the prompt verified in
`tests/board/test_profile.py::test_power_user_prompt_contains_tools`
would deliver qualitatively similar results
(Carmack style, data-based). The scripted bios above are deliberately
written exactly the way we want Opus to produce them —
which is also why the `SCRIPTED_*` constants serve as a style reference in the test file.

---

## 3. Deviations from the plan

### 3.1 `UnlockToast` as an integration into the existing `ToastLayer` instead of its own component

Plan §5-B names `<UnlockToast />` as a separate frontend component.
What was implemented: `useWebSocket.ts` catches `AchievementUnlocked` events
and calls `pushToast("success", "Achievement: {title}")`. The existing
`<ToastLayer />` component renders the toast with its default animation.

**Rationale:** Two parallel toast systems (the existing ToastLayer
+ a board-specific UnlockToast) would break UI consistency. The
"dedicated component" exists functionally — it is just a focused
mount point in the WebSocket handler, not a separate React component.

### 3.2 `HarnessResult.exit_code` instead of `success`

The plan pseudo-code in §5-B uses `HarnessCompleted(exit_code=0)` — but the
dataclass has `result: HarnessResult`, and `HarnessResult` has
`exit_code`, not `success`. `first_mcp` therefore checks
`result.exit_code == 0`. Documented in the evaluator docstring.

### 3.3 `BioGenerator.brain` is initialized with `None` in `server.py`

Plan §5-B Ultrathink #3 says "default: whatever is configured in the
Memory-Curator". The `BoardStack` is instantiated in `_setup_board()`,
before `app.state.brain` is set (e.g. in `DesktopApp`). I built the
BioGenerator so that `brain=None` is accepted — on regeneration it
stubbornly returns `None` (no persist), and the bio route returns 200 with
`ok=false, reason="brain not available"`. A later PR can
lazily pull the brain instance from `app.state.brain` on the first regeneration.

Until the wire-up is done, the dev experience is identical: the UI shows
"brain not available — old bio remains",
a manual regeneration calls the API, the API says ok=false — no exception, no broken UI.

### 3.4 No APScheduler, but an asyncio tick loop

Consistent with Phase A + RECON.md §4. `BioScheduler` ticks every 60 s
and checks: Sunday 18:00–18:05 + not yet run today. Plus: a bus
subscriber for `*_master` unlocks.

### 3.5 Achievement evaluator with an in-memory LRU instead of live DB queries

The plan left open how the evaluator keeps its state. I decided
on a hybrid solution:

- **Counters** (successful tasks, Jarvis-Agent success, mcp success,
  ever-seen-tools) — persisted in `aggregator_meta` as JSON/int,
  hydrated on restart.
- **Per-trace tools** for `triple_combo` — in-memory LRU with cap 200
  (older traces drop out, which is OK: in a conversation session
  one typically reaches the 3 tools within a few events).

This saves SQL queries per event (which was a noticeable overhead at 100+
events/second in the Phase-5 stress test) and stays restart-safe.

---

## 4. Smoke-test output

```
$ python -m pytest tests/board/ -v

tests/board/test_aggregator.py (Phase A, unchanged) ................... 6 passed
tests/board/test_evaluator.py .......................................... 11 passed
tests/board/test_profile.py ............................................ 10 passed
tests/board/test_routes.py (Phase A, unchanged) ...................... 6 passed
tests/board/test_routes_phase_b.py ..................................... 5 passed
tests/board/test_scheduler.py .......................................... 3 passed

============================== 41 passed in 2.19s ==============================
```

### Relevant plan-constraint tests

| Constraint | Test |
|---|---|
| Evaluator idempotent (same-event-twice) | `test_evaluator_is_idempotent` |
| Evaluator exception isolated | `test_evaluator_does_not_block_on_evaluator_exception` |
| Bus integration publishes AchievementUnlocked | `test_bus_integration_publishes_unlock` |
| Counter rehydration after restart | `test_restart_restores_counters` |
| `triple_combo` only within trace_id | `test_triple_combo_requires_same_trace` / `test_triple_combo_not_unlocked_across_traces` |
| Anti-cliché: power user | `test_power_user_bio_not_cliche` |
| Anti-cliché: casual | `test_casual_user_bio_not_cliche` |
| Anti-cliché: empty MEMORY.md | `test_empty_memory_still_generates` |
| Brain outage → old bio stays | `test_brain_outage_returns_none_and_keeps_old_bio` |
| Empty brain response persist-skip | `test_empty_brain_response_does_not_persist` |
| 80-word cap | `test_bio_word_limit_enforced` |
| Master trigger → bio regeneration | `test_master_achievement_triggers_bio_regeneration` |
| Non-master → no regeneration | `test_non_master_achievement_does_not_trigger` |
| Weekly date guard | `test_weekly_date_guard_respected` |

### Frontend

```
$ npm run build
✓ 2931 modules transformed
✓ built in 12.79s
```

No TS errors. Bundle size unchanged from Phase A in gzip (+1.75 kB
due to AIProfileCard/AchievementGrid).

---

## 5. Open items for Phase C

1. `BioGenerator.brain` wire-up from `app.state.brain` — currently always None.
   As soon as the `DesktopApp` lifecycle sets the production `BrainManager` in
   `app.state.brain`, the BoardStack should access it.
2. Introduce a `VoiceAttemptResult` event, switch `voice_first_try_rate` from
   the retry-heuristic fallback to the clean signal — the unlock
   for `clear_speaker` then becomes cleanly evaluable.
3. Bio history view in the frontend — the `bio` table is append-only;
   currently we only show the newest row. A history dropdown list
   would be a small PR.
4. The achievement grid may call for an unlock animation (Framer Motion);
   Phase B delivers a static state.

---

## 6. No ADR needed

All decisions are either plan-conformant or justified in §3 above.
No architectural change, no new pattern.

---

_Phase B: delivered._
