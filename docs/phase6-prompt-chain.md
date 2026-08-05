# Phase 6 — Prompt Chain

5-phase plan based on the closing note of the research document
(`docs/research/self-healing-architecture.md`, currently under
a private source note) and ADR-0009.

**Branch:** `phase6-self-healing` — all prompts are executed on this branch.
**Prerequisite:** ADR-0009 read, the 5 hard rules from `CLAUDE.md` §"Phase 6" internalized, `phase6-self-healing` checked out as branch.

**Order is binding:** Foundation (1) -> Worker-Harness (2) -> Critic-Loop (3) -> UI/API (4) -> Safety/Polish (5). Phase N+1 only starts once `/skill phase6-smoke-test` is green for phase N.

---

## Prompt 0 — Kickoff (TBD by the user)

Written by the user — typically:
- Branch `phase6-self-healing` off `main`, create worktree.
- Fill `docs/phase6-plan.md` with an AC table (phases 1-5, acceptance criteria per phase, smoke-test list).
- Optional: first skeleton for `jarvis/missions/__init__.py` + `tests/missions/__init__.py`.
- Final-version `docs/research/self-healing-architecture.md` from the private source note (move + commit).

---

## Prompt 1 — Foundation: Event-Schema + EventBus + EventStore + MissionManager-State-Machine + Recovery

<TBD by the user — detailed implementation assignment>

**Delivered (flat layout, from the approved plan glistening-wobbling-owl.md):**
- `jarvis/missions/event_bus.py` — per-subscriber bounded `asyncio.Queue` with drop-oldest policy, ~120 LOC.
- `jarvis/missions/event_store.py` — aiosqlite WAL writer + `events_since(seq)` reader + persist-before-publish.
- `jarvis/missions/events.py` — Pydantic v2 envelope + discriminated union over 15 payloads (`MissionBudgetWarning` instead of `BudgetWarning` because of the Phase-5 collision).
- `jarvis/missions/ids.py` — UUIDv7 helper inline (~30 LOC), no dep.
- `jarvis/missions/recovery.py` — startup scan, mark stale non-terminal missions as `FAILED("crash_recovery")`.
- `jarvis/missions/manager.py` + `state_machine.py` — `MissionManager` with state machine `PENDING -> RUNNING -> CRITIQUING -> (LOOPING -> RUNNING)* -> APPROVED|FAILED|CANCELLED|TIMED_OUT`.
- `jarvis/missions/missions_schema.sql` — `missions` + `mission_events` tables (idempotent).
- `tests/missions/` — 80 tests (UUIDv7, state machine, bus, store, manager+recovery).
- `scripts/smoke_phase6_p1.py` — end-to-end smoke, exit 0 on success.

**Open for Phase 5:** `jarvis/missions/budget.py` (token-bucket cost guard) — event type `MissionBudgetWarning` is already defined, implementation comes with the Safety phase.

**Acceptance:** `/skill phase6-smoke-test phase 1` -> `regression_in_other_phases: false`, all smoke tests green, `pytest tests/missions -v` 100% pass.

---

## Prompt 2 — Worker-Harness + Job Object + Jarvis-Agent-stream-json + Codex-CLI

<TBD by the user>

**Delivers:**
- `jarvis/missions/workers/base.py` — `WorkerHarness` protocol.
- `jarvis/missions/workers/jarvis_agent.py` — the external `openclaw agent --output-format stream-json --include-partial-messages` consumer with `--resume <session_id>` support.
- `jarvis/missions/workers/codex.py` — `codex exec --json --sandbox workspace-write --ask-for-approval never` with per-worker `CODEX_HOME`.
- `jarvis/missions/workers/stream_consumer.py` — line-buffered async NDJSON reader with tee to `<run_dir>/logs/stream.jsonl`.
- `jarvis/missions/workers/supervisor.py` — done/stuck/waiting detection (process-exit + `result` event + `api_retry` honor + 90s idle + 900s hard cap).
- `jarvis/missions/isolation/worktree.py` — `git worktree add -b agent/<task-id>`.
- `jarvis/missions/isolation/job_object.py` — Windows Job Object wrapper with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. **MUST USE the `@win32-specialist` subagent for the hook lifecycle.**
- `jarvis/missions/isolation/env.py` — per-worker minimal-allowlist ENV builder.
- `jarvis/missions/isolation/port_allocator.py` — per-worker port (4000-9000 range).
- `jarvis/missions/windows/{proc_tree,creationflags,conpty}.py` — psutil walk + `CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP|CREATE_BREAKAWAY_FROM_JOB`.
- `tests/missions/workers/`, `tests/missions/isolation/`.

**Acceptance:** `/skill phase6-smoke-test phase 2` -> smoke test spawns a dummy worker in the worktree, kills it via Job Object, verifies no zombie via `psutil`. Regression: 0.

