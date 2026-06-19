# Voice Continuation Recombine While Thinking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the user keeps talking after an utterance was already dispatched to the brain, abort the half-formed answer and re-think the *combined* sentence as one turn — instead of dropping the earlier half as a fresh, context-less message.

**Architecture:** Three cooperating units. (A) A small, pure `ContinuationWindow` remembers the last dispatched text and decides whether a fast-follow utterance is a continuation. (C) The pipeline prepends the remembered text to the next utterance and re-dispatches as one turn, dropping the prior committed turn from history if needed. (B) A thinking-phase interrupt monitor — built by reusing the proven `_barge_monitor` — cancels the in-flight brain turn the moment the user speaks during thinking, so the truncated answer is never voiced and never committed to history (cancel-before-commit, verified at `manager.py:4116-4117` + `4345-4351`).

**Tech Stack:** Python 3.11, asyncio, Pydantic v2 config, pytest (`asyncio_mode=auto`), Silero VAD. Spec: `docs/superpowers/specs/2026-06-16-voice-continuation-recombine-while-thinking-design.md`.

**Conventions for this repo:**
- Run pytest with the full interpreter, NOT the Hermes venv `python` (it has no pytest): `C:\Program Files\Python311\python.exe -m pytest ...`.
- Pipeline tests build the object via `SpeechPipeline.__new__(SpeechPipeline)` and set only the attributes under test (existing pattern, see the `getattr`-default notes throughout `pipeline.py`). New per-turn pipeline state MUST be read with `getattr(self, "...", default)` so bare instances keep working.
- Artifacts are English (code, comments, docstrings, commit messages) per `CLAUDE.md`.
- The working tree is SHARED with parallel sessions. Commit **pathspec-scoped** (only the files each task lists) — never `git add -A`.
- Do not push. Do not restart the app as part of a task; the go-live restart is called out at the end.

---

## File Structure

- **Create** `jarvis/speech/continuation_window.py` — the `ContinuationWindow` helper (Unit A). Pure, stdlib-only, deterministic (clock injected), fail-open. Sibling of `continuation_buffer.py`.
- **Create** `tests/unit/speech/test_continuation_window.py` — unit tests for the window.
- **Modify** `jarvis/brain/manager.py` — add `drop_last_turn(expected_user_text)` (Unit C history hygiene).
- **Create** `tests/unit/brain/test_drop_last_turn.py` — unit tests for `drop_last_turn`.
- **Modify** `jarvis/core/config.py` (`VoiceConfig`, ~line 1477) — three new `[voice]` fields.
- **Modify** `tests/unit/` config test (or create `tests/unit/core/test_voice_continuation_config.py`) — default-load assertion.
- **Modify** `jarvis/speech/pipeline.py` — instantiate the window, recombine helper (Unit C), arm/idle hooks (Unit A), thinking-interrupt monitor (Unit B), barged-empty guard, clear-on-hangup/cancel.
- **Create** `tests/unit/speech/test_continuation_recombine.py` — pipeline-level recombine + arm tests (bare instance).
- **Create** `tests/unit/speech/test_thinking_interrupt_monitor.py` — Unit B stall-guard monitor tests.

---

## Task 1: `ContinuationWindow` helper (Unit A)

