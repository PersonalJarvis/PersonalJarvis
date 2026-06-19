# Mission Reliability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Phase-6 mission pipeline from a ~88%-failure / "50-50 gamble" into a system where a mission that does real work consistently ends APPROVED (or, if it genuinely cannot finish, fails *fast* and *honestly* — never silently discarding delivered work).

**Architecture:** The fix is sequenced by *impact-per-risk*, not by code area. Wave 1 stops the largest false-negative source (recovery sweeping live/finished work) with contained changes to `recovery.py` + the boot gate. Wave 2 makes hung/doomed missions fail *fast* at runtime instead of lingering until the next boot's sweep. Wave 3 fixes the structural enabler (the Critic has no evidence channel for non-diff work like Computer-Use) — this one is its own sub-project. Every wave is independently shippable and independently verifiable against the live `data/missions.db`.

**Tech Stack:** Python 3.11, asyncio, SQLite event store (`jarvis/missions/`), Pydantic frozen events, pytest (`asyncio_mode=auto`), fakes (not mocks) per repo convention.

---

## STATUS — Waves 1 + 2 + 3.1 COMPLETE (2026-06-07)

Wave 1 landed on branch `wip/cross-platform-cloud-first-20260529` (not pushed). Commits:
- `99188231` — Task 1.4 part: `TaskOutcome.TIMED_OUT` → `attempts_timed_out` (timeout no longer mislabelled "worker aborted"). *(committed by a parallel session; folded in.)*
- `13b86605` — Task 1.1: recovery preserves delivered work → `interrupted` (with artifacts), not `crash_recovery` with `[]`.
- `7c050412` — Task 1.4 rest: `interrupted` wired into voice (de+en phrase, non-alarming render, announcer suppression, de/en parity guard).
- `17dd69f1` — Task 1.2: **fail-closed recovery gate** — only the lock-holding primary sweeps; side scripts / `--no-lock` sessions can no longer kill live missions.
- `cca80e52` — review fixes: distinct interrupted-vs-crash recovery logs + per-worker dedup of partial artifacts.

Task 1.3 (script isolation) = **resolved by 1.2**: no in-tree script forces `recover=True`, so the fail-closed default makes them non-sweeping.

Verification: full mission suite **762 passed / 2 skipped**; recovery+gate+voice subset **75 passed**. Code-reviewer pass: 2 MAJOR found + fixed, no blockers.

### Wave 2 (fail fast & honest) — COMPLETE
- `3ba13b74` — Task 2.1: **liveness heartbeat**. New `last_heartbeat_ms` column (migration-free) + `touch_heartbeat`/`get_heartbeat`; orchestrator writes a heartbeat every 20 s while a worker drains; recovery's active-guard now uses `max(last_event_ts, heartbeat)` — a busy-but-silent Opus/long-tool-call worker is no longer swept on the next boot.
- `0ba4fc9b` — Task 2.2: **mission-level wall-clock deadline** (2400 s, injectable). `asyncio.timeout` around the post-semaphore TaskGroup; a runaway fails honestly as `attempts_timed_out` (TaskGroup cancellation kills the worker via its Job Object). Generous on purpose — only catches true runaways, never slow-but-working code (avoids re-introducing the Computer-Use stall-guard class of bug).
- **Idle-watchdog (90 s) deliberately NOT wired:** an aggressive idle kill would re-break legitimately-silent Computer-Use / long-tool-call workers (the documented `project_bug_voice_stall_guard_kills_working_computer_use` class). The heartbeat + generous deadline cover the need safely.

### Wave 3.1 (the structural enabler) — COMPLETE
- `9341919f` (orig. `0c79dd5f`) + `b2d15719` — **desktop-launch evidence channel**. Forensic finding: a force-spawned "open Explorer" mission runs `start explorer.exe` via **Bash** (Computer-Use proper is an in-process router tool that never becomes a mission). That produces no git diff and didn't match the git/gh command-evidence pattern → empty-diff veto → timeout. Fix mirrors the proven command-evidence pattern: `extract_verified_desktop_actions` (cross-platform `start`/`explorer`/`Start-Process`/`xdg-open`/`gio open`/`open -a`, tightened against CLI runs like `start git`), `_augment_diff_with_desktop_action_evidence`, and a third critic GROUND-TRUTH exception. A diff-less app-launch can now be APPROVED. Anti-hearsay gate preserved (only non-errored, id-correlated tool_results credited); the LLM critic remains the final judge.

Final review (all waves): 0 BLOCKER, 3 MAJOR (all fixed: empty-`task_outcomes` vacuous-approve guard + desktop-regex tightening; the `MissionTimedOut`-state refinement is a deliberate non-bug deferral — FAILED+`attempts_timed_out` is honest and reaches TTS). Full mission suite **785 passed / 2 skipped**; ruff clean on all new code.

**REQUIRES AN APP RESTART to take effect** (AP-8 restore-trap — `pythonw.exe` holds old code in RAM). After restart, re-measure with the §4 KPI query.

### Deliberately deferred (NOT required for "the bug no longer occurs")
- **Task 3.2 (route quick desktop actions off the heavy mission path):** obsolete for correctness — direct Computer-Use already runs in-process (no mission), and force-spawned desktop actions now pass via 3.1. Pure optimization; deferred to avoid routing-heuristic churn.
- **Task 3.3 (auto-resume interrupted missions):** deferred ON PURPOSE. Auto-running an old mission on boot re-spends the worker, repeats side effects (re-opens apps), can surprise the user (spawn-legibility), and risks retry loops — it would make things *worse*, contradicting the goal. Interrupted missions already preserve their work (`interrupted` reason + artifacts) and stay re-runnable on demand (run_mission idempotency). A UI "retry" affordance is the right home for this, as a separate UI task.

