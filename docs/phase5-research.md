# Phase 5 — Research pass

**Date:** 2026-04-22
**Plan reference:** `.claude/plans/also-er-muss-auch-lexical-pond.md` §9-Phase-5, §20 (builder pattern)  <!-- i18n-allow -->
**Mandate:** `Aufgaben/Unbenanntes Dokument (1).md`
**Author context:** Code scan of phases 0–4, no changes made.

---

## 1. Mandate vs. master plan — contradictions

| # | Location | Master plan (old) | Mandate (new) | Resolution |
|---|---|---|---|---|
| 1 | Phase 5 scope | Only "Screen-Awareness + Computer-Use" (§538, 2 capabilities, 5 builders) | 5 capabilities (Seeing / Acting / Admin / Async / Control) | **Mandate wins.** Amend master plan §21: Phase 5 is now 5 capabilities, of which Admin/Async/Control are new. |
| 2 | UAC/Admin risk tier | §468: `block` for "UAC escalation, disable Defender, format, shutdown" | Admin ops are whitelisted, run via a separate helper | **No conflict if cleanly separated.** Main tool path: `block` stays. Helper: its own Pydantic vocabulary. Both paths exist in parallel; the brain can call *only* the helper, not the blocked tool path. |
| 3 | Computer-Use mechanics | §538: "Claude native `computer_20250124` + pywinauto fallback" | "multi-stage Plan-Observe-Act-Verify loop as a harness" | **Mandate clarifies.** Anthropic Computer-Use remains the LLM capability, Plan-Observe-Act-Verify is the loop orchestration around it. Both together = the `computer-use` harness. |
| 4 | Kill-Switch / Cost-Breaker / Flight-Replay | In the plan only mentioned as keywords (§10, §20.6), no implementation | Hard DoD requirement | **Mandate extends the plan.** §21 in the master plan documents these as Phase 5 scope. |

Proposal: after successful completion, the master plan gets a new §21 "Phase 5 — Final Scope" that pins down the capability list, DoD and whitelist vocabulary of the helper.

---

## 2. What exists and is reusable

**Directly pluggable, no code change needed:**

- `jarvis/core/protocols.py` — the `Harness` protocol with the `allow_computer_use: bool` field is **already provided for** → the new `computer-use` harness is a pure plugin.
- `jarvis/harness/base.py`, `manager.py` — `HarnessManager` dispatches via entry points, no core change.
- `jarvis/safety/risk_tier.py` — fnmatch whitelist/blacklist with `Blacklist > Whitelist > Default` semantics works for Admin-Helper ops if we whitelist patterns like `admin_op install_winget *`.
- `jarvis/safety/approval.py` — `ApprovalWorkflow` with timeout covers the "destructive ops need a prompt" requirement (§6.2 in the mandate).
- `jarvis/brain/rate_limit_tracker.py` — circuit-breaker pattern. Extend it for a cost budget (per-day cooldown) instead of a second tracker.
- `jarvis/core/bus.py` + `events.py` — `frozen=True` events, `subscribe_all` for the Flight-Recorder, `trace_id: UUID` carried through.
- `jarvis/trigger/hotkey.py` — `global_hotkeys` lib, multi-binding support already in the code, just not yet used in the TOML → Ctrl+Alt+Shift+K as a second binding.
- `jarvis/memory/recall.py` — SQLite with WAL + FTS5. The Task-Queue uses the same DB, new tables `tasks`, `task_steps`.
- `jarvis/setup/wizard.py` — the `SECRETS` list is the only place for new Credential Manager entries. The HMAC key must go in here.
- `jarvis/ui/web/` — the router pattern (`mcp_routes.py`, `tools_routes.py`, …) is copyable for `tasks_routes.py`.
- `jarvis/ui/web/frontend/src/` — sidebar + `SectionId` enum + view-per-tab pattern → `TasksView.tsx` builds like `SkillsView.tsx`.
- `jarvis/skills/trigger_matcher.py` — `croniter` is present, but only bound to skills. For the Task-Queue a lightweight one suffices (not APScheduler).
- `jarvis/mcp/server.py` — shows how a dedicated process entry point is built (interesting for the helper pattern).
- `jarvis/ui/tray.py` — the `_command_queue` pattern, the new "Notfall-Stop" (Emergency-Stop) entry is ~15 LOC.
- **Dependencies:** `pywinauto`, `pyautogui`, `mss`, `pywin32`, `global-hotkeys`, `pillow`, `tomlkit`, `fastapi`, `aiosqlite`, `pydantic>=2.9`, `croniter` — **all already in `pyproject.toml`**.

