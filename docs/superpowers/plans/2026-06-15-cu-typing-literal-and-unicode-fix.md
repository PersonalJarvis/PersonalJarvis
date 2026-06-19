# Computer-Use Typing Fix — Literal Dictation + Layout-Independent Keystrokes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Computer-Use (CU) feature type the user's *literal* dictated text — exactly the characters they asked for — into any app, including webview/Tauri terminals under a non-US keyboard layout.

**Architecture:** Two independent root causes, fixed independently and non-destructively. (1) *Mechanics:* the live typing transport is `pyautogui.typewrite`, which injects layout-dependent virtual-keys at 20 ms spacing and garbles characters into a Tauri/webview input under German QWERTZ; the robust native `KEYEVENTF_UNICODE` `SendInput` path already exists in the same file but is demoted to a fallback that never runs. We promote it to the primary Windows path and add a short focus-settle before typing. (2) *Semantics:* the CU executor model freelances "say X in a terminal" into the shell idiom `echo X`; nothing in code injects "echo". We add an explicit "literal dictation" rule to the executor (and planner) prompt that names the failure and preserves genuinely interpretive cases (search/URL).

**Tech Stack:** Python 3.11, Win32 `SendInput` (`ctypes`), `pyautogui` (fallback only on Windows; primary on non-Windows), pytest (`asyncio_mode=auto`), the in-process `computer-use` harness.

---

## Background — root cause (evidence from a two-agent review, 2026-06-15)

Live failure: voice goal *"Can you prompt the terminal at the top left and say hello hello hello and submit it?"* → CU typed **`echo hello hello hello`** with **character errors**. Two distinct bugs:

**Bug 1 — garbled characters (mechanics).**
- Live typing path: `jarvis/plugins/tool/type_text.py:121-123` runs `pyautogui.typewrite(text, interval=delay_s)` because `pyautogui==0.9.54` imports successfully; the native `KEYEVENTF_UNICODE` path (`_send_text_windows`, `type_text.py:18-77`) only runs in the import-failure `except` and is therefore **dormant**.
- The CU loop calls the tool with only `{"text": ...}` (`jarvis/harness/screenshot_only_loop.py:1977-1981`) → the 20 ms schema default (`type_text.py:100`) is what runs.
- `typewrite` injects a layout-dependent **virtual-key** (no scancode, no `KEYEVENTF_UNICODE`); a Chromium/webview terminal (the "BridgeSpace" Tauri app) re-resolves it against the active **German QWERTZ** layout (`0x0407`) less faithfully than a native edit control → dropped/doubled/wrong chars. (The y↔z QWERTZ swap is **ruled out** — none of `h/e/l/o/space` are layout-sensitive; the corruption is the VK-vs-Unicode path, not a character swap.)
- Compounding: there is **no focus-settle between the focusing click and the type** — the batch loop only settles after `open_app` (`screenshot_only_loop.py:3021-3024`); the click tool returns without waiting for focus (`jarvis/plugins/tool/click.py:148-181`). Typing within ~2 ms of the focusing click drops leading characters.

**Bug 2 — the invented `echo` (semantics).**
- **No code injects "echo"** — `grep -i echo jarvis/harness/` is empty; the `type` text is passed byte-for-byte (`screenshot_only_loop.py:349-353` parse, `1972-1991` execute). The vision model authored `{"action":"type","text":"echo hello hello hello"}` itself.
- The executor `_SYSTEM_PROMPT` (`screenshot_only_loop.py:146-279`) is framed purely around goal-accomplishment ("advance the user goal", "GOAL COMPLETION DISCIPLINE") with **no instruction to type the user's literal words**. Cued by the verb "say" + a terminal, the model supplied the shell idiom.
- The Enter/submit path (`screenshot_only_loop.py:1993-2017`, `key` → `hotkey`) is **correct** and must be preserved.

**Verified non-breaking surface:** no test pins `pyautogui` or the 20 ms interval; no test asserts `_send_text_windows` is skipped. The fragile assertions to respect are: `tests/unit/harness/test_native_computer_use.py:63-94` (the `type_text_at` macro must expand to exactly `[click, type]` — so the settle goes in the loop, **not** the mapping), `tests/unit/harness/test_cu_planner_navigation_discipline.py:195-217` (planner prompt substrings + **no "judge"/"feasibility"**), and `tests/unit/harness/test_cu_click_refine.py:66-69` (`'"target"'` must stay in `_SYSTEM_PROMPT` — our edits only append).

