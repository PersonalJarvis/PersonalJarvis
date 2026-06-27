# Computer-Use opt-in proactive zoom-before-click — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, default-off flag that runs the existing zoom-refine pass *before* the first Computer-Use click, so the model confirms or relocates the named target in a magnified live crop — and refuses to click when the target is not in the crop, catching wrong-element clicks.

**Architecture:** Reuse the existing `_refine_click_point` zoom-refine machinery in `jarvis/harness/screenshot_only_loop.py`. The only behavioural change is a gate: the refine pass, today guarded `if clicked:` (retry-only), additionally runs on the first attempt when `[computer_use].zoom_before_click` is on and the click action carries a `target`. A new config field is threaded through `ComputerUseConfig` → `ComputerUseContext` (hot-reloadable) → the loop. No new machinery, no new dependency.

**Tech Stack:** Python 3.11, Pydantic `BaseModel` config, `dataclass` context singleton, `pytest` (`asyncio_mode=auto`), repo fakes (no `unittest.mock`).

## Global Constraints

- **Artifacts are English** — code, comments, docstrings, test names, commit messages. (Enforced by the `language-policy` CI gate; new German lines block merge.)
- **Opt-in, default OFF** — `zoom_before_click: bool = False`. With the flag off, behaviour is byte-for-byte the current behaviour; no latency regression.
- **Invisible on screen (hard requirement)** — the zoom is an internal screenshot crop only. No on-screen magnification, no lens/overlay, no zoom animation; the cursor moves only at the real click. (Satisfied automatically — the reused crop path renders nothing.)
- **Fail-safe** — any failure of the zoom step (crop grab `None`, brain timeout, malformed verdict) degrades to today's plain coarse click; never a hard error.
- **No application-visible side effects** — passive screen read only; no synthetic zoom keystroke, no DOM mutation, no accessibility-tree write.
- **No new dependency**; no new Windows-only requirement (the verified-miss UIA-snap stays Windows-only and already degrades to a no-op elsewhere).
- **Shared working tree** — this repo's tree is edited by several parallel sessions. Commit **hunk-isolated**: `git add` only the exact files for the task, and if other changes are already staged, commit with an explicit pathspec (`git commit -m "..." -- <files>`) so you never sweep another session's staged work.
- After the code change, the running app needs a restart to load it: `POST /api/settings/restart-app` (not `Stop-Process`). The config flag itself is hot-reloadable once the new code is running.

---

### Task 1: Add the `zoom_before_click` config + context flag (plumbing, no behaviour yet)

This task makes the flag exist end-to-end — config field, context field, hot-reload registration, and factory threading — but nothing reads it yet. Deliverable: the flag parses, defaults off, reaches the live context, and is hot-reloadable.

**Files:**
- Modify: `jarvis/core/config.py:1393` (add field to `ComputerUseConfig`)
- Modify: `jarvis/harness/computer_use_context.py:48` (add field to `ComputerUseContext`) and `:198-209` (add to `_RELOADABLE_FIELDS`)
- Modify: `jarvis/brain/factory.py:1009` (thread the flag into the context)
- Test: `tests/unit/harness/test_cu_zoom_before_click_config.py` (new file)

**Interfaces:**
- Produces: `ComputerUseConfig.zoom_before_click: bool` (default `False`); `ComputerUseContext.zoom_before_click: bool` (default `False`); `"zoom_before_click"` present in `jarvis.harness.computer_use_context._RELOADABLE_FIELDS`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/test_cu_zoom_before_click_config.py`:

```python
"""Plumbing tests for the opt-in [computer_use].zoom_before_click flag."""
from __future__ import annotations

from jarvis.core.config import ComputerUseConfig
from jarvis.harness.computer_use_context import (
    ComputerUseContext,
    _RELOADABLE_FIELDS,
)


def test_config_zoom_before_click_defaults_off() -> None:
    assert ComputerUseConfig().zoom_before_click is False


def test_config_zoom_before_click_parses_true() -> None:
    assert ComputerUseConfig(zoom_before_click=True).zoom_before_click is True


def test_context_zoom_before_click_defaults_off() -> None:
    ctx = ComputerUseContext(
        vision_engine=None, brain_manager=None, tool_executor=None,
    )
    assert ctx.zoom_before_click is False