---

## Prompt 3 — Critic-Loop + Verdict-Schema + Reflexion-Memory + Tier-Escalation

<TBD by the user>

**Delivers:**
- `jarvis/missions/critic/prompts.py` — adversarial and collaborative templates per research document §F. Anchor token (`mission.prompt` verbatim) in EVERY render.
- `jarvis/missions/critic/verdict.py` — `CriticVerdict` Pydantic model + JSON schema per research document §"Recommended Critic JSON output schema".
- `jarvis/missions/critic/runner.py` — invokes `openclaw agent --model sonnet|opus --json-schema … --max-turns 1 --permission-mode plan --bare`. **`MAX_CRITIC_LOOPS: Final[int] = 3` hardcoded.**
- `jarvis/missions/critic/log_summarizer.py` — pre-summarize step (Haiku) on the 4k-token tail.
- `jarvis/missions/critic/escalation.py` — Sonnet -> Opus on iteration 2; cross-model optional via config flag.
- `tests/missions/critic/` — unit tests for anchor-token persistence, empty-evidence rejection, MAX-cap enforcement.

**Mandatory:** `@jarvis-critic-design-reviewer` must report all 5 PASS/FAIL criteria green BEFORE prompt 4 starts.

**Acceptance:** `/skill phase6-smoke-test phase 3` -> worker-critic loop simulated with FakeWorker, verdicts are aggregated correctly, loop terminates guaranteed after 3 iterations.

---

## Prompt 4 — FastAPI WS Fan-out + React Mission Control + xterm.js + Trace-Viewer

<TBD by the user>

**Delivers:**
- `jarvis/missions/api/app.py` — FastAPI lifespan, port 8765 (or configurable).
- `jarvis/missions/api/http_routes.py` — `GET /api/missions`, `GET /api/missions/{id}`, `POST /api/kill/{id}`, `POST /api/missions/{id}/cancel`.
- `jarvis/missions/api/ws_manager.py` — `ConnectionManager` with per-client queue, `last_event_id` replay from SQLite, 200-event hot-replay buffer.
- `jarvis/missions/api/auth.py` — JWT via query param or first-message handshake (default localhost-only).
- `ui/` — Vite + React 18 + TS + Tailwind v4 + shadcn/ui + xterm.js v5 + react-arborist + Zustand + react-use-websocket.
- `ui/src/components/features/{tree,terminal,critic,trace,controls}/` — mission tree, PTY tabs, verdict panel, trace timeline, kill switch + global kill.
- `tests/missions/api/`, `ui/src/**/*.test.tsx`.

**Acceptance:** `/skill phase6-smoke-test phase 4` -> backend smoke spawns 3 parallel dummy missions, WS client receives all events in order via `last_event_id` replay, UI lazy-mounts PTY without a >34MB heap spike per terminal.

---

## Prompt 5 — Safety-Hardening + Injection-Scanner + Voice-Readback DE + Budget-Guards

<TBD by the user>

**Delivers:**
- `jarvis/missions/safety/injection_scanner.py` — PostToolUse pattern scanner against tool-output injection.
- `jarvis/missions/safety/path_guard.py` — block list `~/.ssh`, `~/.aws`, `~/.config/gh`, `.env*`, `id_rsa*`.
- `jarvis/missions/safety/tool_firewall.py` — tool input/output minimizer + sanitizer.
- `jarvis/missions/voice/readback.py` — German-summary formatter for `MissionApproved`/`MissionFailed`. Cite event_id, NEVER an LLM narrative.
- Wiring in `jarvis.toml` `[orchestrator]`, `[budget]`, `[isolation]`, `[voice]`, `[safety]`, `[ui]` per research document §"Extended jarvis.toml additions".
- `tests/missions/safety/`, `tests/missions/e2e/` — end-to-end: Voice -> MissionDispatched -> Worker+Critic -> MissionApproved -> TTS.

**Acceptance:** `/skill phase6-smoke-test phase 5` -> injection scanner blocks the prepared test payloads (CLAUDE.md, env-leak), voice-readback reads the observation summary instead of an LLM narrative, budget guard aborts at 80% with a voice warning.

---

## After Phase 5 — Production Polish (open, not in scope of the prompt chain)

- Cleanup cron for `sub-agents-outputs/` (default 14 days), started as a Phase-2 background task.
- Optional Dev-Drive (ReFS) detect + Defender exclusions setup in the wizard.
- Integration of the existing `BrainManager` cost hooks (ADR-0006) into `MissionManager.budget`.
- Two-bus bridge between DesktopApp `server.bus` and the `MissionManager` bus (resolves the bus separation described in CLAUDE.md §"Known quirk").
- Finalize the Phase-5 `SubJarvisManager` -> Phase-6 `MissionManager` promotion heuristic (see ADR-0009 §5).
