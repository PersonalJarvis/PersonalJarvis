# Cross-Platform Computer-Use Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Computer-Use select healthy vision-capable planner providers cross-platform, without hardcoding Grok or any platform-specific Computer Use API.

**Architecture:** Add a harness-local `ComputerUsePlannerSelector` that wraps provider-chain iteration, capability filtering, and provider health updates. Keep screenshot capture and action execution unchanged; only the brain-selection step changes.

**Tech Stack:** Python 3.11, pytest/pytest-asyncio, existing `BrainManager`, `BrainRequest`, `BrainDelta`, `RateLimitTracker`, and screenshot-only CU harness.

---

## File Structure

- Create `jarvis/harness/computer_use_planner.py`: provider selection and failure classification for Computer-Use brain calls.
- Modify `jarvis/harness/screenshot_only_loop.py`: delegate provider iteration in `_call_brain` to `ComputerUsePlannerSelector`.
- Modify `tests/unit/harness/test_cu_loop_robustness.py`: add red-green tests for cooldown/dead-provider behavior and selector integration.
- Optional modify `tests/unit/harness/test_cu_read_informational_goal.py` only if current read-goal tests do not already pin "app open is not enough".

## Task 1: Provider Health Regression Tests

**Files:**
- Modify: `tests/unit/harness/test_cu_loop_robustness.py`
- Later create: `jarvis/harness/computer_use_planner.py`

- [ ] **Step 1: Add failing tests for provider health updates**

Add tests near the existing provider-chain tests:

```python
class _RateTrackerProbe:
    def __init__(self) -> None:
        self.marked: list[tuple[str, str | None]] = []
        self.blocked: set[tuple[str, str | None]] = set()

    def is_available(self, provider: str, model: str | None = None) -> bool:
        return (provider, model) not in self.blocked

    def mark_rate_limited(self, provider: str, model: str | None = None,
                          cooldown_s: float | None = None) -> None:
        self.marked.append((provider, model))
        self.blocked.add((provider, model))
```

```python
async def test_call_brain_marks_rate_limited_provider_and_skips_next_call() -> None:
    manager = _FallbackChainManager()
    manager._rate_tracker = _RateTrackerProbe()
    manager._dead_providers = set()
    manager.primary = _StreamingBrain(exc=RuntimeError("429 Too Many Requests"))
    manager.fallback = _StreamingBrain(text='{"action": "done"}')
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = manager
    obs = Observation(trace_id=uuid4(), timestamp_ns=time.time_ns(),
                      screenshot_path=None, screenshot_hash="x")
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    raw = await _call_brain(ctx, observation=obs, user_goal="g",
                            history_text="", images_override=[img])
    assert raw == '{"action": "done"}'
    assert manager._rate_tracker.marked == [("primary", "bad-model")]

    manager.primary.calls = 0
    raw = await _call_brain(ctx, observation=obs, user_goal="g",
                            history_text="", images_override=[img])
    assert raw == '{"action": "done"}'
    assert manager.primary.calls == 0
```

```python
async def test_call_brain_marks_invalid_key_provider_dead() -> None:
    manager = _FallbackChainManager()
    manager._rate_tracker = _RateTrackerProbe()
    manager._dead_providers = set()
    manager.primary = _StreamingBrain(
        exc=RuntimeError("Error code: 401 - invalid x-api-key")
    )
    manager.fallback = _StreamingBrain(text='{"action": "done"}')
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = manager
    obs = Observation(trace_id=uuid4(), timestamp_ns=time.time_ns(),
                      screenshot_path=None, screenshot_hash="x")
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    raw = await _call_brain(ctx, observation=obs, user_goal="g",
                            history_text="", images_override=[img])
    assert raw == '{"action": "done"}'
    assert "primary" in manager._dead_providers

    manager.primary.calls = 0
    raw = await _call_brain(ctx, observation=obs, user_goal="g",
                            history_text="", images_override=[img])
    assert raw == '{"action": "done"}'
    assert manager.primary.calls == 0
```

```python
async def test_call_brain_does_not_mark_transient_5xx_dead() -> None:
    manager = _FallbackChainManager()
    manager._rate_tracker = _RateTrackerProbe()
    manager._dead_providers = set()
    manager.primary = _StreamingBrain(exc=RuntimeError("502 Bad Gateway"))
    manager.fallback = _StreamingBrain(text='{"action": "done"}')
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = manager
    obs = Observation(trace_id=uuid4(), timestamp_ns=time.time_ns(),
                      screenshot_path=None, screenshot_hash="x")
    img = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")

    raw = await _call_brain(ctx, observation=obs, user_goal="g",
                            history_text="", images_override=[img])
    assert raw == '{"action": "done"}'
    assert manager._dead_providers == set()
    assert manager._rate_tracker.marked == []
```

- [ ] **Step 2: Run red tests**

Run:

```powershell
& 'C:\Program Files\Python311\python.exe' -m pytest tests/unit/harness/test_cu_loop_robustness.py -q
```

Expected: the new tests fail because `_call_brain` does not mark rate-limited or invalid-key providers.

## Task 2: Implement `ComputerUsePlannerSelector`

**Files:**
- Create: `jarvis/harness/computer_use_planner.py`

- [ ] **Step 1: Add selector implementation**

Create a focused selector with:

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

