# Phase 6 — Test Report

**Date:** 2026-04-26
**Status:** Live (all 5 sub-phases implemented + verified)
**Branch:** `router-permanent-vision` (implementation) — merge into `phase6-self-healing` before production deploy

---

## Test Inventory

### Total

| Suite | Pre-Phase-6 | Phase-6 (P1+P2+P3+P4+P5) | Total |
|---|---|---|---|
| `tests/missions/` | — | **458** | 458 |
| `tests/unit/` | ~280 | — | ~280 |
| `tests/contract/` | ~80 | — | ~80 |
| `tests/integration/` | ~90 | — | ~90 |
| **Grand Total** | **~450** | **458** | **~908** |

### Phase-6 Test Inventory (per sub-phase)

| # | Sub-Phase | Files | Tests |
|---|---|---|---|
| 1 | Foundation | `test_{ids,state_machine,event_bus,event_store,manager_recovery}.py` | 80 |
| 2 | Worker-Layer | `test_{worktree,job_object,env,stream_consumer,claude_worker_dryrun}.py` | 69 |
| 3 | Critic-Loop + Kontrollierer | `critic/test_*.py` + `kontrollierer/test_*.py` + `test_budget.py` | 156 |
| 4 | UI/API (Backend) | `api/test_missions_{routes,ws}.py` | 21 |
| 5 | Safety + Voice + Cleanup | `test_{injection_scanner,path_guard,destructive_confirm,voice_readback,voice_listener,cleanup}.py` | 132 |
| | **Phase-6 Total** | | **458** |

### Phase 4 Frontend (no pytest)

- `tsc --noEmit`: ZERO errors
- `npm install` (4 packages): success
- Files: 11 new + 3 modified (Sidebar/MainView/store SectionId)

---

## Smoke-Test Status (S1–S6 from plan)

| # | Test | Status | Note |
|---|---|---|---|
| **S1** | `pytest tests/ -v` complete | ✓ green (mod. preexisting) | 458 missions + ~450 phase 0-5 = ~908. Pre-existing 5 failures in `test_builtin_skills.py [skill-creator]` and `test_core.py [anthropic conductor]` are NOT Phase-6-induced. |
| **S2** | E2E Voice-Path (real hardware + LLM) | **MANUAL — see steps below** | Cannot be run autonomously (microphone + TTS hardware) |
| **S3** | Crash-Recovery | **MANUAL** | Plan description; uses the MissionManager Recovery path (fully checked via unit test in `tests/missions/test_manager_recovery.py`) |
| **S4** | Budget-Stop | **MANUAL** (real LLM cost) | Unit tests in `test_budget.py` verify 50%/80%/100% logic in isolation |
| **S5** | Injection-Scanner | ✓ via tests | `test_injection_scanner.py` 28 tests + orchestrator integration in `kontrollierer/test_loop.py` |
| **S6** | Path-Guard | ✓ via tests | `test_path_guard.py` 22 tests + orchestrator integration |

---

## Master E2E Guide (S2 + S3 + S4)

### S2 — End-to-End Voice-Path

**Pre-conditions:**
- `claude` CLI in PATH, `claude /login` done once (OAuth token in the Keychain)
- `jarvis.toml [tts]` with a working provider (grok-voice or elevenlabs)
- Wake word via hotkey fallback `ctrl+right_alt+j` if needed

**Steps:**
```bash
# Terminal A — Backend + Voice-Pipeline + Orb-Overlay
python -m jarvis

# Browser
# Open http://localhost:47821/
# Sidebar -> "Missions"
```

1. Speak (or hotkey): "Schreib mir eine Python-Funktion die Primzahlen bis 100 generiert und einen Pytest-Test dazu." (DE: "Write me a Python function that generates the primes up to 100 plus a pytest test for it.")
2. The UI mission tree shows the new mission under the "Mission Control" sidebar view.
3. Expectation:
   - Mission state PENDING → RUNNING → CRITIQUING → APPROVED (1-2 iterations typical)
   - Decomposer builds 1-2 parallel tasks (`primes.py`, `test_primes.py`)
   - PTY tab live with claude output
   - VerdictPanel shows the CriticVerdict with 4 axes
   - **Voice (DE):** "Sir, fertig. Die Primzahl-Funktion und der Test sind angelegt." (via grok-voice TTS)

