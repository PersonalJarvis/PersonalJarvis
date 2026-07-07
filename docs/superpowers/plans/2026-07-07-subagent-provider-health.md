# Subagent Provider Health & Failure Surfacing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface subagent/worker provider failures (dead auth, quota, unreachable, timeout) in the desktop app — proactively via a live-honest Sub-Agents section health + view banner, reactively via a populated `error_class`/`error_detail`/`failed_provider` on mission-failure events, readable failure text in the Sub-Agents view, and an honest voice phrase.

**Architecture:** Extend the existing main-provider section-health pattern (backend `SectionHealth` → `useSectionHealth` → UI) to subagents, and thread a new closed error-class vocabulary through the five layers (Python events → SQLite event store → REST/WS → TS types → UI/voice) with a parity test (AP-4/BUG-008 defense). Spec: `docs/superpowers/specs/2026-07-07-subagent-provider-health-design.md`.

**Tech Stack:** Python 3.11+ (pydantic v2, FastAPI), React + TypeScript (vitest, @testing-library/react), in-house Zustand i18n (en/de/es JSON locales).

## Global Constraints

- Cross-platform: all health checks are pure-stdlib, offline (file reads + process-local flags), no network probes, no OS-specific calls — must work on headless `python:3.11-slim`.
- Zero regressions: `pytest tests/missions/ -q` and `npm run test` (in `jarvis/ui/web/frontend/`) must stay green after every task.
- Artifacts are English-only (code, comments, tests, commit messages). German/Spanish appear ONLY in i18n locale JSON values and in the voice phrase table (product surface; mark German phrase-table lines with `# i18n-allow`).
- Commit after each task, pathspec-scoped (`git add <files>` — never `git add -A`; the working tree is shared).
- Voice phrase table (`FAILURE_REASON_PHRASES`) is de/en today (`Lang = Literal["de","en"]`, `readback.py:34`) — new entries follow that existing table's language set. UI i18n carries en/de/es.
- New error-class tokens (single source `MISSION_ERROR_CLASSES` in `jarvis/missions/events.py`): `provider_auth`, `provider_quota`, `provider_unreachable`, `worker_timeout`. `error_class` fields stay `str | None` (legacy recovery values `MissionInterrupted`/`OrchestratorCrash` remain valid).

---

### Task 1: Error-class vocabulary + event fields (Python + TS mirror + parity test)

**Files:**
- Modify: `jarvis/missions/events.py` (imports line 13; `WorkerKilled` lines 94-109; `MissionFailed` lines 122-127)
- Modify: `jarvis/ui/web/frontend/src/types/missions.ts` (WorkerKilled lines 123-136; MissionFailed lines 148-154)
- Test: `tests/missions/test_mission_error_class_parity.py` (new)

**Interfaces:**
- Produces: `jarvis.missions.events.MISSION_ERROR_CLASSES: frozenset[str]`; `WorkerKilled.error_class/error_detail: str | None`; `MissionFailed.error_detail/failed_provider: str | None`; TS `MissionErrorClass` union type.

- [ ] **Step 1: Write the failing parity test**

Create `tests/missions/test_mission_error_class_parity.py`:

```python
"""Five-layer parity guard for the mission error-class vocabulary (AP-4).

The 2026-07-06 incident produced a mission failure whose cause (dead provider
auth) was invisible in the app: ``MissionFailed.error_class`` existed but was
never populated, and no UI/voice layer consumed it. This test locks the new
closed vocabulary across Python <-> TypeScript so the BUG-008 enum-drift class
cannot recur. (Voice-table and locale-file parity are asserted in
``test_error_class_full_parity.py`` once those layers land.)
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.missions.events import MISSION_ERROR_CLASSES

_REPO = Path(__file__).resolve().parents[2]


def _ts_error_classes() -> set[str]:
    ts = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "types" / "missions.ts"
    text = ts.read_text(encoding="utf-8")
    m = re.search(r"export type MissionErrorClass\s*=([^;]+);", text)
    assert m, "MissionErrorClass union not found in missions.ts"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_error_class_tokens_python_ts_parity() -> None:
    py = set(MISSION_ERROR_CLASSES)
    ts = _ts_error_classes()
    assert py == ts, f"error_class drift — python-only={py - ts}, ts-only={ts - py}"


def test_expected_tokens_present() -> None:
    assert MISSION_ERROR_CLASSES == frozenset(
        {"provider_auth", "provider_quota", "provider_unreachable", "worker_timeout"}
    )


def test_new_event_fields_default_none() -> None:
    """Old stored events (no new fields) must keep validating."""
    from jarvis.missions.events import MissionFailed, WorkerKilled

    mf = MissionFailed(reason="task_error", last_state="CRITIQUING")
    assert mf.error_class is None and mf.error_detail is None
    assert mf.failed_provider is None
    wk = WorkerKilled(worker_id="w1", reason="worker_error")
    assert wk.error_class is None and wk.error_detail is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/test_mission_error_class_parity.py -q`
Expected: FAIL with `ImportError: cannot import name 'MISSION_ERROR_CLASSES'`

- [ ] **Step 3: Implement the Python side**

In `jarvis/missions/events.py`, extend the typing import (line 13):

```python
from typing import Annotated, Any, Final, Literal, Union
```

Directly ABOVE `class WorkerKilled` (line 94), add:

```python
# Closed vocabulary for the provider-failure classification carried by
# WorkerKilled.error_class / MissionFailed.error_class. Single source of
# truth; mirrored in frontend/src/types/missions.ts (MissionErrorClass),
# the voice phrase table (FAILURE_REASON_PHRASES), and the i18n locales —
# guarded by tests/missions/test_mission_error_class_parity.py +
# test_error_class_full_parity.py (AP-4/BUG-008 defense). The event field
# stays `str | None`: the recovery sweep's legacy values
# ("MissionInterrupted"/"OrchestratorCrash") remain valid, and None means
# "unclassified — fall back to `reason`".
MISSION_ERROR_CLASSES: Final[frozenset[str]] = frozenset({
    "provider_auth",        # credential dead/invalid (401, not logged in)
    "provider_quota",       # usage/session/rate limit or billing exhausted
    "provider_unreachable",  # transient availability (5xx, overloaded)
    "worker_timeout",       # wall-clock / first-output timeout
})
```