@dataclass
class ProviderAttemptError:
    provider: str
    model: str | None
    kind: str
    detail: str

@dataclass
class ComputerUsePlannerSelector:
    manager: Any
    chain: list[tuple[str, str | None]]
    errors: list[ProviderAttemptError] = field(default_factory=list)
    mission_blocked: set[tuple[str, str | None]] = field(default_factory=set)
    blind_skipped: int = 0

    def iter_candidates(self, *, images_attached: bool) -> Iterator[
        tuple[int, str, str | None, Any]
    ]:
        # Yield (chain_index, provider, model, brain) for usable candidates.
        # Skip dead, cooldown, mission-blocked, and screenshot-blind candidates.
        raise NotImplementedError

    def record_failure(self, provider: str, model: str | None, exc: Exception) -> None:
        # Classify provider failures and update manager health where safe.
        raise NotImplementedError

    def error_message(self, *, images_attached: bool, attempted: int) -> str:
        # Return a short CULoopError detail with blind/cooldown/dead/failure context.
        raise NotImplementedError
```

Use `jarvis.brain.manager._classify_provider_error` and `_is_rate_limit_exc` inside `record_failure` to match normal BrainManager semantics. Treat classified `missing_key` and `account_blocked` as session-dead by adding to `manager._dead_providers` when available. Treat `rate_limit` as `_rate_tracker.mark_rate_limited(provider, model)` when available. Treat transient/default failures as mission-local blocked only.

- [ ] **Step 2: Keep capability filtering provider-agnostic**

In `iter_candidates`, call `manager._get_brain(provider, model)` and skip if `images_attached` and `supports_vision` is false. Do not check provider names. Use `_dead_providers` and `_rate_tracker.is_available()` when those attributes exist.

## Task 3: Wire Selector Into `_call_brain`

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py`

- [ ] **Step 1: Replace local provider-loop health logic**

Inside `_call_brain`, after building `chain`, import and instantiate:

```python
from jarvis.harness.computer_use_planner import ComputerUsePlannerSelector

selector = ComputerUsePlannerSelector(manager=manager, chain=chain)
attempted = 0
for idx, provider, model, brain in selector.iter_candidates(images_attached=images_attached):
    attempted += 1
    try:
        agg = await aggregate(brain.complete(req))
        text = (agg.text or "").strip()
        if not text:
            selector.record_empty(provider, model)
            continue
        if idx > 0:
            log.info("ComputerUseLoop fallback hit: %s(%s) after %d skipped provider(s)",
                     provider, model, idx)
        return text
    except Exception as exc:
        selector.record_failure(provider, model, exc)
        log.warning("ComputerUseLoop brain provider %s(%s) failed: %s",
                    provider, model, exc)
        continue
raise CULoopError(selector.error_message(images_attached=images_attached,
                                         attempted=attempted))
```

Adjust exact method names to the implementation from Task 2. Keep fake `complete_text` shim and image assembly unchanged.

- [ ] **Step 2: Run green tests**

Run:

```powershell
& 'C:\Program Files\Python311\python.exe' -m pytest tests/unit/harness/test_cu_loop_robustness.py -q
```

Expected: all tests in that file pass.

## Task 4: Read-Goal Verification Check

**Files:**
- Inspect: `tests/unit/harness/test_cu_read_informational_goal.py`
- Modify only if needed: `tests/unit/harness/test_cu_read_informational_goal.py` and `jarvis/harness/screenshot_only_loop.py`

- [ ] **Step 1: Inspect existing read-goal tests**

Run:

```powershell
rg -n "app open|open app|read|informational|done|verified" tests/unit/harness/test_cu_read_informational_goal.py
```

- [ ] **Step 2: If missing, add one failing test**

Add a test proving an informational/read goal is not complete when the verifier proof only says the app is open and no content was read. The expected result should reject `done` and force another step.

- [ ] **Step 3: Implement only if the test exposes a real gap**

If the existing verifier already has this behavior, do not change production code. Record that the existing tests cover it.

## Task 5: Verification

**Files:**
- No new code unless tests expose gaps.

- [ ] **Step 1: Run targeted CU suites**

Run:

```powershell
& 'C:\Program Files\Python311\python.exe' -m pytest tests/unit/brain/test_explicit_computer_use_routing.py tests/unit/brain/test_cu_vs_spawn_routing.py tests/unit/harness/test_cu_loop_robustness.py tests/unit/harness/test_cu_runaway_guards.py tests/unit/harness/test_cu_grok_vision.py -q
```

Expected: pass.

- [ ] **Step 2: Run routing/tool suites**

Run:

```powershell
& 'C:\Program Files\Python311\python.exe' -m pytest tests/unit/plugins/tool/test_computer_use_tool.py tests/unit/brain/test_computer_use_offload.py tests/unit/brain/test_routing.py tests/unit/brain/test_provider_down_phrase.py tests/unit/brain/test_provider_test.py tests/unit/brain/test_tier_model_resolution.py -q
```

Expected: pass.

- [ ] **Step 3: Inspect diff**

Run:

```powershell
git diff -- jarvis/harness/computer_use_planner.py jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_cu_loop_robustness.py tests/unit/harness/test_cu_read_informational_goal.py
```

Expected: scoped diff, no unrelated refactors.