**Do not use, even though it exists:**
- Do not invent a `screen-snapshot` tool — Vision is an **engine**, not a tool. Tools are single actions, Vision delivers observation streams.

---

## 3. What is missing (new build)

| Area | Modules | LOC estimate |
|---|---|---|
| **Cancel infrastructure** | `jarvis/control/cancel.py` (CancelToken, CancelScope) | ~80 |
| **Cost-Breaker** | `jarvis/control/cost.py` (CostMeter, BudgetConfig) | ~120 |
| **Vision** | `jarvis/vision/{engine,screenshot,uia_tree,cache,pruning}.py` | ~500 |
| **Computer-Use harness** | `jarvis/plugins/harness/computer_use.py` + `jarvis/harness/computer_use_loop.py` (Plan-Observe-Act-Verify) | ~400 |
| **Admin-Helper** | `jarvis/admin/{client,schema,ipc}.py` + `jarvis_admin_helper.py` (separate entry) | ~500 |
| **Task-Queue** | `jarvis/tasks/{store,scheduler,runner,schema.sql}.py` | ~400 |
| **Kill-Switch** | `jarvis/control/kill_switch.py` + voice intent + tray and hotkey integration | ~150 |
| **Flight-Recorder replay** | `jarvis/telemetry/{recorder,replay}.py` + CLI | ~200 |
| **UI** | `ui/web/tasks_routes.py`, `frontend/.../TasksView.tsx`, `KillSwitchBanner.tsx` | ~300 |
| **Protocols + contract tests + fakes** | `protocols.py` extension, 3 fakes, 3 contract-test files | ~300 |
| **Integration** | `pyproject.toml`, `jarvis.toml`, `wizard.py`, `__main__.py`, `CLAUDE.md`, `README.md` | ~150 |
| **Tests** | unit + contract + integration `@pytest.mark.phase5` | ~800 |

**Total:** ~3,900 LOC prod + ~800 LOC tests. In the Phase-2/4 range.

---

## 4. Dependencies between the 5 capabilities

```
Cancel/Cost/Flight (5, Cross-Cutting)
        │
        ▼
 ┌──────┴───────────────────┐
 │                          │
Vision (1)            Admin-Helper (3)       Task-Queue (4)
 │                          │                          │
 └──────┬──────┐             └──(Ops via HMAC-IPC)     │
        ▼      │                                       │
 Computer-Use (2) ◄───────── whitelisted Helper-Ops    │
        │                                              │
        └──────────────(Task-Spec "Execute CU")──────┘
```

**Ordering constraint:**
1. **(5) first** — Cancel/Cost/Flight are the bracket. Without a cancel token in Phase 5.0 I would have to patch it into each of (1)-(4) later (a guaranteed kill regression).
2. **(1) before (2)** — Vision is input for the CU loop.
3. **(3) in parallel** — the helper is self-contained, no dependency on (1)/(2).
4. **(4) in parallel** — the Task-Queue can use a dummy runner as long as (2) is not finished.

**Sub-agent split (Phase 5.1):**
- Stream A (Vision) · Stream B (Admin-Helper) · Stream C (Task-Queue) — all three in parallel to general-purpose sub-agents, as soon as the protocols + cancel infrastructure from 5.0 are in place.