def test_zoom_before_click_is_hot_reloadable() -> None:
    # Listed in _RELOADABLE_FIELDS so a voice / Self-Mod toggle applies to the
    # next mission without an app restart (mirrors verify_after_each_step).
    assert "zoom_before_click" in _RELOADABLE_FIELDS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/harness/test_cu_zoom_before_click_config.py -v`
Expected: FAIL — `AttributeError`/`ValidationError` (field not defined) and the `_RELOADABLE_FIELDS` assertion fails.

- [ ] **Step 3: Add the config field**

In `jarvis/core/config.py`, immediately after `verify_after_each_step: bool = True` (line 1393), insert:

```python
    # Proactively zoom-refine each click target BEFORE clicking (default OFF).
    # When on AND the click action carries a `target`, the loop grabs a live
    # zoomed crop around the coarse point, re-locates the target inside it, and
    # only then clicks — and REFUSES to click (re-plans) when the target is not
    # in the crop, catching wrong-element clicks the pixel-diff verify accepts.
    # Internal screenshot crop only: nothing is shown on screen. Costs one extra
    # model call per targeted click, so it stays opt-in to protect the default
    # latency profile.
    zoom_before_click: bool = False
```

- [ ] **Step 4: Add the context field**

In `jarvis/harness/computer_use_context.py`, immediately after `verify_after_each_step: bool = True` (line 48), insert:

```python
    # Proactive zoom-before-click (opt-in, default OFF). See
    # ComputerUseConfig.zoom_before_click. Internal screenshot crop only —
    # nothing renders on screen.
    zoom_before_click: bool = False
```

- [ ] **Step 5: Register the field for hot-reload**

In `jarvis/harness/computer_use_context.py`, in the `_RELOADABLE_FIELDS` tuple (lines 198-209), add `"zoom_before_click",` after `"verify_after_each_step",`:

```python
_RELOADABLE_FIELDS: tuple[str, ...] = (
    "step_budget",
    "per_step_timeout_s",
    "think_timeout_cap_s",
    "image_max_bytes",
    "image_max_dimension",
    "settle_scale",
    "fast_step_model",
    "max_replans",
    "verify_after_each_step",
    "zoom_before_click",
    "announce_progress",
)
```

- [ ] **Step 6: Thread the flag into the context at boot**

In `jarvis/brain/factory.py`, in the `ComputerUseContext(...)` construction (around line 1009), after `verify_after_each_step=cu_cfg.verify_after_each_step,` add:

```python
                zoom_before_click=getattr(cu_cfg, "zoom_before_click", False),
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `pytest tests/unit/harness/test_cu_zoom_before_click_config.py -v`
Expected: PASS (4 passed).

- [ ] **Step 8: Verify the factory edit by reading it back**

Run: `git diff jarvis/brain/factory.py`
Expected: the diff shows exactly the one added `zoom_before_click=getattr(...)` line inside the `ComputerUseContext(...)` call, nothing else.

- [ ] **Step 9: Commit (hunk-isolated)**

```bash
git add jarvis/core/config.py jarvis/harness/computer_use_context.py jarvis/brain/factory.py tests/unit/harness/test_cu_zoom_before_click_config.py
git commit -m "feat(cu): add opt-in zoom_before_click config+context flag (plumbing)" -- jarvis/core/config.py jarvis/harness/computer_use_context.py jarvis/brain/factory.py tests/unit/harness/test_cu_zoom_before_click_config.py
```

---

### Task 2: Run the zoom-refine before the first click when opted in

Wire the flag into `_click_with_refine`: the existing refine pass additionally runs on the first attempt when `zoom_before_click` is on and the action has a `target`. All downstream verdict handling already exists (relocate → click; not-found + first-attempt + target → re-plan; failure → coarse click).

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py:2892-2904` (the gate inside `_click_with_refine`)
- Test: `tests/unit/harness/test_cu_click_refine.py` (extend: one fake-ctx param + five behaviour tests)

**Interfaces:**
- Consumes: `ComputerUseContext.zoom_before_click` (Task 1), read via `getattr(ctx, "zoom_before_click", False)`; the existing `_refine_click_point(ctx, observation, x, y, monitor_geom, *, user_goal, target, retry_note) -> tuple[bool, int, int] | None`; the existing `target = str(obj.get("target") or "").strip()` local (line 2876).
- Produces: nothing for later tasks (terminal task).

- [ ] **Step 1: Add a `zoom` switch to the test fake**

In `tests/unit/harness/test_cu_click_refine.py`, update `FakeCtx.__init__` (lines 162-175) to accept a `zoom` flag (keep the existing `verify` default so all current tests stay green):

```python
class FakeCtx:
    def __init__(self, brain: FakeBrain, executor: FakeExecutor, *,
                 verify: bool = True, zoom: bool = False) -> None:
        self.brain_manager = brain
        self.tool_executor = executor
        self.tools = {"click": FakeTool()}
        self.bus = None
        self.per_step_timeout_s = 5.0
        self.verify_after_each_step = verify
        # Opt-in proactive zoom-before-click (default off mirrors production).
        self.zoom_before_click = zoom
        # These tests isolate the LLM refine path; the Phase-2 UIA snap (which
        # would otherwise fire first on a verified miss and query the real host
        # accessibility tree) is turned off so the refine behaviour is tested in
        # isolation. The snap itself is covered by tests/unit/harness/test_cu_uia_snap.py.
        self.uia_click_fallback = False