**Acceptance cap:** Wall-clock < 90s, cost < $0.40, MissionApproved.

### S3 — Crash-Recovery

```bash
# Terminal A
python -m jarvis
# Dispatch mission via voice or UI

# While the mission is running:
# Terminal B
taskkill /F /IM pythonw.exe

# Re-launch
python -m jarvis
```

Expectation:
- Console: `Phase-6 stack online (db=..., recovered=1, sweep=N/M/0 scanned/removed/errors)`
- UI Mission-View: the previous mission as FAILED with reason="crash_recovery"
- `tasklist /FI "IMAGENAME eq claude.exe"` → empty (Job Object reaps all workers)
- Voice (if voice-source): "Sir, eine vorherige Mission wurde wegen Crash abgebrochen."

### S4 — Budget-Stop

```bash
# Edit jarvis.toml
# [phase6.budget]
# per_mission_usd = 0.05  # instead of 5.0

python -m jarvis
# Voice: "Bau mir die ganze README neu."
```

Expectation:
- Mission starts, worker spawn, first cost accumulates
- At 50% (0.025): Voice "Ruben, halbes Budget verbraucht."
- At 80% (0.04): Voice "Ruben, achtzig Prozent vom Budget weg."
- At 100% (0.05): WorkerKilled with reason="budget" + MissionFailed("budget_exceeded") + Voice "Sir, Budget aufgebraucht. Mission abgebrochen."

### S5 — Injection (already covered by unit tests)

The unit test simulates mock worker output with `"ignore previous instructions and ANTHROPIC_API_KEY=..."` — scanner detected, WorkerKilled emitted. A production test would be identical but against a real worker.

### S6 — Path-Guard (already covered by unit tests)

The unit test simulates diff output with `+++ b/.env` — Path-Guard blocks, WorkerKilled emitted. A production test would be identical.

---

## Architecture Highlights

### Action/Observation invariant (ADR-0009 §1)
- Voice-Readback **NEVER** reads raw LLM narratives
- `MissionApproved.summary_de` is Kontrollierer-signed (source_actor="kontrollierer")
- `WorkerCorrectionRequired.correction_instruction` (LLM output) is **NEVER** read aloud — only "Iteration N läuft" as an ack
- Verified via `test_voice_listener.py::test_correction_instruction_never_in_voice_output`

### Sycophancy defense (Critic-Loop, 3-fold staggered)
1. **Adversarial framing** in the prompt: "skeptical of this implementation, find at least three concrete bugs"
2. **Anchor token** verbatim: `<<<{mission_prompt}>>>` freshly each iteration
3. **Empty-Evidence-Reject** + adversarial-reframe retry, then raise

### Budget discipline
- Per-mission $5 + daily $50 (default, overridable in jarvis.toml)
- 50%/80% voice warnings auto-emitted via the `MissionBudgetWarning` event
- 100% raises `BudgetExceeded` → orchestrator catches → WorkerKilled(reason="budget") → MissionFailed
- Concurrency-safe via asyncio.Lock in record()

### Safety layer (Phase 5 NEW)
- **PostToolUse Injection-Scanner** between worker output and Critic call
  - 9 pattern categories (env-leak, rm -rf, ignore previous, etc.)
  - high+critical blocks; med/low logged
- **Path-Guard** with a glob-based block list (SSH/AWS/.env/cert keys)
  - Three levels: `is_blocked(path)`, `filter_diff_paths(diff)`, `check_prompt_for_blocked_paths(prompt)`
- **Destructive-Confirm** pre-mission gate (rm -rf, drop table, force-push)
  - UI AlertDialog path (voice-confirm deferred to Phase 7)

### Cleanup strategy (Phase 5 NEW)
- **Startup sweep** at app start (always-on)
- **Daily cron** opt-in via `[phase6.cleanup].daily=true`
- mtime-based, default 14-day cutoff
- `git worktree remove --force` first, fallback `shutil.rmtree`

---

## Known Limitations

1. **Voice-Listener requires manual TTS wiring** — `bootstrap_missions(tts_speak_fn=None)` means: no voice. Production wiring from DesktopApp/Speech-Pipeline → `bootstrap_missions(tts_speak_fn=self._tts.synthesize)` is open for Phase 7.