Extend `WorkerKilled` (after the `reason` field, line 109):

```python
    # Provider-failure classification (2026-07-06 incident): a token from
    # MISSION_ERROR_CLASSES when the kill traces to a classified provider
    # failure, plus the truncated upstream error text. Optional + defaulted
    # so previously stored events keep validating.
    error_class: str | None = None
    error_detail: str | None = None
```

Extend `MissionFailed` (after `partial_artifacts`, line 127):

```python
    # Provider-failure surfacing (2026-07-06 incident): the truncated
    # upstream error text and the provider slug of the worker that failed
    # (e.g. "claude", "codex", "openrouter"). Optional + defaulted so
    # previously stored events keep validating.
    error_detail: str | None = None
    failed_provider: str | None = None
```

- [ ] **Step 4: Implement the TS mirror**

In `jarvis/ui/web/frontend/src/types/missions.ts`, directly ABOVE `export type WorkerKilledReason` (line 123), add:

```ts
/** Closed provider-failure vocabulary — mirrors MISSION_ERROR_CLASSES in
 * jarvis/missions/events.py (parity-tested). `error_class` on the wire stays
 * `string | null` because legacy recovery values also occur. */
export type MissionErrorClass =
  | "provider_auth"
  | "provider_quota"
  | "provider_unreachable"
  | "worker_timeout";
```

Extend the `WorkerKilled` interface:

```ts
export interface WorkerKilled extends BasePayload {
  event_type: "WorkerKilled";
  worker_id: string;
  reason: WorkerKilledReason;
  error_class?: string | null;
  error_detail?: string | null;
}
```

Extend the `MissionFailed` interface:

```ts
export interface MissionFailed extends BasePayload {
  event_type: "MissionFailed";
  reason: string;
  error_class: string | null;
  last_state: string;
  partial_artifacts: string[];
  error_detail?: string | null;
  failed_provider?: string | null;
}
```