**Files:**
- Create: `jarvis/speech/continuation_window.py`
- Test: `tests/unit/speech/test_continuation_window.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/speech/test_continuation_window.py
"""Unit tests for ContinuationWindow (voice continuation recombine, Unit A)."""
from __future__ import annotations

from jarvis.speech.continuation_window import ContinuationWindow


class FakeClock:
    def __init__(self) -> None:
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns

    def advance_ms(self, ms: int) -> None:
        self.now_ns += ms * 1_000_000


def test_fresh_dispatch_arms_window_in_flight():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    assert not w.is_armed
    w.note_dispatch("ich moechte nach", continued=False)
    assert w.is_armed
    assert w.text == "ich moechte nach"


def test_recombine_while_in_flight_joins_prior_and_new():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    w.note_dispatch("ich moechte nach", continued=False)
    # deadline is None (turn in flight) -> always active
    assert w.try_recombine("Griechenland") == "ich moechte nach Griechenland"


def test_recombine_within_grace_after_idle():
    clk = FakeClock()
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=clk)
    w.note_dispatch("ich moechte nach", continued=False)
    w.mark_idle()  # answer finished -> grace countdown starts
    clk.advance_ms(2000)  # within grace
    assert w.try_recombine("Griechenland") == "ich moechte nach Griechenland"


def test_recombine_after_grace_expires_returns_none_and_clears():
    clk = FakeClock()
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=clk)
    w.note_dispatch("ich moechte nach", continued=False)
    w.mark_idle()
    clk.advance_ms(3000)  # past grace
    assert w.try_recombine("Griechenland") is None
    assert not w.is_armed


def test_chain_cap_stops_merging_after_max_fragments():
    w = ContinuationWindow(grace_ms=9999, max_chain=3, clock=FakeClock())
    w.note_dispatch("a", continued=False)          # chain=1
    assert w.try_recombine("b") == "a b"
    w.note_dispatch("a b", continued=True)          # chain=2
    assert w.try_recombine("c") == "a b c"
    w.note_dispatch("a b c", continued=True)        # chain=3
    # 4th fragment would exceed max_chain=3 -> no merge, window cleared
    assert w.try_recombine("d") is None
    assert not w.is_armed


def test_clear_disarms():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    w.note_dispatch("x", continued=False)
    w.clear()
    assert not w.is_armed
    assert w.try_recombine("y") is None


def test_recombine_when_unarmed_returns_none():
    w = ContinuationWindow(grace_ms=2500, max_chain=3, clock=FakeClock())
    assert w.try_recombine("anything") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/test_continuation_window.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.speech.continuation_window'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# jarvis/speech/continuation_window.py
"""ContinuationWindow — re-attach a fast-follow utterance to the in-flight turn.

Sibling of :mod:`jarvis.speech.continuation_buffer`. Where ContinuationBuffer
coalesces a *syntactically* open fragment BEFORE dispatch, ContinuationWindow
covers the case the maintainer reported (2026-06-16): the user keeps talking
while the brain is ALREADY thinking/speaking. The pipeline aborts the
half-formed answer (Unit B) and, on the next utterance, prepends the
just-dispatched text so the whole sentence is re-thought as ONE turn.

Stdlib-only, deterministic (clock injected), fail-open. The window holds:

* ``text``        — the last dispatched user text, eligible to be extended.
* ``chain``       — fragments coalesced into the current window (bounded).
* ``deadline_ns`` — ``None`` while the arming turn is still in flight (always
                    active); a wall-clock deadline once the turn went idle
                    (grace countdown). Expiry is checked lazily on the next
                    ``try_recombine`` — never via a background timer that could
                    fire across turns (BUG-032 watchdog-class avoidance).

Design contract: ``try_recombine`` is NON-destructive on success (it leaves the
window armed with the prior text); the pipeline overwrites the text via
``note_dispatch`` only once it actually commits the combined turn to the brain.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

_DEFAULT_GRACE_MS: Final[int] = 2500
_DEFAULT_MAX_CHAIN: Final[int] = 3


class ContinuationWindow:
    """Tracks the last dispatched utterance so a continuation can re-attach."""

    def __init__(
        self,
        *,
        grace_ms: int = _DEFAULT_GRACE_MS,
        max_chain: int = _DEFAULT_MAX_CHAIN,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if grace_ms < 0:
            raise ValueError("grace_ms must be >= 0")
        if max_chain < 1:
            raise ValueError("max_chain must be >= 1")
        self._grace_ns = int(grace_ms) * 1_000_000
        self._max_chain = int(max_chain)
        self._clock = clock or time.monotonic_ns
        self._text: str = ""
        self._chain: int = 0
        self._deadline_ns: int | None = None

    @property
    def text(self) -> str:
        return self._text

    @property
    def is_armed(self) -> bool:
        return self._chain > 0

    def note_dispatch(self, text: str, *, continued: bool) -> None:
        """Record a turn that just committed to the brain.

        ``continued`` marks whether THIS dispatch was itself a recombine
        (chain grows) or a fresh turn (chain resets to 1). The window becomes
        'in flight' (no deadline) until ``mark_idle`` starts the grace countdown.
        """
        self._text = text.strip()
        self._chain = (self._chain + 1) if continued else 1
        self._deadline_ns = None

    def mark_idle(self) -> None:
        """The armed turn finished (answer spoken or aborted): start grace."""
        if self.is_armed:
            self._deadline_ns = self._clock() + self._grace_ns

    def try_recombine(self, new_text: str) -> str | None:
        """Return ``prior + new`` if a continuation is live, else ``None``.

        Non-destructive on success (window stays armed with the prior text).
        Expired or over-cap -> clears and returns ``None`` (fresh turn).
        """
        if not self.is_armed:
            return None
        if self._deadline_ns is not None and self._clock() > self._deadline_ns:
            self.clear()
            return None
        if self._chain >= self._max_chain:
            self.clear()
            return None
        return f"{self._text} {new_text.strip()}".strip()

    def clear(self) -> None:
        self._text = ""
        self._chain = 0
        self._deadline_ns = None


__all__ = ["ContinuationWindow"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/test_continuation_window.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/speech/continuation_window.py tests/unit/speech/test_continuation_window.py
git commit -m "feat(speech): ContinuationWindow helper for voice continuation recombine"
```

---

## Task 2: `BrainManager.drop_last_turn` (Unit C history hygiene)

**Why:** When a continuation arrives in the grace window *after* the prior turn already committed its (user, assistant) pair to history, the combined turn must REPLACE that pair, not duplicate the half-sentence. `drop_last_turn` removes the tail pair only when its user message matches the text we are about to supersede — so it is a safe no-op when the prior turn was aborted before commit (the common interrupt case).