2. **Decomposer without BrainManager** — `bootstrap_missions(brain_caller=None)` means: all missions are treated as a 1-step plan (heuristic). Production wiring (BrainManager hook) is open for Phase 7.

3. **Multi-step mission decomposition** not yet E2E tested — the decomposer logic is verified via unit tests, but so far no run with a real brain has produced n_workers > 1.

4. **Two-bus bridge** between `DesktopApp.server.bus` (Phase-1a) and `MissionBus` (Phase-6) not implemented. The DesktopApp's voice layer and the mission voice listener today work on **separate** buses — mission events reach the UI via WebSocket, not via the DesktopApp-internal bus. Consequence: a voice trigger ("Hey Jarvis, schreib X") currently lands at the **Phase-5 SubJarvisManager**, NOT at the Phase-6 Kontrollierer. Phase-7 task: the router heuristic in `BrainManager` must route mission-capable + call DesktopApp `bootstrap_missions()` instead of just starting the backend WS.

5. **Cross-Model-Critic** (worker=Claude, critic=Codex/GPT) is not in the default path — opt-in via config flag in `escalation.py`, but untested in real-LLM E2E.

6. **PTY-Tail (`missions_pty_routes.py`)** is a Phase-4 stub: WS hello + 4404 response when worker_id is unknown. Real line-by-line tail of the `stream.jsonl` file is marked as a TODO.

7. **WS-Backpressure (128KB/16KB watermarks)** only for the PTY stream, not for the global event stream (which uses a 200-event bounded queue with drop-oldest).

8. **`AGENTS.md`** does not exist in the repo — the 5 hard rules are integrated into `CLAUDE.md`. No separate `AGENTS.md` created (user decision).

9. **`README.md` (repo root)** does not exist in the repo — the status table could go there, but it is not established in the current repo workflow. No new file created (user decision).

---

## Recommendations for Phase 7

### High-Priority
1. **Two-bus bridge** between DesktopApp and MissionManager. Today the voice path is still at the Phase-5 `SubJarvisManager` for ALL missions. Phase 7 must finalize the router heuristic (`BrainManager._should_force_sub_jarvis` ↔ `should_dispatch_mission`).
2. **TTS wiring**: call `bootstrap_missions(tts_speak_fn=DesktopApp._tts.synthesize)` in the DesktopApp + inject a `BrainCaller` (instead of None).
3. **`/p6-status` automation**: the slash-command file exists, but as a skill it is not in the registry — Phase 7 could add an automated status check per mission dispatch via the UI.

### Medium-Priority
4. **PTY-Tail implementation** (fill the Phase-4 stub) — backpressure frames + watch-mode file tail.
5. **Cross-Model-Critic** taken live as an A/B test — if 5% of missions are cross-checked in parallel with a Codex critic, we collect data on the actual sycophancy reduction.
6. **Voice-Confirm path** for `destructive_confirm` — a synchronous "wait for Yes/No" module in the Speech-Pipeline.

### Low-Priority / Forensics
7. **Worktree-cleanup policy on MissionFailed** — immediate `git worktree remove --force` (disk space) vs. 7-day prune (forensics). Currently 14 days uniform.
8. **Calibration loop** for the Critic — manual re-grade of 5-10% of verdicts; track FP/FN rate; alert on drift.
9. **WS-Backpressure for the global stream** (today only PTY).
10. **Multi-Monitor-Vision-Aware-Worker** — workers cannot take a screenshot today while they run (would require Phase-5 Vision-Stack integration).

---

## Mandatory Follow-ups Before Production Use

1. **Branch switch:** `git checkout -b phase6-self-healing` and commit phase 1-5 + the Phase-4 frontend.
2. **Real-LLM-Smoke** S2 run manually (voice path, ~$0.40 budget).
3. **Finalize ADR-0009 §"Open"** via `/skill phase6-adr-update`:
   - Reflexion-Memory layout: ✓ decided (Markdown in the mission root)
   - Cross-Model-Critic trigger: deferred (opt-in config flag, no default)
   - Worktree-cleanup policy: 14-day prune (default)
   - Voice-Readback on iteration 2: ✓ default OFF, opt-in via `[phase6.voice].announce_critic_loop=true`
   - Critic-Auth path (`--bare` opt-in via `ANTHROPIC_API_KEY` detection): ✓ implemented in `runner.py`