(The new TS fields are optional (`?`) because events stored BEFORE this change replay over the WebSocket without them.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/test_mission_error_class_parity.py tests/missions/test_worker_killed_reason_parity.py -q`
Expected: PASS (both parity guards)

- [ ] **Step 6: Commit**

```bash
git add jarvis/missions/events.py jarvis/ui/web/frontend/src/types/missions.ts tests/missions/test_mission_error_class_parity.py
git commit -m "feat(missions): closed error-class vocabulary on WorkerKilled/MissionFailed events"
```

---

### Task 2: `_classify_worker_error` pure helper (orchestrator)

**Files:**
- Modify: `jarvis/missions/kontrollierer/orchestrator.py` (insert directly AFTER `_worker_error_is_auth`, which follows `_worker_error_is_transient` at line 140)
- Test: `tests/missions/kontrollierer/test_error_classification.py` (new)

**Interfaces:**
- Consumes: `_worker_error_is_auth(err)`, `_worker_error_is_transient(err)` (module-level, same file).
- Produces: `_classify_worker_error(err: str, *, timed_out: bool = False) -> str | None` returning a `MISSION_ERROR_CLASSES` token or `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/missions/kontrollierer/test_error_classification.py`:

```python
"""Unit matrix for the worker-error -> error_class mapping (pure function).

Real-world inputs from live incidents: the 2026-07-06 expired Claude OAuth
401, the 2026-06-08 codex refresh-token death, the 2026-06-10 Claude Max
session-limit, the 2026-05-28 zero-output startup timeout.
"""
from __future__ import annotations

import pytest

from jarvis.missions.events import MISSION_ERROR_CLASSES
from jarvis.missions.kontrollierer.orchestrator import _classify_worker_error


@pytest.mark.parametrize(
    "err,expected",
    [
        # 2026-07-06 live text (expired Claude Max OAuth token)
        (
            "Failed to authenticate. API Error: 401 Invalid authentication credentials",
            "provider_auth",
        ),
        ("Failed to refresh token. Please log in again.", "provider_auth"),
        ("Not logged in · Please run /login", "provider_auth"),
        ("You've hit your usage limit. Try again at 7:40 PM.", "provider_quota"),
        ("You've hit your session limit · resets 11:10pm", "provider_quota"),
        ("Credit balance is too low", "provider_quota"),
        ("429 Too Many Requests", "provider_quota"),
        ("503 Service Unavailable", "provider_unreachable"),
        ("upstream is overloaded, please try again", "provider_unreachable"),
        (
            "subprocess produced no output within 120s startup timeout",
            "worker_timeout",
        ),
        ("Compilation failed: missing semicolon", None),
        ("", None),
    ],
)
def test_classification_matrix(err: str, expected: str | None) -> None:
    assert _classify_worker_error(err) == expected


def test_structured_timeout_flag_wins() -> None:
    assert _classify_worker_error("", timed_out=True) == "worker_timeout"
    # Even a classifiable text defers to the structured flag.
    assert _classify_worker_error("401", timed_out=True) == "worker_timeout"


def test_all_returned_tokens_are_in_the_closed_set() -> None:
    samples = [
        "401 Unauthorized", "usage limit", "503", "overloaded", "timeout",
    ]
    for s in samples:
        token = _classify_worker_error(s)
        assert token is None or token in MISSION_ERROR_CLASSES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/kontrollierer/test_error_classification.py -q`
Expected: FAIL with `ImportError: cannot import name '_classify_worker_error'`

- [ ] **Step 3: Implement the classifier**

In `jarvis/missions/kontrollierer/orchestrator.py`, directly AFTER the `_worker_error_is_auth` function body, add:

```python
def _classify_worker_error(err: str, *, timed_out: bool = False) -> str | None:
    """Map a worker's terminal error onto MISSION_ERROR_CLASSES, or ``None``.

    Pure + offline; the single place the orchestrator derives the
    provider-failure class that flows to WorkerKilled/MissionFailed and from
    there to the Sub-Agents view and the voice announcer. Order matters:
    the structured timeout flag wins (it is the robust signal), then auth
    (the most specific text class), then quota/billing, then the generic
    transient bucket. Unclassifiable errors return ``None`` so consumers
    fall back to the mission-level ``reason``.
    """
    if timed_out:
        return "worker_timeout"
    if not err:
        return None
    low = err.lower()
    if _worker_error_is_auth(low):
        return "provider_auth"
    if any(
        m in low
        for m in (
            "balance", "billing", "credit",
            "session limit", "usage limit",
            "rate limit", "rate_limit", "ratelimit",
            "too many requests", "429",
            "out of credits", "out_of_credits",
        )
    ):
        return "provider_quota"
    if "timeout" in low:
        return "worker_timeout"
    if _worker_error_is_transient(low):
        return "provider_unreachable"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/kontrollierer/test_error_classification.py -q`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add jarvis/missions/kontrollierer/orchestrator.py tests/missions/kontrollierer/test_error_classification.py
git commit -m "feat(missions): classify worker terminal errors into the error-class vocabulary"
```

---

### Task 3: Orchestrator wiring — populate WorkerKilled + MissionFailed

**Files:**
- Modify: `jarvis/missions/kontrollierer/orchestrator.py`:
  - `__init__` (anchor: `self._task_iter_diffs: dict[str, list[tuple[int, str]]] = {}` at line 728)
  - the `spawn_result.worker_error` branch (lines ~1229-1330; anchors below)
  - `_publish_worker_killed` (line 1867)
  - `_fail_mission` (line 2575)
  - `_approve_mission` hygiene (anchor: `self._task_answers.pop` usage in `_fail_mission` line 2582; mirror it in approve)
- Test: extend `tests/missions/kontrollierer/test_loop.py`

**Interfaces:**
- Consumes: `_classify_worker_error` (Task 2), event fields (Task 1), the existing `_AuthErrorWorker` fixture in `test_loop.py`.
- Produces: `MissionFailed` events with `error_class`/`error_detail`/`failed_provider` populated whenever a worker terminal error caused the failure; `WorkerKilled` events with `error_class`/`error_detail`.

- [ ] **Step 1: Write the failing test**

Append to `tests/missions/kontrollierer/test_loop.py` (after `test_worker_auth_error_every_iteration_fails_honestly`):

```python
@pytest.mark.asyncio
async def test_mission_failed_carries_error_classification(
    manager: MissionManager, tmp_path: Path
) -> None:
    """The 2026-07-06 gap: MissionFailed.error_class was always None, so the
    UI/voice could not name the cause. An all-401 mission must now carry
    error_class="provider_auth", the truncated upstream text, and the
    provider slug of the worker that failed."""
    worker = _AuthErrorWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="task on a dead credential")

    end = await k.run_mission(mid)
    assert end == MissionState.FAILED

    events = await manager.store.events_for_mission(mid)
    failed = [e.payload for e in events if e.payload.event_type == "MissionFailed"]
    assert len(failed) == 1
    assert failed[0].error_class == "provider_auth"
    assert "401" in (failed[0].error_detail or "")
    assert failed[0].failed_provider == "claude"

    killed = [e.payload for e in events if e.payload.event_type == "WorkerKilled"]
    assert killed
    assert killed[-1].error_class == "provider_auth"
    assert "401" in (killed[-1].error_detail or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest "tests/missions/kontrollierer/test_loop.py::test_mission_failed_carries_error_classification" -q`
Expected: FAIL with `assert None == "provider_auth"`

- [ ] **Step 3: Implement the wiring**

(a) In `Kontrollierer.__init__`, directly after `self._task_iter_diffs: dict[str, list[tuple[int, str]]] = {}` (line 728), add:

```python
        # Last classified worker-failure per mission (error_class,
        # error_detail, failed_provider) — written by the worker_error branch
        # in the critic loop, consumed once by _fail_mission so the terminal
        # MissionFailed event can name the real cause (2026-07-06 incident).
        # Popped on BOTH terminal paths (fail + approve) so a retried-then-
        # approved mission never leaks a stale context into a later run.
        self._mission_failure_context: dict[str, dict[str, str | None]] = {}
```

(b) In the `spawn_result.worker_error` branch, directly after the `is_auth = _worker_error_is_auth(err_lower)` assignment (added in the 2026-07-06 fix; it follows the `is_transient` assignment near line 1242), add:

```python
                # Record the classified failure for the terminal MissionFailed
                # event. Last write wins: the final iteration's cause is the
                # one the mission actually died of.
                self._mission_failure_context[mission_id] = {
                    "error_class": _classify_worker_error(
                        spawn_result.worker_error,
                        timed_out=spawn_result.worker_timed_out,
                    ),
                    "error_detail": spawn_result.worker_error[:300],
                    "failed_provider": (
                        getattr(worker, "provider", None)
                        or getattr(worker, "cli", None)
                    ),
                }
```

(c) In the same branch, the `else:` arm that publishes the kill (anchor: `await self._publish_worker_killed(` with `reason=kill_reason`) gains the two new kwargs:

```python
                    await self._publish_worker_killed(
                        mission_id=mission_id,
                        worker_id=spawn_result.worker_id,
                        reason=kill_reason,
                        error_class=self._mission_failure_context.get(
                            mission_id, {}
                        ).get("error_class"),
                        error_detail=spawn_result.worker_error[:300],
                    )
```

(d) `_publish_worker_killed` (line 1867) — extend signature and payload:

```python
    async def _publish_worker_killed(
        self,
        *,
        mission_id: str,
        worker_id: str,
        reason: str,
        error_class: str | None = None,
        error_detail: str | None = None,
    ) -> None:
```

and the payload construction (line 1892):

```python
            payload=WorkerKilled(
                worker_id=worker_id,
                reason=mapped,  # type: ignore[arg-type]
                error_class=error_class,
                error_detail=error_detail,
            ),
```

(e) `_fail_mission` (line 2575) — read the context once and attach it. After the hygiene line `self._task_answers.pop(mission_id, None)` add:

```python
        failure_ctx = self._mission_failure_context.pop(mission_id, {})
```

and extend the payload construction:

```python
            payload=MissionFailed(
                reason=reason,
                error_class=failure_ctx.get("error_class"),
                error_detail=failure_ctx.get("error_detail"),
                failed_provider=failure_ctx.get("failed_provider"),
                last_state=view.state.value,
                partial_artifacts=partial_artifacts or [],
            ),
```

(f) In `_approve_mission` (the method constructing `MissionApproved`, directly above `_fail_mission`), add the same hygiene pop at its start:

```python
        self._mission_failure_context.pop(mission_id, None)
```

- [ ] **Step 4: Run tests to verify they pass (incl. no regression)**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/kontrollierer/test_loop.py -q`
Expected: PASS (all loop tests, incl. the new one)

- [ ] **Step 5: Commit**

```bash
git add jarvis/missions/kontrollierer/orchestrator.py tests/missions/kontrollierer/test_loop.py
git commit -m "feat(missions): MissionFailed/WorkerKilled carry error_class, error_detail, failed_provider"
```

---

### Task 4: Sub-Agents registry — readable error + error_class on the node

**Files:**
- Modify: `jarvis/agents/registry.py` (`AgentNode` dataclass lines 66-90; `WorkerKilled` handler lines 414-422; `MissionFailed` handler lines 448-462)
- Modify: `jarvis/ui/web/frontend/src/store/jarvisAgents.ts` (`SubAgentNode` interface lines 24-48)
- Test: extend `tests/unit/agents/test_registry.py`

**Interfaces:**
- Consumes: `WorkerKilled.error_class/error_detail`, `MissionFailed.error_class/error_detail` (Task 1/3).
- Produces: `AgentNode.error_class: str | None` (serialized by `dataclasses.asdict` → `/api/sub-agents/tree`); `node.error` now carries the human `error_detail` when present. TS `SubAgentNode.error_class?: string | null`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agents/test_registry.py` (follow that file's existing envelope/fixture helpers — it already builds `MissionFailed`/`WorkerKilled` envelopes for the current handlers; mirror the nearest existing test's setup):

```python
def test_mission_failed_carries_error_detail_and_class(registry_with_mission):
    """node.error must show the human detail (the 401 text), not the raw
    reason token; error_class rides along for the UI message map."""
    registry, mission_tid = registry_with_mission
    _dispatch_mission_failed(  # helper mirrors existing failed-mission tests
        registry,
        mission_tid,
        reason="task_error",
        error_class="provider_auth",
        error_detail="Failed to authenticate. API Error: 401",
    )
    node = registry.snapshot()[mission_tid]
    assert node.status == "failed"
    assert node.error == "Failed to authenticate. API Error: 401"
    assert node.error_class == "provider_auth"


def test_mission_failed_without_detail_keeps_reason_fallback(registry_with_mission):
    registry, mission_tid = registry_with_mission
    _dispatch_mission_failed(registry, mission_tid, reason="task_error")
    node = registry.snapshot()[mission_tid]
    assert node.error == "task_error"
    assert node.error_class is None
```

(If `tests/unit/agents/test_registry.py` has no reusable fixture for a mission node + failed dispatch, add a small `registry_with_mission` fixture + `_dispatch_mission_failed` helper in that file, modeled 1:1 on its existing MissionFailed test — do not invent a new event-construction style.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agents/test_registry.py -q -k error_detail`
Expected: FAIL (`AgentNode` has no attribute `error_class` / `node.error == "task_error"`)

- [ ] **Step 3: Implement**

(a) `AgentNode` dataclass — after `error: str | None = None` (line 88), add:

```python
    error_class: str | None = None
```

(b) `WorkerKilled` handler (lines 414-422) — replace the `node.error` line:

```python
            node.error = payload.error_detail or f"killed: {payload.reason}"
            node.error_class = payload.error_class
```

(c) `MissionFailed` branch (line 455) — replace `node.error = payload.reason`:

```python
                node.error = payload.error_detail or payload.reason
                node.error_class = payload.error_class
```

(d) TS `SubAgentNode` (store/jarvisAgents.ts) — after `error?: string | null;`:

```ts
  error_class?: string | null;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agents/test_registry.py -q`
Expected: PASS (all registry tests)

- [ ] **Step 5: Commit**

```bash
git add jarvis/agents/registry.py jarvis/ui/web/frontend/src/store/jarvisAgents.ts tests/unit/agents/test_registry.py
git commit -m "feat(agents): sub-agent nodes carry error_class + human error detail"
```

---

### Task 5: Voice — honest failure phrases for error classes

**Files:**
- Modify: `jarvis/missions/voice/readback.py` (`FAILURE_REASON_PHRASES` lines 175-204; `render_failed` line 294; new helper `failure_phrase_key`)
- Modify: `jarvis/missions/voice/announcer.py` (`MissionFailed` branch lines 234-280)
- Test: extend `tests/missions/test_voice_announcer.py`

**Interfaces:**
- Consumes: `MissionFailed.error_class` (Task 1/3).
- Produces: `failure_phrase_key(reason: str, error_class: str | None) -> str` in `readback.py`; four new keys in BOTH language maps of `FAILURE_REASON_PHRASES`; `render_failed(..., error_class: str | None = None)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/missions/test_voice_announcer.py` (mirror that file's existing MissionFailed-announcement test setup for envelope construction and `_render` invocation):

```python
def test_failed_announcement_names_provider_auth_cause() -> None:
    """error_class beats the generic reason phrase: a provider_auth failure
    must not be spoken as the meaningless 'The worker aborted.'"""
    env = _failed_envelope(reason="task_error", error_class="provider_auth")
    text_de, _prio = _render(env, lang="de")
    assert "Anmeldung" in text_de  # i18n-allow: asserted German TTS output
    text_en, _prio = _render(env, lang="en")
    assert "sign-in" in text_en
    assert "worker aborted" not in text_en.lower()


def test_failed_announcement_without_error_class_keeps_reason_phrase() -> None:
    env = _failed_envelope(reason="task_error", error_class=None)
    text_en, _prio = _render(env, lang="en")
    assert "The worker aborted." in text_en
```

(Reuse/extend the file's existing envelope factory; if none exists for MissionFailed, add `_failed_envelope(reason, error_class=None)` + `_render(env, lang)` thin wrappers around the same construction the existing failure tests use.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/test_voice_announcer.py -q -k provider_auth`
Expected: FAIL (text is "The mission failed. The worker aborted.")

- [ ] **Step 3: Implement**

(a) `readback.py` — add the four entries to BOTH maps in `FAILURE_REASON_PHRASES` (inside the existing dict literals; keep the `# i18n-allow` convention on German lines):

```python
        # error_class keys (looked up BEFORE the reason key; see
        # failure_phrase_key). Same table so announcer + direct-TTS listener
        # cannot drift (2026-05-27 finding #7).
        "provider_auth": "Die Anmeldung beim KI-Anbieter ist ungültig oder abgelaufen.",  # i18n-allow
        "provider_quota": "Das Kontingent des KI-Anbieters ist erschöpft.",  # i18n-allow
        "provider_unreachable": "Der KI-Anbieter ist gerade nicht erreichbar.",  # i18n-allow
        "worker_timeout": "Der Worker hat das Zeitlimit überschritten.",  # i18n-allow
```

and in the `"en"` map:

```python
        "provider_auth": "The AI provider sign-in is invalid or expired.",
        "provider_quota": "The AI provider's quota is exhausted.",
        "provider_unreachable": "The AI provider is currently unreachable.",
        "worker_timeout": "The worker hit its time limit.",
```

(b) `readback.py` — add the shared key resolver directly BELOW `FAILURE_REASON_PHRASES`:

```python
def failure_phrase_key(reason: str, error_class: str | None) -> str:
    """Pick the phrase-table key for a failed mission.

    A populated ``error_class`` (e.g. ``provider_auth``) is more specific
    than the mission-level ``reason`` (often the generic ``task_error``), so
    it wins whenever the table carries it. Falls back to the reason's short
    form. Single source for the announcer AND the direct-TTS listener.
    """
    ec = (error_class or "").strip()
    if ec and ec in FAILURE_REASON_PHRASES["en"]:
        return ec
    return (reason or "").split(":", 1)[0].strip()
```

(c) `readback.py` — `render_failed` gains the parameter and uses the resolver. Change the signature (line 294) and the first line:

```python
    def render_failed(
        self, *, reason: str = "", language: Lang = "de",
        error_class: str | None = None,
    ) -> str:
        short_reason = failure_phrase_key(reason, error_class)
```

(the rest of the method body is unchanged — it already looks up `short_reason` in the table).

(d) `announcer.py` — in the `MissionFailed` branch, replace the two lines

```python
            reason = (getattr(payload, "reason", "") or "").strip()
            short_reason = reason.split(":", 1)[0].strip()
```

with:

```python
            reason = (getattr(payload, "reason", "") or "").strip()
            short_reason = failure_phrase_key(
                reason, getattr(payload, "error_class", None)
            )
```

and add `failure_phrase_key` to the existing `from .readback import ...` import in `announcer.py`. (The `crash_recovery`/`interrupted` suppression right below keeps working: those come in via `reason`, never via `error_class`, and `failure_phrase_key` returns the short reason when `error_class` is absent.)

- [ ] **Step 4: Run tests to verify they pass (incl. no regression)**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/test_voice_announcer.py tests/missions/test_voice_readback.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/missions/voice/readback.py jarvis/missions/voice/announcer.py tests/missions/test_voice_announcer.py
git commit -m "feat(voice): mission-failure announcements name the provider error class"
```

---

### Task 6: Live-honest Sub-Agents section health

**Files:**
- Modify: `jarvis/missions/init.py` (new `reachable_worker_families()` next to `_cross_family_last_resort_worker`, line 452)
- Modify: `jarvis/ui/web/provider_routes.py` (`_worker_usable` line 646; `_jarvis_agent_section_health` line 676; new `_worker_flagged_dead`)
- Test: `tests/missions/test_reachable_worker_families.py` (new) + extend `tests/unit/brain/test_section_health.py`

**Interfaces:**
- Consumes: `_claude_cli_auth_viable()` (jarvis/missions/init.py), `codex_needs_reauth()`, `claude_in_quota_cooldown()`, `_codex_oauth_available()`, `_resolve_claude_binary()`, `get_provider_secret()`, `supports_api_agent_worker()`.
- Produces: `jarvis.missions.init.reachable_worker_families() -> list[str]` (ordered, subscription-first, cheap/offline); `provider_routes._worker_flagged_dead(provider: str) -> bool`; `_jarvis_agent_section_health` that can return `error`.

- [ ] **Step 1: Write the failing tests**

Create `tests/missions/test_reachable_worker_families.py`:

```python
"""reachable_worker_families() — the cheap, offline family probe that feeds
the Sub-Agents section health (which families could run a mission RIGHT NOW).
Same probe seams as tests/missions/test_worker_cross_family_fallback.py."""
from __future__ import annotations

import pytest

from jarvis.missions import init as mi


def _patch_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    claude_binary: str | None = None,
    claude_auth_viable: bool = True,
    codex_oauth: bool = False,
    codex_reauth: bool = False,
    keys: tuple[str, ...] = (),
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
        lambda: claude_binary,
    )
    monkeypatch.setattr(mi, "_claude_cli_auth_viable", lambda: claude_auth_viable)
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available",
        lambda: codex_oauth,
    )
    monkeypatch.setattr(
        "jarvis.codex_auth_state.codex_needs_reauth", lambda: codex_reauth
    )
    keyset = {k.strip().lower() for k in keys}
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret",
        lambda p: "KEY" if (p or "").strip().lower() in keyset else None,
    )