## What we preserve (non-goals)

- Do **not** remove or gate Enter-after-type — "submit it" must still press Enter.
- Do **not** make CU dumber for interpretive goals — `search for X` → type query, `go to gmail` → type URL must still work.
- Do **not** touch the `type_text_at` → `[click, type]` mapping (breaks `test_native_computer_use.py`).
- Do **not** add the words `judge` or `feasibility` to any executor/planner prompt (the test fakes route done/fail judges on those words).
- Keep the cloud-first doctrine intact: the native Unicode path is Windows-only by design; non-Windows keeps `pyautogui`; CU stays a `[desktop]` extra.

## File structure (what changes)

- **Modify** `jarvis/plugins/tool/type_text.py` — promote native `KEYEVENTF_UNICODE` `SendInput` to the primary Windows typing path; keep `pyautogui` as Windows fallback + non-Windows primary. (Bug 1, transport.)
- **Modify** `jarvis/harness/screenshot_only_loop.py` — add `_PRE_TYPE_SETTLE_S` constant + a settle before the `type` dispatch (Bug 1, focus); add a "LITERAL DICTATION" rule to `_SYSTEM_PROMPT` and to `_PLANNER_SYSTEM_PROMPT` (Bug 2). All append-only.
- **Create** `tests/unit/plugins/tool/test_type_text.py` — transport-selection regression (native preferred; fallback on failure).
- **Create** `tests/unit/harness/test_cu_literal_dictation.py` — prompt-content guards for the executor + planner literal-dictation rule.
- **Modify** `tests/unit/harness/test_screenshot_only_loop.py` — add a settle-before-type dispatch test.