```

- [ ] **Step 2: Write the five failing behaviour tests**

Append to `tests/unit/harness/test_cu_click_refine.py`:

```python
# ---------------------------------------------------------------------------
# Opt-in proactive zoom-before-click ([computer_use].zoom_before_click)
# ---------------------------------------------------------------------------


async def test_zoom_off_first_click_uses_coarse_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default (flag off): the first attempt clicks the model's coarse point with
    # no proactive refine call — the trust-first default is preserved.
    brain = FakeBrain()
    executor = FakeExecutor()
    _patch(monkeypatch, GrabQueue([b"pre", b"post-different"]))

    ok, _msg = await _click(
        FakeCtx(brain, executor, zoom=False),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert brain.requests == []  # no proactive refine on the default path


async def test_zoom_on_relocates_target_before_first_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Flag on + target: a proactive refine round runs BEFORE the first click and
    # re-locates the target at crop-norm (250,250) inside the 360px crop at
    # (320,320) -> abs (410,410). The FIRST click lands on the refined point.
    brain = FakeBrain(['{"found": true, "x": 250, "y": 250}'])
    executor = FakeExecutor()
    # grab order: refine crop, then verify pre, then verify post.
    _patch(monkeypatch, GrabQueue([b"refine-crop", b"pre", b"post-different"]))

    ok, _msg = await _click(
        FakeCtx(brain, executor, zoom=True),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(410, 410)]
    assert len(brain.requests) == 1  # exactly the proactive refine call


async def test_zoom_on_target_not_in_crop_refuses_to_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Flag on + target NOT visible in the zoom crop: the loop must NOT click
    # (the coarse estimate was so far off the named target is not even in the
    # crop) and returns a re-plan signal — the wrong-element guard.
    brain = FakeBrain(['{"found": false}'])
    executor = FakeExecutor()
    _patch(monkeypatch, GrabQueue([b"refine-crop"]))

    ok, msg = await _click(
        FakeCtx(brain, executor, zoom=True),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is False
    assert executor.clicks == []
    assert "re-plan" in msg


async def test_zoom_on_without_target_uses_coarse_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Flag on but the action carries NO target — nothing to confirm against, so
    # the proactive refine is skipped and the coarse point is clicked directly.
    brain = FakeBrain()
    executor = FakeExecutor()
    _patch(monkeypatch, GrabQueue([b"pre", b"post-different"]))

    ok, _msg = await _click(
        FakeCtx(brain, executor, zoom=True),
        {"action": "click", "x": 500, "y": 500},  # no "target"
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert brain.requests == []


async def test_zoom_on_crop_grab_fails_falls_back_to_coarse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Flag on + target, but the live crop grab fails (None): the proactive
    # refine degrades to None and the loop clicks the coarse point. Fail-safe.
    brain = FakeBrain()
    executor = FakeExecutor()
    # grab 1 (refine crop) -> None; grab 2 pre, grab 3 post.
    _patch(monkeypatch, GrabQueue([None, b"pre", b"post-different"]))

    ok, _msg = await _click(
        FakeCtx(brain, executor, zoom=True),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert brain.requests == []  # grab failed before any brain call
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `pytest tests/unit/harness/test_cu_click_refine.py -k zoom -v`
Expected: `test_zoom_off_*` and `test_zoom_on_without_target_*` and `test_zoom_on_crop_grab_fails_*` PASS already (flag unread → coarse path), but `test_zoom_on_relocates_*` FAILS (clicks `(500,500)` not `(410,410)`; `brain.requests == []`) and `test_zoom_on_target_not_in_crop_*` FAILS (`ok is True`, clicked `(500,500)`). These two failures prove the feature is not yet wired.

- [ ] **Step 4: Wire the proactive gate**

In `jarvis/harness/screenshot_only_loop.py`, inside `_click_with_refine`, replace the top of the attempt loop (lines 2892-2904) — currently:

```python
    for _attempt in range(_CLICK_MAX_ATTEMPTS):
        refined = None
        if clicked:
            # Trust-first (2026-06-10 latency plan Task 3): the refine pass is
            # a full LLM round-trip, and on the FIRST attempt it corrected the
            # model's point by <=5 px in live runs — pure cost (the executor
            # and the refiner see the same frame). Reserve it for retries
            # after a verified miss, where the zoomed live crop genuinely
            # re-locates the target.
            refined = await _refine_click_point(
                ctx, observation, x, y, monitor_geom,
                user_goal=user_goal, target=target, retry_note=retry_note,
            )
```

with:

```python
    for _attempt in range(_CLICK_MAX_ATTEMPTS):
        # Proactive zoom-before-click ([computer_use].zoom_before_click, opt-in,
        # default off): run the zoom-refine BEFORE the first click too, not only
        # after a verified miss. Needs a named target to confirm against;
        # without one it would only add a round-trip, so it falls through to the
        # coarse click. Internal screenshot crop only — nothing renders on
        # screen; on a not-found verdict the loop re-plans instead of clicking
        # the wrong element (handled below).
        proactive_zoom = (
            not clicked
            and bool(target)
            and getattr(ctx, "zoom_before_click", False)
        )
        refined = None
        if clicked or proactive_zoom:
            # Trust-first (2026-06-10 latency plan Task 3): the refine pass is
            # a full LLM round-trip, and on the FIRST attempt it corrected the
            # model's point by <=5 px in live runs — pure cost (the executor
            # and the refiner see the same frame). Reserve it for retries
            # after a verified miss UNLESS the operator opted into proactive
            # zoom above, where the zoomed live crop re-locates the target and
            # guards against a wrong-element click.
            refined = await _refine_click_point(
                ctx, observation, x, y, monitor_geom,
                user_goal=user_goal, target=target, retry_note=retry_note,
            )
```

- [ ] **Step 5: Run the full refine test file to verify all pass**

Run: `pytest tests/unit/harness/test_cu_click_refine.py -v`
Expected: PASS — all pre-existing refine tests stay green (they use `zoom=False`) and the five new `zoom` tests pass.

- [ ] **Step 6: Run the broader Computer-Use harness suite for regressions**

Run: `pytest tests/unit/harness/ -q`
Expected: PASS (no regression in `test_cu_uia_snap.py`, `test_cu_loop_robustness.py`, `test_context_reload.py`, etc.).

- [ ] **Step 7: Commit (hunk-isolated)**

```bash
git add jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_cu_click_refine.py
git commit -m "feat(cu): proactively zoom-refine before the first click when opted in" -- jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_cu_click_refine.py
```

---

## Manual verification (after both tasks, optional live check)

1. Restart the running app: `POST /api/settings/restart-app`.
2. Enable the flag (voice "schalte Zoom vor dem Klick ein", or set `[computer_use].zoom_before_click = true` via the config path / Self-Mod). It hot-reloads — no second restart needed.
3. Run a Computer-Use task that previously mis-clicked a small/dense control. Confirm: nothing visibly zooms on screen; the click lands on the intended element; and a deliberately-wrong target description produces a re-plan rather than a wrong click.

## Self-review notes (author check against the spec)

- **Spec coverage:** config flag (Task 1) ✓; context threading + hot-reload (Task 1) ✓; the one behavioural gate change (Task 2) ✓; three verdict outcomes — relocate/confirm, not-found→re-plan, failure→coarse (Task 2 tests 2/3/5) ✓; target-gating (Task 2 test 4) ✓; default-off regression (Task 2 test 1) ✓; invisible-on-screen + no-app-side-effects (satisfied by reusing the passive crop path — no new rendering or input code added) ✓; cross-platform (no new platform code; capture + UIA-snap unchanged) ✓.
- **Placeholder scan:** none — every code/test step shows complete code and an exact command with expected result.
- **Type consistency:** `zoom_before_click: bool` used identically across config, context, `_RELOADABLE_FIELDS`, factory, and the loop's `getattr(ctx, "zoom_before_click", False)`; `_refine_click_point` call signature matches the existing definition.
- **Out of scope (unchanged):** no adaptive/confidence gating, no Set-of-Marks revival, no browser-DOM path (Approaches B/C, future iterations).