def test_all_dead_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_env(monkeypatch)
    assert mi.reachable_worker_families() == []


def test_subscription_first_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_env(
        monkeypatch,
        claude_binary="/usr/bin/claude", claude_auth_viable=True,
        codex_oauth=True, keys=("openrouter",),
    )
    fams = mi.reachable_worker_families()
    assert fams[0] == "claude"
    assert "codex" in fams and "openrouter" in fams


def test_dead_claude_is_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-07-06 shape: binary present, auth dead -> claude absent."""
    _patch_env(
        monkeypatch,
        claude_binary="/usr/bin/claude", claude_auth_viable=False,
        codex_oauth=True,
    )
    fams = mi.reachable_worker_families()
    assert "claude" not in fams
    assert fams == ["codex"]
```

Append to `tests/unit/brain/test_section_health.py` (new class at the end; the subagent rollup is pure given monkeypatched probes):

```python
class TestSubagentSectionHealth:
    """Live-honest Sub-Agents tab health (2026-07-06 incident: the tab stayed
    green while every worker spawn 401'd on an expired OAuth token)."""

    def _cfg(self, provider: str = "claude-api"):
        class _Sub:  # minimal cfg.brain.worker stand-in
            pass

        sub = _Sub()
        sub.provider = provider

        class _Brain:
            worker = sub
            primary = "openrouter"

        class _Cfg:
            brain = _Brain()

        return _Cfg()

    def test_selected_usable_is_ok(self, monkeypatch) -> None:
        from jarvis.ui.web import provider_routes as pr

        monkeypatch.setattr(pr, "_worker_usable", lambda p: True)
        monkeypatch.setattr(pr, "_worker_flagged_dead", lambda p: False)
        health = pr._jarvis_agent_section_health(self._cfg())
        assert health.status == sh.OK

    def test_selected_dead_with_fallback_is_needs_setup(self, monkeypatch) -> None:
        from jarvis.ui.web import provider_routes as pr

        monkeypatch.setattr(pr, "_worker_usable", lambda p: True)
        monkeypatch.setattr(pr, "_worker_flagged_dead", lambda p: True)
        monkeypatch.setattr(
            "jarvis.missions.init.reachable_worker_families", lambda: ["codex"]
        )
        health = pr._jarvis_agent_section_health(self._cfg())
        assert health.status == sh.NEEDS_SETUP
        assert health.reason == "degraded"
        assert "codex" in health.detail

    def test_nothing_reachable_is_error(self, monkeypatch) -> None:
        from jarvis.ui.web import provider_routes as pr

        monkeypatch.setattr(pr, "_worker_usable", lambda p: False)
        monkeypatch.setattr(pr, "_worker_flagged_dead", lambda p: False)
        monkeypatch.setattr(
            "jarvis.missions.init.reachable_worker_families", lambda: []
        )
        health = pr._jarvis_agent_section_health(self._cfg())
        assert health.status == sh.ERROR
        assert health.reason == "no_provider"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/test_reachable_worker_families.py tests/unit/brain/test_section_health.py -q`
Expected: FAIL (`reachable_worker_families` / `_worker_flagged_dead` not defined)

- [ ] **Step 3: Implement**

(a) `jarvis/missions/init.py` — directly BELOW `_cross_family_last_resort_worker`, add:

```python
def reachable_worker_families() -> list[str]:
    """Which worker families could run a heavy mission RIGHT NOW (cheap,
    offline — file reads + process-local flags, never a network probe).

    Same subscription-first order as ``_cross_family_last_resort_worker``:
    Claude Max CLI (auth-viable), codex ChatGPT OAuth, then the API-key
    families. Feeds the Sub-Agents section health so the app can warn
    BEFORE a mission is dispatched (2026-07-06: the tab stayed green for
    17 hours of guaranteed-dead spawns).
    """
    families: list[str] = []
    from jarvis.missions.workers.claude_direct_worker import _resolve_claude_binary

    if _resolve_claude_binary() is not None and _claude_cli_auth_viable():
        families.append("claude")
    from jarvis.codex_auth_state import codex_needs_reauth
    from jarvis.missions.workers.codex_direct_worker import _codex_oauth_available

    if _codex_oauth_available() and not codex_needs_reauth():
        families.append("codex")
    from jarvis.core.config import get_provider_secret
    from jarvis.missions.workers.api_agent_worker import supports_api_agent_worker

    for prov in ("claude-api", "gemini", "openrouter", "openai"):
        if supports_api_agent_worker(prov) and get_provider_secret(prov):
            families.append(prov)
    return families
```

(b) `jarvis/ui/web/provider_routes.py` — directly BELOW `_worker_usable` (line 673), add:

```python
def _worker_flagged_dead(provider: str) -> bool:
    """True when the SELECTED worker provider is proven dead/cooling right
    now — signals the presence-only ``_worker_usable`` cannot see (the
    2026-07-06 gap: an expired-in-place OAuth token, a session-dead flag, a
    quota cooldown). Cheap + offline; any probe failure degrades to False
    (fall back to the presence check, never a false red).
    """
    p = (provider or "").lower()
    try:
        if p in {"claude-api", "claude"}:
            from jarvis.claude_quota_state import claude_in_quota_cooldown
            from jarvis.missions.init import _claude_cli_auth_viable

            return claude_in_quota_cooldown() or not _claude_cli_auth_viable()
        if p in _CODEX_SUBAGENT_SLUGS or p in {"codex", "openai-codex"}:
            from jarvis.codex_auth_state import codex_needs_reauth

            return codex_needs_reauth()
    except Exception:  # noqa: BLE001
        return False
    return False
```

(c) Replace the tail of `_jarvis_agent_section_health` (the `if _worker_usable(provider): ... return SectionHealth(...)` block, lines 702-710) with:

```python
    if _worker_usable(provider) and not _worker_flagged_dead(provider):
        return SectionHealth(
            status=_section_health.OK, reason="ok", detail=f"Subagent worker: {label}"
        )
    # The selected worker cannot run right now. Distinguish "a fallback
    # family carries the missions" (amber) from "nothing is reachable —
    # the next mission WILL fail" (red).
    try:
        from jarvis.missions.init import reachable_worker_families

        families = reachable_worker_families()
    except Exception:  # noqa: BLE001
        families = []
    if families:
        return SectionHealth(
            status=_section_health.NEEDS_SETUP,
            reason="degraded",
            detail=(
                f"Subagent worker '{label}' is unavailable — missions run on "
                f"{families[0]} until it is reconnected"
            ),
        )
    return SectionHealth(
        status=_section_health.ERROR,
        reason="no_provider",
        detail=(
            f"No subagent provider is reachable — missions will fail. "
            f"Reconnect '{label}' or add an API key."
        ),
    )
```

Also update the function's docstring (it currently states "It never flags ``error``" — replace that sentence with: `Since 2026-07-07 it distinguishes degraded (fallback carries) from error (nothing reachable).`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/test_reachable_worker_families.py tests/unit/brain/test_section_health.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/missions/init.py jarvis/ui/web/provider_routes.py tests/missions/test_reachable_worker_families.py tests/unit/brain/test_section_health.py
git commit -m "feat(providers): live-honest Sub-Agents section health (degraded vs no-provider)"
```

---

### Task 7: Frontend — health banner + readable failure text + i18n

**Files:**
- Create: `jarvis/ui/web/frontend/src/views/sub-agents/failureLabel.ts`
- Create: `jarvis/ui/web/frontend/src/views/sub-agents/failureLabel.test.ts`
- Modify: `jarvis/ui/web/frontend/src/views/sub-agents/DepartureBoard.tsx` (props lines 80-85; `resultLabel` lines 71-78; banner block lines 152-157; drilldown line 331)
- Modify: `jarvis/ui/web/frontend/src/views/JarvisAgentsView.tsx` (pass health prop)
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/en.json`, `de.json`, `es.json` (`subagents_view` namespace)

**Interfaces:**
- Consumes: `SubAgentNode.error_class` (Task 4), `useSectionHealth()` + `SectionHealth` from `@/hooks/useProviders`, `useT()` from `@/i18n`.
- Produces: `failureLabel(node: Pick<SubAgentNode, "error" | "error_class">, t: (key: string) => string): string | null`; `DepartureBoard` props gain `health?: SectionHealth | null`.

- [ ] **Step 1: Write the failing test**

Create `jarvis/ui/web/frontend/src/views/sub-agents/failureLabel.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { failureLabel } from "./failureLabel";

const t = (key: string) => `[${key}]`; // identity-ish stub: proves the key used

describe("failureLabel", () => {
  it("maps a known error_class to its i18n message and appends the detail", () => {
    expect(
      failureLabel(
        { error: "Failed to authenticate. API Error: 401", error_class: "provider_auth" },
        t,
      ),
    ).toBe("[subagents_view.error_class.provider_auth] (Failed to authenticate. API Error: 401)");
  });

  it("falls back to the raw error when the class is unknown/legacy", () => {
    expect(failureLabel({ error: "task_error", error_class: "OrchestratorCrash" }, t)).toBe(
      "task_error",
    );
    expect(failureLabel({ error: "task_error", error_class: null }, t)).toBe("task_error");
  });

  it("returns null when there is no error at all", () => {
    expect(failureLabel({ error: null, error_class: null }, t)).toBeNull();
  });

  it("uses the mapped message alone when no detail text exists", () => {
    expect(failureLabel({ error: null, error_class: "worker_timeout" }, t)).toBe(
      "[subagents_view.error_class.worker_timeout]",
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `jarvis/ui/web/frontend/`): `npm run test -- run src/views/sub-agents/failureLabel.test.ts`
Expected: FAIL (module `./failureLabel` not found)

- [ ] **Step 3: Implement**

(a) Create `jarvis/ui/web/frontend/src/views/sub-agents/failureLabel.ts`:

```ts
import type { SubAgentNode } from "@/store/jarvisAgents";

/** i18n keys for the closed MissionErrorClass vocabulary (parity-tested
 * against jarvis/missions/events.py MISSION_ERROR_CLASSES). Legacy/unknown
 * classes fall back to the raw error text. */
const ERROR_CLASS_KEYS: Record<string, string> = {
  provider_auth: "subagents_view.error_class.provider_auth",
  provider_quota: "subagents_view.error_class.provider_quota",
  provider_unreachable: "subagents_view.error_class.provider_unreachable",
  worker_timeout: "subagents_view.error_class.worker_timeout",
};

/** Human failure label for a sub-agent node: the i18n message for a known
 * error_class (with the upstream detail in parentheses), else the raw error
 * text, else null (no failure to show). */
export function failureLabel(
  node: Pick<SubAgentNode, "error" | "error_class">,
  t: (key: string) => string,
): string | null {
  const key = node.error_class ? ERROR_CLASS_KEYS[node.error_class] : undefined;
  if (key) {
    const msg = t(key);
    return node.error ? `${msg} (${node.error})` : msg;
  }
  return node.error ?? null;
}
```

(b) `DepartureBoard.tsx`:

- Add imports:

```ts
import type { SectionHealth } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { failureLabel } from "./failureLabel";
```

- Extend props:

```ts
interface Props {
  agents?: SubAgentNode[];
  snapshotError?: string | null;
  health?: SectionHealth | null;
}

export function DepartureBoard({ agents = [], snapshotError = null, health = null }: Props) {
  const t = useT();
```

- Change `resultLabel` to accept the translator and use `failureLabel` (replace lines 71-78):

```ts
function resultLabel(node: SubAgentNode, t: (key: string) => string): string {
  const failure = failureLabel(node, t);
  if (failure) return failure;
  const summary = [...node.prompts].reverse().find((p) => p.startsWith("[summary] "));
  if (summary) return summary.replace("[summary] ", "");
  if (node.status === "completed") return "Done";
  if (node.status === "running") return "In progress";
  return "-";
}
```

Update its call sites to pass `t` (the table cell that renders the Result column and any other `resultLabel(...)` usage; `t` must be threaded as a prop into `AgentRow` if `resultLabel` is called there — follow the existing prop-drilling style of the file).

- Add the health banner directly ABOVE the existing `snapshotError` block (line 152):

```tsx
        {health && (health.status === "needs_setup" || health.status === "error") && (
          <div
            className={cn(
              "mb-3 flex items-center gap-2 rounded-md border px-3 py-2 text-xs",
              health.status === "error"
                ? "border-destructive/30 bg-destructive/10 text-destructive"
                : "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
            )}
          >
            <CircleAlert className="h-4 w-4 shrink-0" />
            <span className="font-medium">
              {t(
                health.status === "error"
                  ? "subagents_view.health_error"
                  : "subagents_view.health_degraded",
              )}
            </span>
            <span className="opacity-80">{health.detail}</span>
          </div>
        )}
```

- Drilldown (line 331): replace `{agent.error && <div className="text-destructive">{agent.error}</div>}` with:

```tsx
                {failureLabel(agent, t) && (
                  <div className="text-destructive">{failureLabel(agent, t)}</div>
                )}
```

(c) `JarvisAgentsView.tsx` — fetch the health and pass it down:

```ts
import { useSectionHealth } from "@/hooks/useProviders";
```

inside the component:

```ts
  const { health } = useSectionHealth();
```

and at the `<DepartureBoard ...>` render site add the prop:

```tsx
      <DepartureBoard agents={rows} snapshotError={snapshotError} health={health["subagents"] ?? null} />
```

(match the existing prop names at that call site; only ADD `health`).

(d) i18n — add to the `subagents_view` object in each locale file:

`en.json`:

```json
    "health_degraded": "Subagent provider degraded",
    "health_error": "No subagent provider reachable",
    "error_class": {
      "provider_auth": "Provider sign-in invalid or expired — reconnect it in the API-Keys view.",
      "provider_quota": "Provider quota exhausted — missions retry on another provider.",
      "provider_unreachable": "Provider temporarily unreachable.",
      "worker_timeout": "The worker hit its time limit."
    }
```

`de.json`:

```json
    "health_degraded": "Subagent-Anbieter beeinträchtigt",
    "health_error": "Kein Subagent-Anbieter erreichbar",
    "error_class": {
      "provider_auth": "Anbieter-Anmeldung ungültig oder abgelaufen — in der API-Keys-Ansicht neu verbinden.",
      "provider_quota": "Anbieter-Kontingent erschöpft — Missionen weichen auf einen anderen Anbieter aus.",
      "provider_unreachable": "Anbieter vorübergehend nicht erreichbar.",
      "worker_timeout": "Der Worker hat das Zeitlimit überschritten."
    }
```

`es.json`:

```json
    "health_degraded": "Proveedor de subagentes degradado",
    "health_error": "Ningún proveedor de subagentes accesible",
    "error_class": {
      "provider_auth": "El inicio de sesión del proveedor no es válido o ha caducado; vuelve a conectarlo en la vista de claves API.",
      "provider_quota": "Cuota del proveedor agotada; las misiones usan otro proveedor.",
      "provider_unreachable": "Proveedor temporalmente inaccesible.",
      "worker_timeout": "El worker alcanzó su límite de tiempo."
    }
```

- [ ] **Step 4: Run tests + typecheck to verify**

Run (in `jarvis/ui/web/frontend/`): `npm run test -- run src/views/sub-agents/` and `npx tsc --noEmit` (or the repo's typecheck script if `package.json` defines one)
Expected: PASS / no type errors

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/views/sub-agents/failureLabel.ts jarvis/ui/web/frontend/src/views/sub-agents/failureLabel.test.ts jarvis/ui/web/frontend/src/views/sub-agents/DepartureBoard.tsx jarvis/ui/web/frontend/src/views/JarvisAgentsView.tsx jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(ui): Sub-Agents health banner + readable provider-failure messages"
```

---

### Task 8: Full-parity guard + regression sweep + spec sync

**Files:**
- Create: `tests/missions/test_error_class_full_parity.py`
- Modify: `docs/superpowers/specs/2026-07-07-subagent-provider-health-design.md` (§6: phrase entries live in the EXISTING de/en `FAILURE_REASON_PHRASES` table — the voice system's `Lang` is de/en; UI i18n carries en/de/es)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the full parity test**

Create `tests/missions/test_error_class_full_parity.py`:

```python
"""Cross-layer parity for MISSION_ERROR_CLASSES (AP-4/BUG-008 defense):
Python events <-> voice phrase table (de+en) <-> UI locale files (en/de/es).
The Python<->TS union is guarded in test_mission_error_class_parity.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.missions.events import MISSION_ERROR_CLASSES
from jarvis.missions.voice.readback import FAILURE_REASON_PHRASES

_REPO = Path(__file__).resolve().parents[2]
_LOCALES = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "i18n" / "locales"


@pytest.mark.parametrize("lang", sorted(FAILURE_REASON_PHRASES))
def test_voice_table_carries_every_error_class(lang: str) -> None:
    missing = MISSION_ERROR_CLASSES - set(FAILURE_REASON_PHRASES[lang])
    assert not missing, f"FAILURE_REASON_PHRASES[{lang!r}] missing {missing}"


@pytest.mark.parametrize("locale", ["en", "de", "es"])
def test_ui_locales_carry_every_error_class(locale: str) -> None:
    data = json.loads((_LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    keys = set(data.get("subagents_view", {}).get("error_class", {}))
    missing = MISSION_ERROR_CLASSES - keys
    assert not missing, f"{locale}.json subagents_view.error_class missing {missing}"
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python.exe -m pytest tests/missions/test_error_class_full_parity.py -q`
Expected: PASS (Tasks 5 + 7 delivered the entries; if it fails, the missing layer is named in the assert)

- [ ] **Step 3: Update the spec's §6 language note**

In `docs/superpowers/specs/2026-07-07-subagent-provider-health-design.md` §6, replace the sentence about a new `ERROR_CLASS_PHRASES` table carrying de/en/es with: the entries live in the EXISTING `FAILURE_REASON_PHRASES` table (de/en — the voice readback system's `Lang` literal), while the UI i18n locales carry en/de/es; extending the voice system itself to `es` is tracked as separate backlog.

- [ ] **Step 4: Full regression sweep**

Run:
- `.venv/Scripts/python.exe -m pytest tests/missions/ tests/unit/agents/ tests/unit/brain/test_section_health.py -q`
- in `jarvis/ui/web/frontend/`: `npm run test -- run`
- `.venv/Scripts/python.exe -m ruff check jarvis/missions/ jarvis/agents/registry.py jarvis/ui/web/provider_routes.py` (pre-existing findings in untouched lines are out of scope; new code must be clean)

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/missions/test_error_class_full_parity.py docs/superpowers/specs/2026-07-07-subagent-provider-health-design.md
git commit -m "test(missions): cross-layer parity guard for the error-class vocabulary"
```