**Files:**
- Modify: `jarvis/brain/manager.py` (add method near `clear_history`, `manager.py:4403`)
- Test: `tests/unit/brain/test_drop_last_turn.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/brain/test_drop_last_turn.py
"""Unit tests for BrainManager.drop_last_turn (continuation recombine, Unit C)."""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.protocols import BrainMessage


def _mgr_with_history(messages):
    mgr = BrainManager.__new__(BrainManager)
    mgr._history = list(messages)
    return mgr


def test_drops_matching_user_assistant_pair():
    mgr = _mgr_with_history([
        BrainMessage(role="user", content="hello"),
        BrainMessage(role="assistant", content="hi"),
        BrainMessage(role="user", content="ich moechte nach"),
        BrainMessage(role="assistant", content="Wohin genau?"),
    ])
    assert mgr.drop_last_turn("ich moechte nach") is True
    assert len(mgr._history) == 2
    assert mgr._history[-1].content == "hi"


def test_noop_when_tail_user_text_differs():
    mgr = _mgr_with_history([
        BrainMessage(role="user", content="something else"),
        BrainMessage(role="assistant", content="ok"),
    ])
    assert mgr.drop_last_turn("ich moechte nach") is False
    assert len(mgr._history) == 2


def test_noop_on_short_history():
    mgr = _mgr_with_history([BrainMessage(role="user", content="x")])
    assert mgr.drop_last_turn("x") is False
    assert len(mgr._history) == 1


def test_noop_when_tail_is_not_user_assistant_pair():
    mgr = _mgr_with_history([
        BrainMessage(role="assistant", content="a"),
        BrainMessage(role="assistant", content="b"),
    ])
    assert mgr.drop_last_turn("a") is False


def test_match_is_whitespace_insensitive():
    mgr = _mgr_with_history([
        BrainMessage(role="user", content="  ich moechte nach  "),
        BrainMessage(role="assistant", content="?"),
    ])
    assert mgr.drop_last_turn("ich moechte nach") is True
    assert mgr._history == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/brain/test_drop_last_turn.py -v`
Expected: FAIL — `AttributeError: 'BrainManager' object has no attribute 'drop_last_turn'`.

- [ ] **Step 3: Write the minimal implementation**

Add directly below `clear_history` (`manager.py:4403-4404`):

```python
    def drop_last_turn(self, expected_user_text: str) -> bool:
        """Remove the most recent (user, assistant) pair when its user message
        matches ``expected_user_text`` (whitespace-insensitive).

        Used by the voice continuation-recombine path: when a combined turn
        supersedes the immediately-preceding committed turn, the truncated half
        must not be duplicated in history. Safe no-op when fewer than two
        messages are buffered, when the tail is not a user/assistant pair, or
        when the tail user text does not match — so it does nothing when the
        prior turn was aborted before commit (the common interrupt case).
        Returns ``True`` iff a pair was removed.
        """
        if len(self._history) < 2:
            return False
        last = self._history[-1]
        prev = self._history[-2]
        if last.role != "assistant" or prev.role != "user":
            return False
        if (prev.content or "").strip() != (expected_user_text or "").strip():
            return False
        del self._history[-2:]
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/brain/test_drop_last_turn.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/manager.py tests/unit/brain/test_drop_last_turn.py
git commit -m "feat(brain): drop_last_turn for voice continuation recombine history hygiene"
```

---

## Task 3: `[voice]` config fields

**Files:**
- Modify: `jarvis/core/config.py` (`VoiceConfig`, after `clarify_after_ms` at `config.py:1532`)
- Test: `tests/unit/core/test_voice_continuation_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_voice_continuation_config.py
"""Defaults for the voice continuation-recombine knobs."""
from __future__ import annotations

from jarvis.core.config import VoiceConfig


def test_continuation_defaults():
    cfg = VoiceConfig()
    assert cfg.continuation_interrupt_enabled is True
    assert cfg.continuation_grace_ms == 2500
    assert cfg.continuation_max_chain == 3


def test_continuation_overrides_apply():
    cfg = VoiceConfig(
        continuation_interrupt_enabled=False,
        continuation_grace_ms=1000,
        continuation_max_chain=2,
    )
    assert cfg.continuation_interrupt_enabled is False
    assert cfg.continuation_grace_ms == 1000
    assert cfg.continuation_max_chain == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/core/test_voice_continuation_config.py -v`
Expected: FAIL — `AttributeError: 'VoiceConfig' object has no attribute 'continuation_interrupt_enabled'`.

- [ ] **Step 3: Write the minimal implementation**

Insert after the `clarify_after_ms: int = 2500` field (`config.py:1532`), before the timeout-floor field:

```python
    # --- Continuation recombine (2026-06-16) -------------------------------
    # When the user keeps talking AFTER an utterance was already dispatched to
    # the brain (the brain is already thinking/speaking), abort the half-formed
    # answer and re-think the COMBINED sentence as one turn, instead of dropping
    # the earlier half as a fresh, context-less message. Master switch; false =
    # behaves exactly as before this feature. Spec:
    # docs/superpowers/specs/2026-06-16-voice-continuation-recombine-while-thinking-design.md
    continuation_interrupt_enabled: bool = True
    # How long AFTER the answer finished a new utterance still counts as a
    # continuation (the "kurze Nachfrist"). Kept short to bound the risk that a
    # genuinely new command is mis-attached.
    continuation_grace_ms: int = 2500
    # Max fragments coalesced into one turn before the next utterance is a fresh
    # turn (mirrors completion_max_chain — bounds indefinite chaining).
    continuation_max_chain: int = 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/core/test_voice_continuation_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py tests/unit/core/test_voice_continuation_config.py
git commit -m "feat(config): [voice] continuation recombine knobs"
```

---

## Task 4: Pipeline recombine + arm helpers (Unit A + C wiring)

This task adds two small, unit-testable helper methods and wires them into the
turn flow plus the constructor. The helpers carry the logic; the call-site edits
are thin.

**Files:**
- Modify: `jarvis/speech/pipeline.py`
- Test: `tests/unit/speech/test_continuation_recombine.py`

### 4a — `_maybe_recombine_continuation` + `_arm_continuation` helpers

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/speech/test_continuation_recombine.py
"""Pipeline-level continuation recombine + arm (Unit A/C wiring)."""
from __future__ import annotations

from jarvis.speech.pipeline import SpeechPipeline
from jarvis.speech.continuation_window import ContinuationWindow


class FakeBrain:
    def __init__(self) -> None:
        self.dropped: list[str] = []

    def drop_last_turn(self, expected_user_text: str) -> bool:
        self.dropped.append(expected_user_text)
        return True


def _pipeline(*, enabled=True, window=None, brain=None):
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._continuation_interrupt_enabled = enabled
    p._continuation_window = window or ContinuationWindow(grace_ms=2500, max_chain=3)
    p._brain = brain
    p._continuation_dispatched_this_turn = False
    return p


def test_recombine_joins_and_requests_drop():
    brain = FakeBrain()
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    win.note_dispatch("ich moechte nach", continued=False)
    p = _pipeline(window=win, brain=brain)
    text, continued = p._maybe_recombine_continuation("Griechenland")
    assert text == "ich moechte nach Griechenland"
    assert continued is True
    assert brain.dropped == ["ich moechte nach"]  # drop requested with prior text


def test_recombine_noop_when_disabled():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    win.note_dispatch("ich moechte nach", continued=False)
    p = _pipeline(enabled=False, window=win, brain=FakeBrain())
    text, continued = p._maybe_recombine_continuation("Griechenland")
    assert text == "Griechenland"
    assert continued is False


def test_recombine_noop_when_unarmed():
    p = _pipeline(window=ContinuationWindow(grace_ms=2500, max_chain=3), brain=FakeBrain())
    text, continued = p._maybe_recombine_continuation("hello")
    assert text == "hello"
    assert continued is False


def test_cancel_text_clears_window_and_does_not_merge():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    win.note_dispatch("ich moechte nach", continued=False)
    p = _pipeline(window=win, brain=FakeBrain())
    # "vergiss das" is a cancel phrase -> no merge, window cleared
    text, continued = p._maybe_recombine_continuation("vergiss das")
    assert text == "vergiss das"
    assert continued is False
    assert not win.is_armed


def test_arm_continuation_records_dispatch_and_marks_flag():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    p = _pipeline(window=win)
    p._arm_continuation("ich moechte nach", continued=False)
    assert win.is_armed
    assert win.text == "ich moechte nach"
    assert p._continuation_dispatched_this_turn is True