---

## 5. Third-party libs that are missing

**Almost nothing.** `pyproject.toml` scan: the already-declared deps cover everything. Only **candidates** (possibly not needed):

| Candidate | Purpose | Needed? |
|---|---|---|
| `uiautomation` (vs. `pywinauto`) | UIA tree, possibly more robust with modern WinUI3 apps | **No** — `pywinauto.uia_element_info` suffices. Fallback later if needed. |
| `APScheduler` | Task scheduler | **No** — ADR-005: lightweight own scheduler. |
| `pywin32-ctypes` | pure ctypes instead of pywin32 | **No** — `pywin32` is already included, ShellExecuteEx via `win32api.ShellExecute` works directly. |
| `pynput` (vs. `global-hotkeys`) | Alternative hotkey lib | **No** — `global-hotkeys` is already established. |

**Zero new deps.** If Vision pruning becomes ML-based (ADR-002), `scikit-learn` would be worth considering — but per YAGNI I reject it for Phase 5.

---

## 6. Architecture decisions (ADR candidates)

All with a proposal; on approval I write them out under `docs/adr/NNNN-*.md`:

| ADR | Title | Proposal |
|---|---|---|
| 0001 | IPC App ↔ Admin-Helper | **Named Pipe** (`\\.\pipe\jarvis-admin-<user>`), SDDL `D:(A;;FA;;;<SID>)`, HMAC-SHA256 with nonce |
| 0002 | UIA tree pruning | **Depth ≤ 6 + interesting roles + OnScreen-Rect**, target ≤ 150 nodes for the LLM |
| 0003 | Task-Queue storage | **Same `data/jarvis.db`**, new tables `tasks`, `task_steps` (transactional) |
| 0004 | Kill propagation | **`CancelToken` + `KillRequested` event**, subprocess harnesses additionally get `taskkill /T /F` |
| 0005 | Scheduler | **Lightweight asyncio + heapq**, no APScheduler |
| 0006 | Cost-budget hook | **Wrapper around `BrainManager.complete`**, sums per `trace_id`, raises `BudgetExceeded` + cancels the stream |
| 0007 | Flight-Recorder format | **JSONL, day-rotated**, `data/flight_recorder/YYYY-MM-DD.jsonl`, UUID encoder |

---

## 7. Known failure modes (taken from the mandate)

All risks listed in mandate §150 are addressed in §5 of the ADRs. Additionally identified:

- **`_build_speech_and_orb` in `ui/desktop_app.py` starts a second bus** (see the CLAUDE.md note from 2026-04-21). Critical for Phase 5: the kill switch must hit **both** buses. Mitigation: `KillRequested` is forwarded into the inner bus via `BrainManager.signal_kill()`. Document in ADR-004.
- **Pre-existing failures in `test_launcher_headless.py`** (from the Phase-2/4 reports) — not Phase 5, but must not get worse through the Phase 5 integration. Smoke check before merge.

---

## 8. What I want confirmed by the user

Three decisions where the mandate leaves room (upfront rather than after the fact):

1. **Helper distribution** — separate Python script via ShellExecute/runas, same venv. No PyInstaller freeze in Phase 5.
2. **Computer-Use harness** — in-process (brain calls directly), not subprocess. Advantage: the cancel token propagates trivially.
3. **Task-trigger scope** — only `after_delay`, `at_time`, `on_event`. No cron (skills have that), no RRULE.

I want these three answers before I start Task 2 (ADRs). Everything else I decide myself and document in ADRs.

---

## 9. Next step

After user sign-off on this report:
1. Task 2: write ADRs 001–007.
2. Task 3: protocols + contract tests + fakes.
3. Task 4: cancel infrastructure.
4. Only then delegate Stream A/B/C in parallel to sub-agents.

The master plan gets §21 added after the phase is complete.