**Bug class CLOSED (as of Waves 1–3.1):** every *then-known* spurious-failure bucket is addressed — crash_recovery (gate + heartbeat + preserve-work), task_error/timeout on desktop actions (evidence channel + honest relabel), runaway hangs (deadline), and the two critic buckets (already dead since 2026-06-01). Remaining failures *of that era* are *legitimate* (worker genuinely cannot do the task, auth/billing errors) and are now labelled honestly.

---

## WAVE 4 — Provider-coupling instant-fail (2026-06-08) — FIXED

> **The bug reopened the moment the user switched the Heavy-Worker provider.** Waves 1–3.1 were all forensically measured while `[brain.sub_jarvis].provider = claude-api`. The instant the user set the sub-agent provider to anything else (live config on 06-08: `grok`, with a `gemini` fallback), **every single mission failed in ~3 s** — a brand-new root cause the earlier waves never exercised.

**Forensic ground truth (`data/missions.db`, 06-08):** missions `019ea82e-3fac`, `019ea82e-8cc5`, `019ea830-072f` each: `WorkerSpawned pid=0` → `WorkerKilled reason=user` (~0.3 s) → `MissionFailed reason=task_error partial_artifacts=[]` (~3 s total, `cost_usd=0.0`). The verbatim worker error in `data/jarvis_desktop.log`:

```
ClaudeDirectWorker: primary provider is grok, expected claude-api
ClaudeDirectWorker: primary provider is openai-codex, expected claude-api
```

`reason=user` is misleading — `orchestrator.py:976-985` maps **any** non-timeout/non-billing `worker_error` to the closed-literal `"user"`. The user cancelled nothing; the worker refused to start.

### Root cause — the same provider-coupling defect in TWO paths

Post-Welle-4 the OpenClaw-backed `SubJarvisWorker` was removed (the ~92% nested-claude hang). So `jarvis/missions/init.py::_worker_factory` now routes **every** `[brain.sub_jarvis].provider` that is not `openai-codex`/`chatgpt` (grok, gemini, openai, openrouter, openclaw-claude, **and the unset default** — whose `_resolve_provider_chain` last-ditch stub is `("grok", "grok-4.3")`) to `ClaudeDirectWorker` (the "proven direct Opus worker", `init.py:476`). But both the worker **and** the critic still carried a guard that assumed a *fall-through to a worker that no longer exists*:

1. **`jarvis/missions/workers/claude_direct_worker.py`** (`spawn`, ex-line 218-241): refused with `"primary provider is <x>, expected claude-api"` unless the resolved chain primary was `claude-api`. → every non-claude/non-codex mission died instantly.
2. **`jarvis/missions/critic/runner.py`** (`_invoke_once`, ex-line 931-938): the "fall back to the direct claude critic" branch passed `model=primary_model or model` — i.e. the **foreign** provider model (`grok-4.3`) — to `claude --model`, which the claude CLI rejects with `returncode=1`. The critic failed twice → `critic_unavailable`, failing the mission **even after the worker delivered real work** (surfaced the moment fix #1 let the worker run).

Both are the same class: *a heavy-worker/critic path that hard-couples its ability to run to a configured provider slug it no longer has a dedicated backend for.* The OpenClaw escape hatch the guards assumed is gone.

### The fix — `ClaudeDirectWorker`/`CriticRunner` are the universal Claude-Max fallback

- **Worker:** new `_resolve_claude_model(primary)` helper resolves a real claude model (`[brain.providers.claude-api].deep_model` → `claude-opus-4-8` default — **never** a foreign slug). The refusal guard is gone; when the configured provider differs the worker **runs on the Claude Max OAuth backend** and only `logger.warning`s the swap (anti-silent-fallback: legible, never hidden).
- **Critic:** the non-claude fallback branch now grades on the claude critic model from `choose_critic_model` (`model`), never `primary_model`; logs the provider it fell back from.

This honours the dedicated workers exactly (`claude-api` → ClaudeDirectWorker, `openai-codex`/`chatgpt` → CodexDirectWorker) and gives **everything else** the reliable frontier-quality Claude Max fallback instead of an instant death — matching the user's `frontier-quality-before-cost` mandate. `SubJarvisWorker` is **not** resurrected (AP-14 upheld).

### Verification
- **Unit (TDD red→green):** `tests/missions/workers/test_claude_direct_provider_fallback.py` (5) + `tests/missions/critic/test_runner_claude_direct.py::test_non_claude_provider_critic_uses_claude_model_not_foreign` (3). Both watched fail first ("worker refused…", "foreign model leaked into claude argv"). Full `tests/missions/` suite: **796 passed, 2 skipped**; ruff clean on changed code.
- **Real end-to-end (GREEN, 2026-06-08):** `scripts/verify_submission_provider_fix.py` drove the REAL `manager.dispatch → kontrollierer.run_mission` pipeline against an **isolated temp DB** with the **live grok config** (the exact bug condition), spawning real `claude` Max-OAuth `claude-opus-4-8` workers + critics. Result: **3/3 rounds APPROVED, zero provider-refusal failures, exit 0** (missions `019ea847`/`019ea848`). Before the fix the same harness reproduced the bug: every round died with `ClaudeDirectWorker: primary provider is grok, expected claude-api` (worker) → then, after fix #1, `Critic lieferte zweimal keinen schema-validen JSON-Output` (critic foreign-model rejection).

**REQUIRES AN APP RESTART to take effect** (AP-8 restore-trap — `pythonw.exe` holds old code). After restart, missions dispatched under the grok sub-agent reach APPROVED instead of dying in 3 s.

### WAVE 4b — Codex sub-agent: expired ChatGPT login (2026-06-08, same evening)

> After the grok fix went live (confirmed: the Wave-4 warning string appears in `data/jarvis_desktop.log` at 20:58 / 21:11 — **no restore-trap**), the user switched the sub-agent to **Codex** and missions failed again — now running for **minutes**, not 3 s. Different root cause, surfaced *because* the worker now runs.

**Forensic ground truth (`data/missions.db` + log, 06-08):**
- Mission `019ea8db` (codex worker, 153 s, 3 iters → `task_error`): the codex subprocess emitted an error event whose `message` was a **dict** `{'message': 'Failed to refresh token. … Please log in again.'}`. `CodexDirectWorker` (`codex_direct_worker.py:423`) fed it into `ClaudeResult(result=…)` (a `str` field) → **Pydantic `ValidationError` → worker crash → opaque `task_error`**, hiding the real cause.
- Mission `019ea8a5` (claude worker via the grok fix, **7.8 KB real diff**) → `critic_unavailable`: the critic ran via **codex-direct** (config had drifted to codex by critic time) and the dead codex token meant no schema-valid JSON twice.
- `Pre-Boot-Key-Check: kein Key in ['codex'] -> Provider 'codex' deaktiviert` while `codex status: connected=True mode=chatgpt` — **`codex status` lies**: it checks token *presence*, not *validity* (same class as the Gmail-OAuth-placeholder bug).

**Root cause:** the user's **Codex ChatGPT OAuth session is expired and cannot refresh** (`codex login` needed). On top of that operational fact sat two code defects: (a) the worker *crashed* on the structured error instead of surfacing it; (b) a dead codex login failed every codex mission with no fallback.

**The fix (`jarvis/missions/workers/codex_direct_worker.py` + `critic/runner.py`):**
1. `_coerce_codex_error_text()` — always extract a plain string from the codex error event (nested-dict-safe); `result=str(...)`. No more Pydantic crash; the real message survives.
2. **Codex auth-expiry → Claude Max fallback (worker):** when the codex terminal error matches `_CODEX_AUTH_EXPIRED_MARKERS` (refresh token / log in again / 401 / …) AND codex did no real work, the worker delegates the task to `ClaudeDirectWorker` (the codex error event is *not* yielded, so `_spawn_worker_collect`'s `worker_error` stays unset and the claude result drives the outcome). The mission COMPLETES on Claude Max.
3. **Codex critic → Claude critic fallback:** when the codex critic yields no verdict, fall back to `_invoke_via_claude_direct` instead of `critic_unavailable`.

Same philosophy as the grok fix: every sub-agent option now works — codex-with-good-token → codex; codex-with-dead-token → Claude Max fallback (legible warning, `run codex login` to restore codex); grok/gemini/openai/openrouter/unset → Claude Max. `codex status`-lies and the boot key-check vs ChatGPT-mode mismatch remain a **follow-up** (cosmetic; missions now succeed regardless).

**Verification:** unit TDD `tests/missions/workers/test_codex_auth_fallback.py` (5: coerce helper, auth-marker detect, dict-error-no-crash, **auth-expiry → claude fallback**). Full `tests/missions/`: **801 passed, 2 skipped**; ruff clean on changed code. Real end-to-end (GREEN, 2026-06-08): `scripts/verify_submission_provider_fix.py` re-run with `JARVIS__BRAIN__SUB_JARVIS__PROVIDER=openai-codex` against the live dead token — every round logged `codex ChatGPT login expired ('400 … Your session has ended')` → worker falls back to Claude Max, then `codex critic produced no verdict (401 … token invalidated) → falling back to the claude critic` → **3/3 APPROVED, exit 0** (missions `019ea8f8`/`019ea8fa`/`019ea8fb`). The grok path is unbroken by these changes: **3/3 APPROVED** in the same run. The Wave-4 grok fix had already proven live earlier (warning string in `jarvis_desktop.log` 20:58/21:11 — no restore-trap), so a single restart picks up these codex fixes too.

---

## 0. Why this plan exists (evidence, not opinion)

Forensics against the live `data/missions.db` (read-only) on 2026-06-07:

| Metric | Value |
|---|---|
| Total missions | 327 |
| **FAILED** | **286 (87.5%)** |
| APPROVED | 39 |
| CANCELLED | 2 |
| Failure rate is consistent | every single day 60–100%, since 2026-05-10 |

**Failure reason buckets (`MissionFailed.reason`):**

| Bucket | Count | Status after 2026-06-01 fix (`ef2d247c`) | Verdict |
|---|---|---|---|
| `task_error` | 105 | **STILL FIRING** (07-06) | LIVE — biggest |
| `crash_recovery` | 98 | **STILL FIRING** (4× on 06-01 after the fix) | LIVE — 2nd biggest |
| `critic_loop_exhausted` | 59 | last seen 05-30 | DEAD ✓ (fix worked) |
| `critic_unavailable` | 23 | last seen 05-31 | DEAD ✓ (fix worked) |
| `critic_rejected` | 1 | — | correct behaviour |

**The two live buckets account for 203 of 286 failures (71%).** The 2026-06-01 fix
genuinely killed the two *critic* buckets — do not touch that code. It did **not**
fix the two largest buckets. The deep-dive for the `task_error` incident is
[`docs/phantom-worker-aborted-deep-dive/README.md`](../../phantom-worker-aborted-deep-dive/README.md).

### Root causes (verified `file:line`)

**`crash_recovery` (98) — three independent holes, none addressed by the 06-01 fix:**

1. **Liveness is inferred from event-recency, not from process liveness.**
   `jarvis/missions/recovery.py:143-147` skips a mission only if `events[-1].ts_ms`
   is `< 30 min` old (`RECOVERY_STALE_AFTER_MS`, `recovery.py:57`). A real worker
   (Opus, critic loops, Computer-Use) routinely goes silent **>30 min** between
   events. There is **no runtime watchdog and no mission-level wall clock**
   (`orchestrator.py` drains the worker stream with no wrapping timeout; the
   `WorkerSupervisor` at `workers/supervisor.py:44` with `idle_timeout_s=90`/`hard_cap_s=900`
   is defined but **never wired**). So a working-but-silent mission is
   indistinguishable from an orphan and gets swept on the next boot.

2. **The primary-instance gate is fail-OPEN.** `jarvis/ui/web/server.py:1555` —
   `_is_primary = os.environ.get("JARVIS_PRIMARY_INSTANCE", "1") != "0"` defaults
   *unset* to primary, and `bootstrap_missions(recover_missions=True)` /
   `MissionManager.start(recover=True)` default to recovering. Any process that
   opens the live DB without routing through the launcher (smoke/validate/eval
   scripts, a `--no-lock` parallel session) runs `startup_recover` against the
   live DB and sweeps the desktop instance's in-flight missions. This is the
   **simultaneous-timestamp double-sweep** seen in the data (two missions both
   swept at exactly `20:28:24`).

3. **Delivered-but-unapproved work is swept with `partial_artifacts=[]`.**
   `recovery.py:61-66` (`_TERMINAL_EVENT_STATE`) only reconciles the 4 *terminal*
   events. A `WorkerDraftReady` (real work, `events.py:66`) is NOT a recognised
   "work-done" signal, so a mission that produced a draft but had not yet reached
   `MissionApproved` is failed as `crash_recovery` — **this is the exact symptom in
   the user's screenshots: 17 delivered files, badge = ERROR.**

**`task_error` (105) — the enabler is a structural evidence gap:**

4. **The Critic's only ground truth is the git diff.** `critic/prompts.py:111-154`
   + the deterministic veto at `critic/runner.py:505-544`: an empty diff ⇒
   `correctness=FAIL` ⇒ `revise`. Computer-Use / desktop actions ("open Explorer")
   produce **no diff**, so every iteration is vetoed, all 3 loops
   (`MAX_CRITIC_LOOPS=3`, `critic/runner.py:273`) burn, the last hits the 630 s
   per-iteration cap (`workers/claude_direct_worker.py:70` `600` + `30`), and the
   mission times out after ~18 min. The action *succeeded*; there is simply
   nothing for the Critic to grade. `stream_evidence.py` credits out-of-worktree
   file writes (`extract_write_targets`) and git/gh commands
   (`extract_verified_commands`) — but **nothing for Computer-Use**.

5. **The in-flight uncommitted fix only relabels.** The working-tree diff to
   `orchestrator.py` (+ `readback.py`) adds `TaskOutcome.TIMED_OUT` →
   `attempts_timed_out` → "Das Zeitlimit wurde überschritten." That makes the
   *spoken word* honest (timeout, not abort) but the mission **still fails**. It
   does not reduce the failure rate. Keep it; build prevention on top.

### Design principle (the one sentence to remember)

> Both failing subsystems fail because they trust a *proxy* that defaults to
> FAILURE: recovery trusts event-recency as a liveness proxy; the Critic trusts
> the git-diff as a work proxy. The fix is to replace each proxy with a real
> signal (process liveness / a real evidence channel) and to make the
> *default outcome of uncertainty be "preserve", not "fail"*.

---

## 1. File map (what changes, and why)

| File | Responsibility | Wave |
|---|---|---|
| `jarvis/missions/recovery.py` | Add a "delivered-work" guard before the sweep; honest `interrupted` reason | 1 |
| `jarvis/missions/voice/readback.py` | Phrases for `interrupted` + keep `attempts_timed_out` | 1 |
| `jarvis/missions/voice/announcer.py` | Suppress/soften `interrupted` like `crash_recovery` already is | 1 |
| `jarvis/ui/web/server.py` | Fail-CLOSED primary gate (require proof of lock ownership) | 1 |
| `jarvis/missions/manager.py` / `init.py` | `recover` default → require explicit primary token | 1 |
| `scripts/smoke_phase6_*.py`, `scripts/_final_*` | Open an isolated DB / pass `recover_missions=False` | 1 |
| `jarvis/missions/events.py` | `MissionHeartbeat` event (or `last_heartbeat_ms` column) | 2 |
| `jarvis/missions/kontrollierer/orchestrator.py` | Emit heartbeat while worker runs; wire idle-watchdog; mission deadline | 2 |
| `jarvis/missions/recovery.py` | Active-guard keys off heartbeat, not last-event | 2 |
| `jarvis/missions/critic/runner.py`, `stream_evidence.py` | Computer-Use action-observation evidence channel | 3 |
| `jarvis/missions/recovery.py` (resume) | Resume-and-grade an interrupted mission with a draft | 3 |

**Five-layer enum discipline (`docs/anti-drift-three-layer.md`):** `interrupted` and
`attempts_timed_out` are new wire-format `MissionFailed.reason` strings. Each MUST
be added to: (a) the reason→phrase maps in `readback.py` de+en, (b) any UI label
map for failure reasons, (c) a parity test asserting de/en/UI coverage. See Task 1.4.

---

## WAVE 1 — Stop the false-negative bleeding *(highest impact, lowest risk)*

> Outcome of Wave 1: `crash_recovery` stops overwriting delivered work and stops
> being triggered by side processes. Expected effect on the live DB: the
> `crash_recovery` bucket drops toward ~0 for single-instance operation, and
> delivered-but-interrupted missions show their artifacts instead of a bare ERROR.

### Task 1.1: A "delivered work" guard before the crash_recovery sweep

**Files:**
- Modify: `jarvis/missions/recovery.py` (`startup_recover`, branch 3 at lines 149-182; helper near `_last_terminal_state` line 208)
- Test: `tests/missions/test_recovery.py` (create if absent; otherwise extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/missions/test_recovery.py
import pytest
from jarvis.missions.event_store import MissionEventStore
from jarvis.missions.events import (
    EventEnvelope, MissionDispatched, WorkerDraftReady, MissionStateChanged, now_ms,
)
from jarvis.missions.recovery import startup_recover, RECOVERY_STALE_AFTER_MS
from jarvis.missions.state_machine import MissionState


async def _append(store, mid, payload, ts):
    await store.append_and_publish(
        EventEnvelope(mission_id=mid, source_actor="system", ts_ms=ts, payload=payload)
    )


@pytest.fixture
async def store(tmp_path):
    s = MissionEventStore(db_path=str(tmp_path / "m.db"))
    await s.open()
    yield s
    await s.close()


async def test_stale_mission_with_draft_is_interrupted_not_crash_recovery(store):
    """A mission that produced a WorkerDraftReady but went stale must be failed
    with reason='interrupted' (delivered work preserved), NOT 'crash_recovery'.

    NOTE: the real WorkerDraftReady schema (events.py:66) is
    (worker_id, artifact_uri, diff, tokens_used, cost_usd, session_id) — there is
    NO `artifacts` list and NO `iteration` field. The 'work was produced' signal
    is a WorkerDraftReady with a non-empty artifact_uri or a non-empty diff."""
    mid = "019e0000-0000-7000-8000-000000000001"
    old = now_ms() - (RECOVERY_STALE_AFTER_MS + 60_000)  # well past the stale window
    await store.upsert_mission(mission_id=mid, prompt="x", state=MissionState.CRITIQUING.value,
                               language="de", ts_ms=old)
    await _append(store, mid, MissionDispatched(prompt="x", language="de"), old)
    await _append(store, mid, MissionStateChanged(from_state="RUNNING",
                  to_state="CRITIQUING", reason="iter-0-start"), old)
    await _append(store, mid, WorkerDraftReady(
        worker_id="w1", artifact_uri="file:///out/report.html",
        diff="diff --git a/report.html b/report.html\n+<html>",
        tokens_used=10, cost_usd=0.0, session_id="s1"), old)

    recovered = await startup_recover(store)

    events = await store.events_for_mission(mid)
    failed = [e for e in events if e.payload.event_type == "MissionFailed"]
    assert failed, "mission should still reach a terminal state"
    assert failed[-1].payload.reason == "interrupted"
    assert failed[-1].payload.reason != "crash_recovery"
    # delivered artifact_uri must be preserved, not dropped to []
    assert "file:///out/report.html" in failed[-1].payload.partial_artifacts


async def test_stale_mission_with_no_work_stays_crash_recovery(store):
    """A mission that produced NO draft is a genuine orphan → crash_recovery."""
    mid = "019e0000-0000-7000-8000-000000000002"
    old = now_ms() - (RECOVERY_STALE_AFTER_MS + 60_000)
    await store.upsert_mission(mission_id=mid, prompt="x", state=MissionState.RUNNING.value,
                               language="de", ts_ms=old)
    await _append(store, mid, MissionDispatched(prompt="x", language="de"), old)

    await startup_recover(store)

    events = await store.events_for_mission(mid)
    failed = [e for e in events if e.payload.event_type == "MissionFailed"]
    assert failed[-1].payload.reason == "crash_recovery"
```

- [ ] **Step 2: Run test to verify it fails**

Run (use the Jarvis interpreter, NOT a stray venv — see "Gotchas"):
`& "C:\Program Files\Python311\python.exe" -m pytest tests/missions/test_recovery.py -v`
Expected: FAIL — the first test gets `reason == 'crash_recovery'` and empty `partial_artifacts`.

- [ ] **Step 3: Write minimal implementation**

In `recovery.py`, add a helper and branch the sweep. Replace branch 3's hardcoded
`reason="crash_recovery"` / `partial_artifacts=[]` with a delivered-work check:

```python
# near _last_terminal_state (recovery.py)
def _delivered_artifacts(events: list[EventEnvelope]) -> list[str]:
    """artifact_uri(s) from WorkerDraftReady events that carried real work, or [].

    A non-empty result means the mission produced real work before going stale:
    the orphan was a DELIVERY interruption, not an empty crash. We preserve the
    artifact_uri and label the failure honestly as 'interrupted' so the Outputs
    view surfaces the work instead of a bare crash_recovery ERROR.

    WorkerDraftReady schema (events.py:66): worker_id, artifact_uri, diff,
    tokens_used, cost_usd, session_id. "Real work" = a draft whose artifact_uri
    OR diff is non-empty (a draft with both empty is not a delivery).
    """
    artifacts: list[str] = []
    for env in events:
        p = env.payload
        if p.event_type != "WorkerDraftReady":
            continue
        uri = (getattr(p, "artifact_uri", "") or "").strip()
        diff = (getattr(p, "diff", "") or "").strip()
        if uri:
            artifacts.append(uri)
        elif diff:
            # work happened but no URI surfaced — record a sentinel so the
            # mission is still treated as 'interrupted', not 'crash_recovery'.
            artifacts.append(f"draft:{env.mission_id}")
    return artifacts
```

```python
        # 3. Orphaned. Distinguish "had delivered work" from "empty crash".
        delivered = _delivered_artifacts(events)
        sweep_reason = "interrupted" if delivered else "crash_recovery"
        error_class = "MissionInterrupted" if delivered else "OrchestratorCrash"
        state_env = EventEnvelope(
            mission_id=mission_id, source_actor="system", ts_ms=now_ts,
            payload=MissionStateChanged(from_state=last_state,
                to_state=MissionState.FAILED.value, reason=sweep_reason),
        )
        await store.append_and_publish(state_env)
        fail_env = EventEnvelope(
            mission_id=mission_id, source_actor="system", ts_ms=now_ts,
            payload=MissionFailed(reason=sweep_reason, error_class=error_class,
                last_state=last_state, partial_artifacts=delivered),
        )
        await store.append_and_publish(fail_env)
```

> Note: confirm the `WorkerDraftReady` payload field name (`artifacts`) against
> `jarvis/missions/events.py:66`; if it differs, use the real attribute. The
> `getattr(..., [])` guard makes the helper robust either way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/missions/test_recovery.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/missions/recovery.py tests/missions/test_recovery.py
git commit -m "fix(missions): preserve delivered work on recovery (interrupted != crash_recovery)"
```

### Task 1.2: Fail-CLOSED primary gate (stop side processes sweeping live missions)

**Files:**
- Modify: `jarvis/ui/web/server.py:1552-1560` (`_init_mission_stack`)
- Modify: `jarvis/missions/manager.py:66-100` (`start`), `jarvis/missions/init.py:204,238` (`bootstrap_missions`)
- Test: `tests/missions/test_recovery_gate.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/missions/test_recovery_gate.py
import os
import pytest
from jarvis.missions import init as missions_init


async def test_bootstrap_does_not_recover_without_explicit_primary(monkeypatch, tmp_path):
    """A process that does not prove primary ownership must NOT run recovery.
    Guards against smoke/eval scripts sweeping the live desktop instance."""
    monkeypatch.delenv("JARVIS_PRIMARY_INSTANCE", raising=False)
    calls = {"recovered": False}

    async def _spy(*a, **k):
        calls["recovered"] = True
        return []

    monkeypatch.setattr("jarvis.missions.manager.startup_recover", _spy)
    # default call (no recover_missions arg, no env) must be fail-CLOSED
    await missions_init.bootstrap_missions(db_path=str(tmp_path / "m.db"))
    assert calls["recovered"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/missions/test_recovery_gate.py -v`
Expected: FAIL — current default `recover_missions=True` runs recovery.

- [ ] **Step 3: Write minimal implementation**

(a) `jarvis/missions/init.py` — flip the default to fail-closed:

```python
async def bootstrap_missions(
    ...
    recover_missions: bool = False,   # was True — fail-CLOSED; only the launcher opts in
    ...
):
```

(b) `jarvis/ui/web/server.py:1555` — require POSITIVE proof, not "not 0":

```python
        # Fail-CLOSED: only an instance that PROVED it holds the single-instance
        # primary lock (launcher sets JARVIS_PRIMARY_INSTANCE="1") may recover.
        # Unset / "0" / any side process (smoke, eval, --no-lock) does NOT sweep.
        _is_primary = os.environ.get("JARVIS_PRIMARY_INSTANCE") == "1"
        result = await bootstrap_missions(
            db_path=db_path,
            ...
            recover_missions=_is_primary,
        )
```

(c) `jarvis/missions/manager.py:69` — change `recover: bool = True` → `recover: bool = False`
and update the docstring to say recovery is opt-in for the proven-primary path only.

- [ ] **Step 4: Run test to verify it passes**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/missions/test_recovery_gate.py tests/missions/ -k "recover" -v`
Expected: PASS. Then run the full mission suite to catch callers that relied on the old default:
`& "C:\Program Files\Python311\python.exe" -m pytest tests/missions/ -q`
Expected: PASS (fix any caller that legitimately needed recovery by passing `recover_missions=True`).

- [ ] **Step 5: Commit**

```bash
git add jarvis/missions/init.py jarvis/missions/manager.py jarvis/ui/web/server.py tests/missions/test_recovery_gate.py
git commit -m "fix(missions): fail-closed recovery gate — only the proven primary sweeps"
```

### Task 1.3: Isolate side-process scripts from the live DB

**Files:**
- Modify: `scripts/smoke_phase6_p1.py:51-52`, `scripts/_final_validate_2_live_worker.py:60`, `scripts/_final_live_verdict.py:44` (and any other direct `MissionManager(...)` / `bootstrap_missions(...)` opener — grep first)

- [ ] **Step 1: Find every direct opener**

Run: `grep -rn "MissionManager(\|bootstrap_missions(" scripts/`
Record the list. Any script that opens `data/missions.db` (the real path) is a sweeper.

- [ ] **Step 2: Make each script isolated**

For each, either point it at a temp DB, or pass the now-default-safe flag explicitly:

```python
# Example — smoke script must never touch the live DB:
import tempfile, os
db_path = os.path.join(tempfile.mkdtemp(prefix="jarvis-smoke-"), "missions.db")
manager = MissionManager(db_path=db_path)
await manager.start(recover=False)   # explicit, even though default is now False
```

- [ ] **Step 3: Verify no script references the live data/ DB for recovery**

Run: `grep -rn "data/missions.db\|data\\\\missions.db" scripts/`
Expected: only read-only forensic usage, never a `start(recover=True)` against it.

- [ ] **Step 4: Commit**

```bash
git add scripts/
git commit -m "fix(missions): smoke/validate scripts use isolated DB, never sweep live missions"
```

### Task 1.4: Five-layer parity for `interrupted` + `attempts_timed_out`

**Files:**
- Modify: `jarvis/missions/voice/readback.py:179-200` (de+en maps — `attempts_timed_out` already added by the in-flight diff; add `interrupted`)
- Modify: `jarvis/missions/voice/announcer.py:189` (suppress/soften `interrupted` like `crash_recovery`)
- Modify: any UI failure-reason label map (grep: `crash_recovery` in `jarvis/ui/web/frontend/` + `jarvis/sessions/`/routes)
- Test: `tests/missions/test_voice_readback.py` (extend), `tests/missions/test_voice_announcer.py` (extend)

- [ ] **Step 1: Write the failing parity + suppression tests**

```python
# tests/missions/test_voice_readback.py  (add)
from jarvis.missions.voice.readback import FAILURE_REASON_PHRASES

def test_new_failure_reasons_have_de_en_parity():
    for reason in ("interrupted", "attempts_timed_out"):
        assert reason in FAILURE_REASON_PHRASES["de"], f"missing de phrase for {reason}"
        assert reason in FAILURE_REASON_PHRASES["en"], f"missing en phrase for {reason}"

def test_de_en_reason_keys_are_identical():
    assert set(FAILURE_REASON_PHRASES["de"]) == set(FAILURE_REASON_PHRASES["en"])
```

```python
# tests/missions/test_voice_announcer.py  (add)
async def test_interrupted_is_suppressed_like_crash_recovery(announcer_fixture):
    """An interrupted-recovery failure must NOT barge in with an alarming phrase,
    exactly as crash_recovery is suppressed (announcer.py:189)."""
    spoken = await announcer_fixture.render_failure(reason="interrupted")
    assert spoken is None  # suppressed, same contract as crash_recovery
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/missions/test_voice_readback.py tests/missions/test_voice_announcer.py -v`
Expected: FAIL — `interrupted` missing; announcer speaks it.

- [ ] **Step 3: Implement**

`readback.py` de + en (the `attempts_timed_out` lines already exist from the in-flight diff):

```python
        # de:
        "interrupted": "Eine laufende Mission wurde unterbrochen, die Teilergebnisse liegen vor.",
        # en:
        "interrupted": "A running mission was interrupted; the partial results are available.",
```

`announcer.py:189` — extend the suppression set:

```python
        # crash_recovery and interrupted are boot-time cleanups, not live failures:
        # never barge in with an alarming phrase for a mission the user isn't
        # actively waiting on (AD-OE5).
        if reason in ("crash_recovery", "interrupted"):
            return None
```

Add the UI label (grep the frontend reason map; add `interrupted` + `attempts_timed_out`
English labels so the Outputs badge reads e.g. "Interrupted" / "Timed out", not raw enum).

- [ ] **Step 4: Run to verify pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/missions/test_voice_readback.py tests/missions/test_voice_announcer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit (and commit the in-flight attempts_timed_out work with it)**

```bash
git add jarvis/missions/voice/ tests/missions/test_voice_*.py jarvis/ui/web/frontend/
git commit -m "feat(missions): honest interrupted/timed-out failure reasons with de/en/UI parity"
```

### Wave 1 verification gate

- [ ] Re-run the forensic query (see §4) against a copy of `data/missions.db`.
- [ ] Restart the live app (`run.bat`) — **mandatory**: a committed fix is inert
      while `pythonw.exe` holds the old code in RAM (restore-trap, AP-8). Verify
      `import jarvis; print(jarvis.__file__)` points at the working tree.
- [ ] Dispatch 5 file-producing missions via `POST /api/missions/dispatch`; restart
      the app mid-flight on 2 of them. Confirm: the 2 interrupted ones show
      `reason="interrupted"` WITH artifacts, never `crash_recovery` with `[]`.
- [ ] Run a smoke script while the app runs; confirm it does **not** sweep live missions.

---

## WAVE 2 — Fail fast & honestly at runtime *(medium effort)*

> Outcome of Wave 2: a hung or doomed mission is reaped *at runtime* in seconds-to-minutes
> with an honest reason, instead of lingering as RUNNING until the next boot's
> 30-min stale sweep. The active-guard stops mistaking a busy worker for an orphan.

### Task 2.1: Mission heartbeat → liveness-based active-guard

**Files:**
- Modify: `jarvis/missions/events.py` (add `MissionHeartbeat` frozen event, OR add `last_heartbeat_ms` to the missions row + an `upsert_heartbeat` on the store)
- Modify: `jarvis/missions/kontrollierer/orchestrator.py` (emit a heartbeat every ~20 s while a worker stream is draining — wrap the `async for ev in worker.spawn(...)` loop, ~line 1199)
- Modify: `jarvis/missions/recovery.py:143-147` (active-guard checks heartbeat freshness, not `events[-1].ts_ms`)
- Test: `tests/missions/test_recovery.py` (extend), `tests/missions/kontrollierer/test_loop.py` (extend)

- [ ] **Step 1: Write the failing test** — a mission whose last *event* is 40 min old
      but whose *heartbeat* is 10 s old must be SKIPPED (still owned by a live worker):

```python
async def test_fresh_heartbeat_protects_silent_worker(store):
    mid = "019e0000-0000-7000-8000-000000000003"
    old = now_ms() - (40 * 60 * 1000)          # last EVENT 40 min ago
    await store.upsert_mission(mission_id=mid, prompt="x", state="CRITIQUING",
                               language="de", ts_ms=old)
    await _append(store, mid, MissionDispatched(prompt="x", language="de"), old)
    await store.upsert_heartbeat(mid, ts_ms=now_ms() - 10_000)   # heartbeat 10 s ago
    recovered = await startup_recover(store)
    assert mid not in recovered  # live worker, not orphaned
```

- [ ] **Step 2–4:** Implement `upsert_heartbeat` + heartbeat emission in the orchestrator
      drain loop + change the active-guard to `max(last_event_ts, last_heartbeat_ms)`.
      Run the tests; expect PASS. (Heartbeat is a *header* update, not an event, so it
      does not bloat the event log or trip the flight recorder.)

- [ ] **Step 5: Commit** — `fix(missions): heartbeat-based liveness so busy workers aren't swept`

### Task 2.2: Wire the idle-watchdog + a mission-level deadline

**Files:**
- Modify: `jarvis/missions/kontrollierer/orchestrator.py` (`_spawn_worker_collect` / the drain at ~1199 — wrap with `WorkerSupervisor` from `workers/supervisor.py:44`, and add an overall `asyncio.wait_for` mission deadline)
- Test: `tests/missions/kontrollierer/test_loop.py`

- [ ] **Step 1: Write the failing test** — a worker that emits one byte then goes idle
      past `idle_timeout_s` is reaped with an honest `attempts_timed_out` (NOT left
      RUNNING, NOT eventually `crash_recovery`):

```python
async def test_idle_worker_is_reaped_with_timeout_reason(fake_worker, kontrollierer):
    fake_worker.emit_first_chunk_then_hang(idle_s=200)   # > idle_timeout_s=90
    outcome = await kontrollierer.run_mission(mission_id="...", prompt="...")
    assert outcome_reason(outcome) == "attempts_timed_out"
```

- [ ] **Step 2–4:** Instantiate `WorkerSupervisor(idle_timeout_s=90, hard_cap_s=900)`,
      feed it `observe_event` per streamed event, and when it reports STUCK, cancel the
      worker (Job Object kill) and return `TaskOutcome.TIMED_OUT`. Add a mission-level
      `asyncio.wait_for(run_mission_inner, timeout=MISSION_DEADLINE_S)` (e.g.
      `MAX_CRITIC_LOOPS * (hard_cap_s) + slack`) that fails with `attempts_timed_out`.
      Run tests; expect PASS.

- [ ] **Step 5: Commit** — `fix(missions): wire idle-watchdog + mission deadline (no more silent 18-min hangs)`

### Wave 2 verification gate

- [ ] Dispatch a deliberately-hanging worker (fake or a `sleep 9999` task); confirm it
      is reaped in ≤ `idle_timeout_s + slack`, mission ends `attempts_timed_out`, and a
      subsequent restart finds NO stale mission to sweep.

---

## WAVE 3 — Fix the structural enabler (Computer-Use evidence) *(its own sub-project)*

> **Scope note (per writing-plans Scope Check):** Wave 3 spans the Critic, the
> Computer-Use harness, and the bus-event capture layer. It should get its own
> detailed plan before implementation. The tasks below are the design + entry points;
> treat them as the spec for that sub-plan.

### Task 3.1: Action-observation evidence channel for the Critic

The Computer-Use loop already emits `ObservationCaptured` / `ActionPlanned` bus events
(see memory: *Computer-Use Voice-Stall-Guard fix, 2026-06-07*). 

- Capture those events + the final screenshot into a per-iteration action-observation
  log, mirroring how `stream_evidence.extract_verified_commands` credits git/gh.
- Add `_augment_diff_with_action_evidence()` in the orchestrator that appends a
  `diff --action-evidence` block when the worker used `computer_use` tools.
- Add a GROUND-TRUTH exception in `critic/prompts.py:111-154`: an empty diff WITH a
  valid action-evidence block is gradable on what the actions did, not auto-revised.
- Guard: a Computer-Use mission with a valid action log but empty git diff is APPROVED,
  not `revise` (regression test in `tests/missions/critic/`).

### Task 3.2: Route quick desktop actions off the heavyweight mission path

"Open Explorer" should not be a 3-iteration Worker-Critic mission at all. Investigate
routing single-step desktop actions through `local_action_gate` (AD-OE3) or a
lightweight direct-action path. Largest lever, largest change — design carefully;
this is where the 18-min-burn disappears entirely.

### Task 3.3: Resume-and-grade an interrupted mission with a draft

The full version of Task 1.1: on recovery, if a mission has a `WorkerDraftReady` and
delivered artifacts, instead of failing it, re-enqueue it for a single Critic pass on
the existing draft (no new worker spawn). Converts "interrupted-with-work" into a real
APPROVED/REJECTED verdict — the difference between "honest label" and "actual success".

---

## 4. Verification — the "consistent success" KPI

This is how we PROVE the gamble is gone. Re-run after each wave against a **copy** of
`data/missions.db` (never lock the live one):

```python
# scripts/mission_health_report.py  (keep this one — it is the KPI dashboard)
import sqlite3, json
from collections import Counter
con = sqlite3.connect("file:data/missions.db?mode=ro", uri=True)
states = Counter(r[0] for r in con.execute("SELECT state FROM missions"))
reasons = Counter()
for (pj,) in con.execute("SELECT payload_json FROM mission_events WHERE event_type='MissionFailed'"):
    reasons[json.loads(pj or '{}').get('reason','?')] += 1
print("states:", dict(states))
print("reasons:", dict(reasons))
```

**Acceptance criteria (post Wave 1 + 2):**
- For single-instance operation, **`crash_recovery` new occurrences = 0** (side
  processes no longer sweep; busy workers no longer mistaken for orphans).
- Delivered-but-interrupted missions appear as `interrupted` WITH artifacts — never
  `crash_recovery` with `partial_artifacts=[]`.
- No mission stays RUNNING for > `hard_cap_s` without a terminal event.
- The post-fix failure rate of **file-producing** missions approaches the worker's
  true error rate (target: APPROVED-rate for simple HTML/file tasks ≥ 90%, vs. the
  current ~50%).
- (Post Wave 3) Computer-Use missions that succeed are APPROVED, not timed out.

---

## 5. Gotchas (read before coding)

- **Restart is mandatory after every fix** (AP-8 restore-trap). `pythonw.exe` holds
  old code in RAM. Run `pwsh scripts/preflight.ps1` in a new worktree first.
- **Use `C:\Program Files\Python311\python.exe`** for tests, not a stray venv — the
  Jarvis interpreter has `filelock` and matches runtime (per the 06-01 memory lesson).
- **Do NOT touch the critic-bucket fixes** (`critic_loop_exhausted` / `critic_unavailable`
  are DEAD — the 06-01 fix works). Append-only to `stream_evidence.py`.
- **`MAX_CRITIC_LOOPS = 3` is ADR-0009-locked** — do not change it; a mission deadline
  is the right lever, not more loops.
- **Five-layer enum discipline** for `interrupted` / `attempts_timed_out` (Task 1.4) —
  BUG-008 recurred 4× from skipping this.
- **The in-flight uncommitted diff** (`orchestrator.py` + `readback.py` +
  `pipeline.py` = the CU stall-guard + `attempts_timed_out` relabel) is GOOD — fold it
  in, don't revert it. It is necessary but not sufficient.
- Every new subprocess uses `NO_WINDOW_CREATIONFLAGS` (AP-1); worktree + Job Object
  per worker (AP-10).

---

## 6. Self-review checklist (run by author)

- **Spec coverage:** crash_recovery (3 holes → Tasks 1.1/1.2/1.3 + 2.1), task_error
  (timeout relabel = in-flight diff + Task 1.4; structural enabler = Wave 3),
  fail-fast (2.2), KPI (§4). ✓
- **Placeholder scan:** Wave 1 & 2 have concrete test code + commands; Wave 3 is
  explicitly flagged as a design spec needing its own plan (Scope Check). ✓
- **Type consistency:** `reason` strings `interrupted` / `attempts_timed_out` used
  identically across readback/announcer/UI/parity test; `_delivered_artifacts` /
  `upsert_heartbeat` referenced consistently. ✓ (Verify `WorkerDraftReady.artifacts`
  field name against `events.py:66` at implementation time — noted in Task 1.1.)