def test_arm_continuation_noop_when_disabled():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    p = _pipeline(enabled=False, window=win)
    p._arm_continuation("x", continued=False)
    assert not win.is_armed
    assert p._continuation_dispatched_this_turn is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/test_continuation_recombine.py -v`
Expected: FAIL — `AttributeError: 'SpeechPipeline' object has no attribute '_maybe_recombine_continuation'`.

- [ ] **Step 3: Write the minimal implementation**

Add these two methods to `SpeechPipeline` (place them right above `_handle_utterance_turn`, ~`pipeline.py:3825`). `is_cancel` is already imported (`pipeline.py:95`).

```python
    def _maybe_recombine_continuation(self, text: str) -> tuple[str, bool]:
        """Unit C: if the user kept talking while the brain was thinking/speaking
        (or within the short grace afterwards), return the COMBINED text plus a
        ``continued=True`` flag for the subsequent ``_arm_continuation`` call.

        A cancel phrase ("vergiss das") clears the window and never merges.
        Fail-open: any error returns ``(text, False)`` — the user is never
        swallowed (AD-OE6). No-op when the feature is disabled or unarmed.
        """
        if not getattr(self, "_continuation_interrupt_enabled", False):
            return text, False
        window = getattr(self, "_continuation_window", None)
        if window is None:
            return text, False
        if is_cancel(text):
            window.clear()
            return text, False
        try:
            combined = window.try_recombine(text)
        except Exception:  # noqa: BLE001 — fail-open by contract
            log.warning("ContinuationWindow.try_recombine raised; failing open", exc_info=True)
            return text, False
        if not combined or combined == text:
            return text, False
        prior = window.text
        log.info("↪ Continuation recombine → %r", combined[:120])
        brain = getattr(self, "_brain", None)
        if brain is not None and hasattr(brain, "drop_last_turn"):
            try:
                brain.drop_last_turn(prior)
            except Exception:  # noqa: BLE001 — history hygiene must never crash the turn
                log.debug("drop_last_turn failed (non-fatal)", exc_info=True)
        return combined, True

    def _arm_continuation(self, text: str, *, continued: bool) -> None:
        """Unit A: record the text we are about to dispatch so the NEXT
        utterance can re-attach to it. Flags that this turn dispatched, so the
        turn-end hook starts the grace countdown only for armed turns. No-op
        when disabled."""
        if not getattr(self, "_continuation_interrupt_enabled", False):
            return
        window = getattr(self, "_continuation_window", None)
        if window is None:
            return
        try:
            window.note_dispatch(text, continued=continued)
            self._continuation_dispatched_this_turn = True
        except Exception:  # noqa: BLE001
            log.debug("continuation note_dispatch failed (non-fatal)", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/test_continuation_recombine.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/speech/pipeline.py tests/unit/speech/test_continuation_recombine.py
git commit -m "feat(speech): continuation recombine + arm helpers (Unit A/C)"
```

### 4b — Constructor + call-site wiring

- [ ] **Step 1: Instantiate the window in `__init__`**

Right after `self._continuation_buffer = ContinuationBuffer()` (`pipeline.py:1244`), add:

```python
        # Continuation recombine (2026-06-16): re-attach a fast-follow utterance
        # to the in-flight turn. See ContinuationWindow + _maybe_recombine_continuation.
        _voice_cfg = getattr(self._config, "voice", None)
        self._continuation_interrupt_enabled = bool(
            getattr(_voice_cfg, "continuation_interrupt_enabled", True)
        )
        self._continuation_window = ContinuationWindow(
            grace_ms=int(getattr(_voice_cfg, "continuation_grace_ms", 2500)),
            max_chain=int(getattr(_voice_cfg, "continuation_max_chain", 3)),
        )
        self._continuation_dispatched_this_turn = False
```

And add the import near the other speech imports (`pipeline.py:98`):

```python
from jarvis.speech.continuation_window import ContinuationWindow
```

- [ ] **Step 2: Call the recombine helper in `_handle_utterance_turn`**

Immediately after the user log line `log.info("👤 User [%s]: %s", lang, text)` (`pipeline.py:3967`), insert:

```python
        # Continuation recombine: attach this utterance to a just-dispatched one
        # if the user kept talking while the brain was thinking/speaking. Must run
        # AFTER the hangup / wake-only / hallucination guards above (they already
        # returned) and BEFORE the ContinuationBuffer below, so the combined text
        # is re-classified for syntactic completeness as a whole.
        text, _continued_dispatch = self._maybe_recombine_continuation(text)
```

- [ ] **Step 3: Arm the window at the dispatch commit point**

Immediately after `await self._set_turn_state(TurnTakingState.PROCESSING)` (`pipeline.py:4100`), insert:

```python
        # Arm/refresh the continuation window with the text we are dispatching.
        self._arm_continuation(text, continued=locals().get("_continued_dispatch", False))
```

(Use `locals().get(...)` so the streaming fixture paths that jump straight here without the recombine line stay safe; `_continued_dispatch` is set in Step 2 on every real turn.)

- [ ] **Step 4: Start the grace countdown on turn end**

In `_handle_utterance` (`pipeline.py:3793-3798`), change the `finally` to mark the window idle for turns that armed:

```python
        try:
            self._continuation_dispatched_this_turn = False
            return await self._handle_utterance_turn(
                pcm, skip_completion=skip_completion
            )
        finally:
            if getattr(self, "_continuation_dispatched_this_turn", False):
                win = getattr(self, "_continuation_window", None)
                if win is not None:
                    win.mark_idle()
            self._emit_latency_turn_complete()
```

- [ ] **Step 5: Clear the window on hangup / continuation discard**

Wherever `self._continuation_buffer.discard()` is called (`pipeline.py:2876` in the hangup path, and `pipeline.py:3202`), add directly after it:

```python
            win = getattr(self, "_continuation_window", None)
            if win is not None:
                win.clear()
```

- [ ] **Step 6: Run the speech suite to verify nothing regressed**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/ tests/unit/audio/ -q`
Expected: PASS for all continuation tests; the 2 pre-existing `xfail` in the speech suite remain xfail. If any unrelated test fails, confirm it fails on a clean checkout before proceeding (shared tree).

- [ ] **Step 7: Lint touched lines**

Run: `C:\Program Files\Python311\python.exe -m ruff check jarvis/speech/pipeline.py`
Expected: no new findings on the touched lines.

- [ ] **Step 8: Commit**

```bash
git add jarvis/speech/pipeline.py
git commit -m "feat(speech): wire continuation window into turn flow (Unit A/C)"
```

---

## Task 5: Thinking-phase interrupt monitor (Unit B)

Reuse the proven `_barge_monitor` (echo heuristics, Silero, second mic) with a
shorter grace, race it against the brain task inside `_run_brain_with_stall_guard`,
and hand interruption over to the existing playback barge monitor once the first
frame plays. Cancelling the brain mid-think skips its history commit, so the
truncated half never lands (verified: `generate()` appends user+assistant last,
`manager.py:4116-4117`; `generate_stream` cancels the producer in `finally`,
`manager.py:4345-4351`).

**Files:**
- Modify: `jarvis/speech/pipeline.py`
- Test: `tests/unit/speech/test_thinking_interrupt_monitor.py`

### 5a — Parameterize `_barge_monitor` grace + add the module constant

- [ ] **Step 1: Add the constant** near the other speech module constants (top of `pipeline.py`, alongside e.g. `FORCED_CUT_REASONS`):

```python
# Grace before the thinking-phase continuation-interrupt monitor may fire. Much
# shorter than the playback barge grace (1.5 s) because during pure thinking
# there is no TTS playing, so speaker->mic echo is not a concern.
_CONTINUATION_THINKING_GRACE_S: float = 0.3
```

- [ ] **Step 2: Parameterize `_barge_monitor`** (`pipeline.py:5857`). Change the signature and the leading sleep:

```python
    async def _barge_monitor(self, *, grace_s: float = 1.5) -> bool:
```
and
```python
        try:
            await asyncio.sleep(grace_s)
        except asyncio.CancelledError:
            return False
```

The two existing callers (`pipeline.py:5092`, `pipeline.py:5791`) call `self._barge_monitor()` with no args, so the 1.5 s default preserves their behavior exactly.

### 5b — Set the first-frame handoff flag in `_brain_streaming`

- [ ] **Step 3:** At the top of `_brain_streaming`, next to `barged = False` (`pipeline.py:4925`), add:

```python
        # Handoff flag for the thinking-phase interrupt monitor in
        # _run_brain_with_stall_guard: once playback starts, that monitor stands
        # down and the per-playback barge monitor (created below) takes over, so
        # only one extra mic runs at a time.
        self._brain_first_frame_played = False
```

- [ ] **Step 4:** Immediately before `produce_task = asyncio.create_task(_produce(), ...)` (`pipeline.py:5088`), add:

```python
        self._brain_first_frame_played = True
```

### 5c — Race the monitor inside the stall guard

- [ ] **Step 5: Write the failing test**

```python
# tests/unit/speech/test_thinking_interrupt_monitor.py
"""Unit B: thinking-phase continuation-interrupt monitor in the stall guard."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.speech.pipeline import SpeechPipeline


def _guard_pipeline(*, enabled=True):
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._continuation_interrupt_enabled = enabled
    # stall-guard ctor-default fallbacks (see _run_brain_with_stall_guard)
    p._brain_stall_poll_s = 0.01
    p._brain_timeout_s = 30.0
    p._brain_hard_timeout_s = 90.0
    p._brain_last_progress = 0.0
    p._brain_first_frame_played = False
    return p


@pytest.mark.asyncio
async def test_interrupt_during_thinking_aborts_and_returns_barged(monkeypatch):
    p = _guard_pipeline()

    async def fake_monitor(*, grace_s):
        return True  # user spoke immediately

    async def slow_brain():
        await asyncio.sleep(5)  # would never finish in the test window
        return ("answer", False)

    monkeypatch.setattr(p, "_barge_monitor", fake_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        slow_brain(), interrupt_monitor=True
    )
    assert response == ""
    assert barged is True


@pytest.mark.asyncio
async def test_no_interrupt_returns_brain_result(monkeypatch):
    p = _guard_pipeline()

    async def quiet_monitor(*, grace_s):
        await asyncio.sleep(10)  # never fires
        return False

    async def quick_brain():
        return ("the answer", False)

    monkeypatch.setattr(p, "_barge_monitor", quiet_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        quick_brain(), interrupt_monitor=True
    )
    assert response == "the answer"
    assert barged is False


@pytest.mark.asyncio
async def test_monitor_stands_down_after_first_frame(monkeypatch):
    p = _guard_pipeline()

    async def fake_monitor(*, grace_s):
        # Simulate the user speaking only AFTER playback already started.
        p._brain_first_frame_played = True
        await asyncio.sleep(0.05)
        return True

    async def brain_that_plays():
        # Playback started; monitor must have stood down, so the real result wins.
        await asyncio.sleep(0.1)
        return ("played answer", False)

    monkeypatch.setattr(p, "_barge_monitor", fake_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        brain_that_plays(), interrupt_monitor=True
    )
    assert response == "played answer"
    assert barged is False


@pytest.mark.asyncio
async def test_disabled_does_not_start_monitor(monkeypatch):
    p = _guard_pipeline(enabled=False)
    started = {"called": False}

    async def tracking_monitor(*, grace_s):
        started["called"] = True
        return True

    async def quick_brain():
        return ("ok", False)

    monkeypatch.setattr(p, "_barge_monitor", tracking_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        quick_brain(), interrupt_monitor=True
    )
    assert response == "ok"
    assert barged is False
    assert started["called"] is False
```

- [ ] **Step 6: Run test to verify it fails**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/test_thinking_interrupt_monitor.py -v`
Expected: FAIL — `TypeError: _run_brain_with_stall_guard() got an unexpected keyword argument 'interrupt_monitor'`.

- [ ] **Step 7: Implement the monitor race**

Change the `_run_brain_with_stall_guard` signature (`pipeline.py:5384`):

```python
    async def _run_brain_with_stall_guard(
        self, coro: Awaitable[tuple[str, bool]], *, interrupt_monitor: bool = False
    ) -> tuple[str, bool]:
```

After `task: asyncio.Task[...] = asyncio.ensure_future(coro)` (`pipeline.py:5425`), add:

```python
        monitor_task: asyncio.Task[bool] | None = None
        if interrupt_monitor and getattr(self, "_continuation_interrupt_enabled", False):
            self._brain_first_frame_played = False
            monitor_task = asyncio.create_task(
                self._barge_monitor(grace_s=_CONTINUATION_THINKING_GRACE_S),
                name="thinking-interrupt-monitor",
            )
```

Replace the poll-wait line `done, _pending = await asyncio.wait({task}, timeout=poll_s)` (`pipeline.py:5428`) and the immediately-following `if task in done:` block with:

```python
                waiters = {task} if monitor_task is None else {task, monitor_task}
                done, _pending = await asyncio.wait(waiters, timeout=poll_s)
                if monitor_task is not None:
                    if getattr(self, "_brain_first_frame_played", False):
                        # Playback started — _brain_streaming's own barge monitor
                        # now owns interruption; stand our thinking monitor down.
                        monitor_task.cancel()
                        monitor_task = None
                    elif (
                        monitor_task in done
                        and not monitor_task.cancelled()
                        and monitor_task.result()
                    ):
                        # User spoke during thinking → abort the half-formed
                        # answer. Cancelling the brain skips its history commit,
                        # so the truncated half never lands; the next utterance
                        # recombines with this prompt.
                        log.info(
                            "✋ Continuation interrupt — user spoke during thinking, "
                            "aborting brain turn"
                        )
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass
                        return ("", True)
                if task in done:
                    if monitor_task is not None:
                        monitor_task.cancel()
                    return task.result()
```

In the `finally` (`pipeline.py:5464`), cancel a still-running monitor before the task cleanup:

```python
        finally:
            if monitor_task is not None and not monitor_task.done():
                monitor_task.cancel()
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
```

- [ ] **Step 8: Run test to verify it passes**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/test_thinking_interrupt_monitor.py -v`
Expected: PASS (4 passed).

### 5d — Pass `interrupt_monitor=True` from the voice path + silence the interrupted empty turn

- [ ] **Step 9:** At the streaming brain call site (`pipeline.py:4133-4135`), pass the flag:

```python
                response, barged = await self._run_brain_with_stall_guard(
                    self._brain_streaming(text, lang),
                    interrupt_monitor=True,
                )
```

- [ ] **Step 10:** Guard the empty-response handler so an interrupted (barged) empty
turn stays silent instead of speaking a clarifying question. Change the
`if not response.strip():` block at `pipeline.py:4169-4177` to:

```python
            if not response.strip():
                if barged:
                    # Interrupted before any answer (continuation interrupt or an
                    # early barge): stay silent — the next utterance recombines
                    # with this prompt. A clarifying question here would talk over
                    # the user who is still going.
                    return await self._finish_after_response(barged=barged)
                # AD-OE6 zero-silent-drop. A *total* provider-chain failure is
                # spoken; a fire-and-forget spawn stays silent (bus reports);
                # ANY other empty turn (function_call/CU without speech, empty
                # content) gets a spoken clarifying question instead of muting —
                # the dominant "Jarvis antwortet nie" cause (logs 2026-06-08).
                await self._handle_silent_brain_turn(lang, text)
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True
```

- [ ] **Step 11: Run the speech + audio suites**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/ tests/unit/audio/ -q`
Expected: PASS (the new monitor tests + all prior; 2 pre-existing speech xfail remain).

- [ ] **Step 12: Lint touched lines**

Run: `C:\Program Files\Python311\python.exe -m ruff check jarvis/speech/pipeline.py`
Expected: no new findings on touched lines.

- [ ] **Step 13: Commit**

```bash
git add jarvis/speech/pipeline.py tests/unit/speech/test_thinking_interrupt_monitor.py
git commit -m "feat(speech): thinking-phase continuation-interrupt monitor (Unit B)"
```

---

## Task 6: Regression + end-to-end recombine guard

Prove (a) the feature OFF reproduces today's behavior, and (b) two utterances coalesce into one combined window text across an idle boundary.

**Files:**
- Test: `tests/unit/speech/test_continuation_recombine.py` (extend)

- [ ] **Step 1: Write the failing tests** (append to the existing file)

```python
def test_two_utterances_coalesce_across_idle_boundary():
    """Turn 1 dispatches; on idle a grace window opens; turn 2 recombines."""
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    p = _pipeline(window=win, brain=FakeBrain())

    # Turn 1: fresh dispatch of the truncated half.
    t1, c1 = p._maybe_recombine_continuation("ich moechte nach")
    assert (t1, c1) == ("ich moechte nach", False)
    p._arm_continuation(t1, continued=c1)
    win.mark_idle()  # turn 1 finished (aborted or spoken)

    # Turn 2: the continuation re-attaches to turn 1's text.
    t2, c2 = p._maybe_recombine_continuation("Griechenland")
    assert t2 == "ich moechte nach Griechenland"
    assert c2 is True
    p._arm_continuation(t2, continued=c2)
    assert win.text == "ich moechte nach Griechenland"


def test_disabled_keeps_utterances_independent():
    win = ContinuationWindow(grace_ms=2500, max_chain=3)
    p = _pipeline(enabled=False, window=win, brain=FakeBrain())
    t1, c1 = p._maybe_recombine_continuation("ich moechte nach")
    p._arm_continuation(t1, continued=c1)
    win.mark_idle()
    t2, c2 = p._maybe_recombine_continuation("Griechenland")
    assert t2 == "Griechenland"   # NOT merged
    assert c2 is False
    assert not win.is_armed       # window never armed when disabled
```

- [ ] **Step 2: Run to verify** (these should already PASS given Tasks 1+4 — they are a regression lock, not new production code)

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/test_continuation_recombine.py -v`
Expected: PASS (8 passed).

- [ ] **Step 3: Run the full speech + brain + config touched suites**

Run: `C:\Program Files\Python311\python.exe -m pytest tests/unit/speech/ tests/unit/audio/ tests/unit/brain/test_drop_last_turn.py tests/unit/core/test_voice_continuation_config.py -q`
Expected: PASS (known pre-existing speech xfail unaffected; any unrelated failure must reproduce on a clean checkout — shared tree).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/speech/test_continuation_recombine.py
git commit -m "test(speech): regression lock for continuation recombine on/off"
```

---

## Go-live (after all tasks merged)

The edits load via the editable install but the running tray app holds the old
`pipeline.py`/`vad.py`/`config.py` in memory. To take effect:

```
POST /api/settings/restart-app
```

(Not `Stop-Process` — Access Denied under the tray `pythonw.exe`.) This interrupts any active voice session / in-flight mission, so do it when the maintainer is ready. The thinking-phase interrupt (Unit B) needs a real microphone to verify live; the recombine logic (Units A/C) is fully exercised by the unit suite and can also be observed via the WS text-drive path.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §4.1 ContinuationWindow → Task 1. ✓
- §4.2 thinking-phase interrupt monitor → Task 5. ✓
- §4.3 recombine on next utterance → Task 4 (`_maybe_recombine_continuation`). ✓
- §4.4 `drop_last_turn` → Task 2. ✓ (Refinement: matches on the prior user text instead of a `_continuation_committed` flag — strictly more robust, covers the aborted-before-commit case as a natural no-op. Documented in Task 2.)
- §5 config → Task 3. ✓
- §6 edge cases: cancel ("vergiss das") clears + no merge (Task 4a test); hangup/wake-only clear (Task 4b Step 5; wake-only returns before recombine and the grace deadline expires it); chain cap (Task 1 test); grace expiry (Task 1 test); barged-empty stays silent (Task 5 Step 10). ✓
- §7 testing strategy → Tasks 1,2,4,5,6. ✓
- §8 risk (single extra mic at a time): Task 5 first-frame handoff stands the thinking monitor down before the playback barge monitor owns the mic. ✓

**Placeholder scan:** none — every code step shows the full code.

**Type consistency:** `ContinuationWindow.note_dispatch(text, *, continued)`, `try_recombine`, `mark_idle`, `clear`, `text`, `is_armed` are used identically across Tasks 1/4/6. `drop_last_turn(expected_user_text) -> bool` consistent across Tasks 2/4. `_run_brain_with_stall_guard(coro, *, interrupt_monitor=False)` consistent across Task 5 Steps 7/9. `_barge_monitor(*, grace_s=1.5)` consistent across Task 5 Steps 2/7. `_continuation_dispatched_this_turn`, `_continuation_interrupt_enabled`, `_continuation_window`, `_brain_first_frame_played` named identically throughout.