> **Test runner note:** use `py -3.11 -m pytest ...` (the project's 3.11 interpreter), **not** the bare `python` on PATH (a different venv). Suite runs with `asyncio_mode=auto`, so `async def test_...` needs no decorator.

---

### Task 1: Layout-independent typing — promote native Unicode `SendInput` (Bug 1, transport)

**Files:**
- Modify: `jarvis/plugins/tool/type_text.py` (add a module logger; rewrite `TypeTextTool.execute`, lines 98-125)
- Test: `tests/unit/plugins/tool/test_type_text.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/plugins/tool/test_type_text.py`:

```python
"""Transport-selection guard for TypeTextTool.

Regression for the CU typo bug (2026-06-15): on Windows the tool must prefer the
native KEYEVENTF_UNICODE SendInput path (layout-independent, exact codepoints)
over pyautogui's layout-dependent virtual-key typing, which garbled characters
into a Tauri/webview terminal under a German QWERTZ layout. pyautogui stays a
fallback when the native path fails (and the primary on non-Windows).
"""
from __future__ import annotations

import sys
import types

from jarvis.plugins.tool import type_text as tt
from jarvis.plugins.tool.type_text import TypeTextTool


class _Ctx:
    user_utterance = "type hello"


def _install_fake_pyautogui(monkeypatch, calls):
    fake = types.ModuleType("pyautogui")

    def _typewrite(text, interval=0.0):
        calls["pyautogui_text"] = text

    fake.typewrite = _typewrite
    monkeypatch.setitem(sys.modules, "pyautogui", fake)


async def test_windows_prefers_native_unicode_over_pyautogui(monkeypatch):
    calls = {"native_text": None, "pyautogui_text": None}

    def _fake_native(text, delay_s):
        calls["native_text"] = text

    monkeypatch.setattr(tt.os, "name", "nt")
    monkeypatch.setattr(tt, "_send_text_windows", _fake_native)
    _install_fake_pyautogui(monkeypatch, calls)

    res = await TypeTextTool().execute({"text": "hello hello hello"}, _Ctx())

    assert res.success is True
    assert calls["native_text"] == "hello hello hello"
    assert calls["pyautogui_text"] is None  # native won; pyautogui untouched
    assert "Unicode" in (res.output or "")


async def test_windows_falls_back_to_pyautogui_when_native_fails(monkeypatch):
    calls = {"pyautogui_text": None}

    def _boom(text, delay_s):
        raise OSError("SendInput returned 0")

    monkeypatch.setattr(tt.os, "name", "nt")
    monkeypatch.setattr(tt, "_send_text_windows", _boom)
    _install_fake_pyautogui(monkeypatch, calls)

    res = await TypeTextTool().execute({"text": "abc"}, _Ctx())

    assert res.success is True
    assert calls["pyautogui_text"] == "abc"


async def test_empty_text_is_rejected(monkeypatch):
    res = await TypeTextTool().execute({"text": ""}, _Ctx())
    assert res.success is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/plugins/tool/test_type_text.py -v`
Expected: `test_windows_prefers_native_unicode_over_pyautogui` FAILS — current code imports `pyautogui` first and calls `typewrite`, so `calls["native_text"]` stays `None` and `calls["pyautogui_text"]` is set.

- [ ] **Step 3: Add a module logger**

In `jarvis/plugins/tool/type_text.py`, after the existing imports block (after `from jarvis.core.protocols import ExecutionContext, ToolResult`), add:

```python
import logging

log = logging.getLogger(__name__)
```

(Place `import logging` with the other stdlib imports near the top; keep import order: stdlib `asyncio, logging, os, time` then the `jarvis.core.protocols` import.)

- [ ] **Step 4: Rewrite `execute` to prefer native Unicode on Windows**

Replace the entire `execute` method body (`type_text.py:98-125`) with:

```python
    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        text = args.get("text") or ""
        delay_s = float(args.get("delay_s", 0.02))
        if not text:
            return ToolResult(success=False, output=None, error="text missing")
        # Windows: prefer the native KEYEVENTF_UNICODE SendInput path. It injects
        # the exact Unicode codepoint regardless of the active keyboard layout
        # (this machine runs German QWERTZ) and is far more robust into
        # webview/Tauri text inputs than pyautogui's layout-dependent virtual-key
        # path, which garbled characters typed into the BridgeSpace Tauri terminal
        # (CU typo bug 2026-06-15). pyautogui stays a best-effort fallback.
        if os.name == "nt":
            try:
                await asyncio.to_thread(_send_text_windows, text, delay_s)
                return ToolResult(
                    success=True,
                    output=f"Typed {len(text)} chars via native Windows Unicode input",
                )
            except Exception as native_exc:  # noqa: BLE001
                log.warning(
                    "native Unicode SendInput failed, falling back to pyautogui: %r",
                    native_exc,
                )
        try:
            import pyautogui
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"text input unavailable: pyautogui import failed: {exc}",
            )
        try:
            pyautogui.typewrite(text, interval=delay_s)
            return ToolResult(success=True, output=f"Typed {len(text)} chars")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/unit/plugins/tool/test_type_text.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Confirm no CU/native test regressed**

Run: `py -3.11 -m pytest tests/unit/harness/test_native_computer_use.py tests/unit/harness/test_screenshot_only_loop.py -q`
Expected: PASS (no test pins `pyautogui`; mapping unchanged).

- [ ] **Step 7: Lint**

Run: `py -3.11 -m ruff check jarvis/plugins/tool/type_text.py`
Expected: no new findings on touched lines.

- [ ] **Step 8: Commit**

```bash
git add jarvis/plugins/tool/type_text.py tests/unit/plugins/tool/test_type_text.py
git commit -m "fix(cu): type via native Unicode SendInput on Windows (layout-independent keystrokes)"
```

---

### Task 2: Focus-settle before typing (Bug 1, leading-char drop)

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py` (add `_PRE_TYPE_SETTLE_S` near `_ACT_TIMEOUT_S` at line 576; add a settle in the `type` branch at line 1972-1975)
- Test: `tests/unit/harness/test_screenshot_only_loop.py` (add one test)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/harness/test_screenshot_only_loop.py` (reuse the module's existing `_FakeExecutor` and `_FakeCtx`; the `type_text` tool can be any sentinel object because `_FakeExecutor` records the call and returns success):

```python
def test_execute_type_settles_before_dispatch(monkeypatch) -> None:
    """The CU loop must pause briefly before typing so a freshly-focused
    webview/Tauri input is listening — otherwise leading characters drop
    (CU typo bug 2026-06-15). The settle is awaited BEFORE the type is sent."""
    import jarvis.harness.screenshot_only_loop as loop

    sleeps: list[float] = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(loop.asyncio, "sleep", _fake_sleep)

    type_tool = object()
    executor = _FakeExecutor()
    ctx = _FakeCtx(executor=executor, tools={"type_text": type_tool})

    success, _message = asyncio.run(
        _execute_action(
            {"action": "type", "text": "hello hello hello"},
            ctx,
            trace_id=None,
            user_goal="type hello hello hello into the terminal",
        ),
    )

    assert success is True
    assert sleeps and sleeps[0] == loop._PRE_TYPE_SETTLE_S
    sent_tool, sent_args, _utterance = executor.calls[0]
    assert sent_tool is type_tool
    assert sent_args["text"] == "hello hello hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/harness/test_screenshot_only_loop.py::test_execute_type_settles_before_dispatch -v`
Expected: FAIL with `AttributeError: module 'jarvis.harness.screenshot_only_loop' has no attribute '_PRE_TYPE_SETTLE_S'`.

- [ ] **Step 3: Add the settle constant**

In `jarvis/harness/screenshot_only_loop.py`, immediately after `_ACT_TIMEOUT_S = 5.0` (line 576), add:

```python
#: Brief pause before a ``type`` action so a freshly-focused webview/Tauri text
#: input (e.g. the BridgeSpace terminal) is actually listening before keystrokes
#: arrive. Without it the focusing click and the type land within ~2 ms and the
#: first characters are dropped (CU typo bug 2026-06-15).
_PRE_TYPE_SETTLE_S = 0.15
```

- [ ] **Step 4: Await the settle in the `type` branch**

In `_execute_action`, in the `if action == "type":` block (line 1972-1975), insert the settle right after the tool-wired guard, before the `try:`:

```python
    if action == "type":
        tool = tools.get("type_text")
        if tool is None:
            return False, "type_text tool not wired"
        # Let a freshly-focused input settle before typing (anti leading-char
        # drop on webview/Tauri terminals — CU typo bug 2026-06-15).
        await asyncio.sleep(_PRE_TYPE_SETTLE_S)
        try:
            res = await asyncio.wait_for(
                executor.execute(
                    tool, {"text": str(obj.get("text", ""))},
                    user_utterance="computer-use", trace_id=trace_id,
                ),
                timeout=_ACT_TIMEOUT_S,
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/unit/harness/test_screenshot_only_loop.py::test_execute_type_settles_before_dispatch -v`
Expected: PASS.

- [ ] **Step 6: Confirm the broader CU loop + mapping suites stay green**

Run: `py -3.11 -m pytest tests/unit/harness/test_screenshot_only_loop.py tests/unit/harness/test_native_computer_use.py -q`
Expected: PASS (the `type_text_at` macro is untouched, so `test_type_text_at_*` stays green).

- [ ] **Step 7: Lint**

Run: `py -3.11 -m ruff check jarvis/harness/screenshot_only_loop.py`
Expected: no new findings on touched lines.

- [ ] **Step 8: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_screenshot_only_loop.py
git commit -m "fix(cu): settle 150ms before typing so webview inputs don't drop leading chars"
```

---

### Task 3: "Literal dictation" rule in the executor prompt (Bug 2, the invented `echo`)

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py` (`_SYSTEM_PROMPT`, insert after the type-into-a-field bullet at line 208-209)
- Test: `tests/unit/harness/test_cu_literal_dictation.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/test_cu_literal_dictation.py`:

```python
"""Guard the CU prompts that stop the model inventing shell commands.

Regression for the live bug (2026-06-15): the goal "say hello hello hello" in a
terminal was typed as "echo hello hello hello" — the executor prompt had no rule
to type the user's literal words. These are prompt-content guards (model output
itself is non-deterministic; the prompt instruction is the testable contract).
"""
from __future__ import annotations

from jarvis.harness.screenshot_only_loop import (
    _PLANNER_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
)


def test_executor_prompt_carries_literal_dictation_rule() -> None:
    low = _SYSTEM_PROMPT.lower()
    assert "literal dictation" in low
    assert "verbatim" in low
    assert "echo" in low  # the specific failure is named so it can't recur silently


def test_planner_prompt_carries_literal_dictation_rule() -> None:
    low = _PLANNER_SYSTEM_PROMPT.lower()
    assert "literal dictation" in low
    assert "verbatim" in low
    assert "echo" in low


def test_literal_dictation_does_not_break_judge_routing_keywords() -> None:
    # The added text must not introduce the words the test fakes / live loop use
    # to recognise the done-judge / fail-feasibility prompts.
    for prompt in (_SYSTEM_PROMPT, _PLANNER_SYSTEM_PROMPT):
        low = prompt.lower()
        assert "judge" not in low
        assert "feasibility" not in low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/harness/test_cu_literal_dictation.py -v`
Expected: `test_executor_prompt_carries_literal_dictation_rule` and `test_planner_prompt_carries_literal_dictation_rule` FAIL (rule not present yet). `test_literal_dictation_does_not_break_judge_routing_keywords` should already PASS (and must stay passing after the edits).

> If `test_literal_dictation_does_not_break_judge_routing_keywords` FAILS at this step, the existing `_SYSTEM_PROMPT` already contains "judge"/"feasibility" — STOP and adjust the assertion to scope only the added block; do not proceed assuming a clean baseline.

- [ ] **Step 3: Add the rule to `_SYSTEM_PROMPT`**

In `jarvis/harness/screenshot_only_loop.py`, immediately after the existing bullet at line 208-209 (the string ending `"...Never type blindly into an unfocused screen.\n"`), insert a new bullet string:

```python
    "* LITERAL DICTATION: when the goal tells you to type, say, write, or enter "
    "specific words (e.g. 'type hello hello hello', 'say X', 'write Y'), the "
    "``type`` action's text MUST be exactly those words -- copy them verbatim. "
    "Do NOT add, wrap, or transform them into a shell command or any prefix; in "
    "particular NEVER prepend 'echo' or surround them with quotes. Only compute "
    "different text when the goal explicitly asks you to (e.g. 'search for X' -> "
    "type the query X; 'go to gmail' -> type the URL).\n"
```

(This is one more adjacent string literal in the existing implicit-concatenation block — keep the surrounding `(` ... `)` structure intact. Do not touch the `"target"` bullet at line 205-207; `test_cu_click_refine.py:66-69` asserts it stays.)

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/unit/harness/test_cu_literal_dictation.py::test_executor_prompt_carries_literal_dictation_rule tests/unit/harness/test_cu_literal_dictation.py::test_literal_dictation_does_not_break_judge_routing_keywords -v`
Expected: both PASS. (The planner test still fails until Task 4.)

- [ ] **Step 5: Confirm the executor-prompt-dependent tests stay green**

Run: `py -3.11 -m pytest tests/unit/harness/test_cu_click_refine.py -q`
Expected: PASS (`'"target"'` still present in `_SYSTEM_PROMPT`).

- [ ] **Step 6: Lint**

Run: `py -3.11 -m ruff check jarvis/harness/screenshot_only_loop.py`
Expected: no new findings.

- [ ] **Step 7: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_cu_literal_dictation.py
git commit -m "fix(cu): executor prompt must type dictated text verbatim (no invented 'echo')"
```

---

### Task 4: Reinforce literal dictation in the planner prompt (Bug 2, multi-step path)

**Files:**
- Modify: `jarvis/harness/screenshot_only_loop.py` (`_PLANNER_SYSTEM_PROMPT`, append before the closing `)` at line 1434-1435)
- Test: `tests/unit/harness/test_cu_literal_dictation.py` (already written in Task 3 — `test_planner_prompt_carries_literal_dictation_rule`)

> *Why also the planner:* a multi-step goal ("terminal … and submit") is decomposed by `_PLANNER_SYSTEM_PROMPT`, and the plan is fed into every executor turn. A literal-text step in the plan reinforces the executor's verbatim behavior.

- [ ] **Step 1: Confirm the planner test currently fails**

Run: `py -3.11 -m pytest tests/unit/harness/test_cu_literal_dictation.py::test_planner_prompt_carries_literal_dictation_rule -v`
Expected: FAIL (rule not in `_PLANNER_SYSTEM_PROMPT` yet).

- [ ] **Step 2: Append the rule to `_PLANNER_SYSTEM_PROMPT`**

In `jarvis/harness/screenshot_only_loop.py`, add one final bullet string to `_PLANNER_SYSTEM_PROMPT` — immediately after the line ending `"...search box containing one of the goal's words.\n"` (line 1434) and before the closing `)` (line 1435):

```python
    "* LITERAL DICTATION: when the goal dictates specific words to type, say, "
    "write, or enter (e.g. 'type hello hello hello', 'say X'), the typing "
    "step's intent MUST carry those exact words verbatim -- never wrap them in "
    "a shell command or prepend 'echo'. This is distinct from a search topic: "
    "only 'search for <topic>' yields a typed query.\n"
```

- [ ] **Step 3: Run the literal-dictation suite to verify it passes**

Run: `py -3.11 -m pytest tests/unit/harness/test_cu_literal_dictation.py -v`
Expected: all 3 PASS.

- [ ] **Step 4: Confirm the navigation-discipline guards stay green (critical — same prompt)**

Run: `py -3.11 -m pytest tests/unit/harness/test_cu_planner_navigation_discipline.py -v`
Expected: PASS — the existing substrings ("navigation vs search", "search box", "do not type", `'news'`, `'latest'`, `'post'`) are untouched, and `test_planner_discipline_avoids_judge_routing_keywords` still holds (the added text contains no "judge"/"feasibility").

- [ ] **Step 5: Lint**

Run: `py -3.11 -m ruff check jarvis/harness/screenshot_only_loop.py`
Expected: no new findings.

- [ ] **Step 6: Commit**

```bash
git add jarvis/harness/screenshot_only_loop.py tests/unit/harness/test_cu_literal_dictation.py
git commit -m "fix(cu): planner prompt reinforces verbatim dictation for multi-step type goals"
```

---

## Final verification (after all 4 tasks)

- [ ] **Full CU + typing test surface green:**

```bash
py -3.11 -m pytest \
  tests/unit/plugins/tool/test_type_text.py \
  tests/unit/harness/test_cu_literal_dictation.py \
  tests/unit/harness/test_screenshot_only_loop.py \
  tests/unit/harness/test_native_computer_use.py \
  tests/unit/harness/test_cu_planner_navigation_discipline.py \
  tests/unit/harness/test_cu_click_refine.py \
  tests/unit/harness/test_cu_loop_robustness.py \
  tests/unit/harness/test_action_registry.py \
  -q
```
Expected: all PASS.

- [ ] **CI language policy** — confirm every new line is English (the `language-policy` gate blocks newly-added German). The new strings above are English; `type_text.py`'s existing German docstring/`"text fehlt"` is grandfathered but the rewritten error is now `"text missing"`.

- [ ] **Restart the live app** so the editable install reloads the harness:
  `POST /api/settings/restart-app` (do **not** `Stop-Process` — Access Denied under the tray `pythonw.exe`).

- [ ] **Live voice re-drive (maintainer mic required — the typed path ≠ the voice pipeline):**
  Say: *"open the terminal and type hello hello hello and submit it."*
  Expected: the terminal receives the literal characters **`hello hello hello`** (no `echo`, no typos), then Enter submits. Repeat 2-3× to confirm the intermittent typo is gone.

- [ ] **Regression smoke for interpretive cases** (must NOT have regressed):
  - *"open Chrome and search YouTube for lo-fi beats"* → types `lo-fi beats` into the search box, presses Enter (a computed query, not literal echo of the sentence).
  - *"open Notepad and type Dear Sir"* → types `Dear Sir` verbatim.

## Risk & rollback

- **Native Unicode primary (Task 1):** the one behavior to watch live — a rare app treats `KEYEVENTF_UNICODE` events as a *paste* rather than per-key input. Terminals (incl. webview terminals) accept Unicode input fine, and `pyautogui` remains the fallback if `_send_text_windows` raises. If a specific app misbehaves, the change is a single localized branch and trivially revertible; consider an app-scoped opt-out rather than reverting globally. `type_text` is a general tool — this improvement applies uniformly to every Windows typing flow, not just CU.
- **Settle (Task 2):** adds 150 ms per type; negligible against the multi-second screenshot loop. Tune `_PRE_TYPE_SETTLE_S` if a slow app still drops leading chars.
- **Prompt rules (Tasks 3-4):** append-only; cannot remove existing guidance. They explicitly preserve interpretive cases (search/URL), so they do not make CU dumber.
- **Deferred / optional hardening (not in this plan, only if the above proves insufficient in live re-drive):** raise the per-key interval for webview targets; clipboard-paste mode for long known-safe text (save/restore clipboard); read-back OCR verification after type with a clear-and-retry. Each adds latency or clipboard side effects, so they are intentionally out of the minimal non-breaking fix set.

## Self-review

- **Spec coverage:** Bug 1 transport → Task 1; Bug 1 focus → Task 2; Bug 2 executor → Task 3; Bug 2 planner → Task 4. Submit/Enter preserved (untouched). ✓
- **Placeholder scan:** every code/edit step shows the exact code and exact `py -3.11 -m pytest` command with expected result. ✓
- **Type/name consistency:** `_PRE_TYPE_SETTLE_S`, `_send_text_windows`, `_SYSTEM_PROMPT`, `_PLANNER_SYSTEM_PROMPT`, `TypeTextTool.execute` used identically across tasks; the settle test reads `loop._PRE_TYPE_SETTLE_S` exactly as defined in Task 2. ✓
