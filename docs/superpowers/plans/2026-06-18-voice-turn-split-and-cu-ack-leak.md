# Voice Turn-Split & Computer-Use ACK-Leak — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two independent voice-session defects forensically traced to session `71f2d2de` (2026-06-18 12:56): (C) an internal English Computer-Use steering instruction leaking into the visible/spoken answer, and (A) one continuous spoken request being chopped into three separate turns.

**Architecture:** Three surgical, independently-testable fixes. Bug C wires the already-existing localized `cu_dispatch_ack` phrase and the already-existing `suppress_response` tool-loop contract onto `ComputerUseTool`. Bug A is fixed in two layers of defense-in-depth: the VAD arms its long-utterance patience autonomously (no longer depending on the STT probe surfacing a partial), and the ContinuationWindow freezes its grace deadline the moment the user resumes speaking (so a slow follow-up still recombines).

**Tech Stack:** Python 3.11, pytest (`asyncio_mode=auto`), Silero VAD, the in-process tool-use loop. No new dependencies.

---

## Forensic root causes (the evidence this plan is built on)

All claims below are proven from `data/sessions.db` (session `71f2d2de`), `data/jarvis_desktop.log` (lines 75430–75738), and the code as it stands in the working tree.

### Bug C — English steering instruction became a user-facing bubble
- `jarvis/plugins/tool/computer_use_tool.py:150-157` returns, as the tool's `output`, the English instruction *"Desktop mission started in the background; … Reply with a brief acknowledgement only — do NOT claim the task is already done."* This is an internal instruction for the router, English by policy.
- `ComputerUseTool` has **no** `suppress_response` flag. Its sibling `SpawnWorkerTool` sets `suppress_response: bool = True` (`jarvis/plugins/tool/spawn_worker.py:232`). Without it, the tool-use loop (`jarvis/brain/tool_use_loop.py:662-666` + `:709-728`) feeds the tool output back into a **second** brain iteration instead of taking it verbatim and stopping. Gemini then **echoed** the English instruction as its own assistant text, which `chat_store.add_message(role="assistant")` rendered as a visible bubble.
- A fully-localized acknowledgement phrase **already exists but is unused**: `cu_dispatch_ack` in `jarvis/voice/action_phrases.py:92-98` (de/en/es). The fix wires it in.

### Bug A — one request split into three turns (all on `reason=silence`, base 1472 ms window)
Three protective mechanisms could each have prevented this; **none armed** in the session:
- **Layer 1 (primary) — adaptive patience never armed.** `_should_extend_silence_for_composition` (`jarvis/speech/pipeline.py:193-200`) only calls `self._vad.extend_silence_window(_DELEGATION_SILENCE_MS)` (`pipeline.py:1809-1812`) when the **STT stability probe** surfaces a growing live partial (`self._probe_live_text`). In this session the probe never surfaced a qualifying partial, so the window stayed at the base 1472 ms for **every** turn. Turn 1 ended on `silence_ms=2976` — had the 3000 ms patience been armed, the endpoint would **not** have fired (2976 < 3000) and the whole sentence would have stayed one turn. This is the highest-leverage fix.
- **Layer 3 (defense-in-depth) — ContinuationWindow grace expired against finalization, not speech-start.** After the continuation-interrupt aborted Turn 1's brain call (`pipeline.py:5959-5968`), `ContinuationWindow.mark_idle()` started a 2500 ms grace (`jarvis/speech/continuation_window.py:74-77`). The user took ~3 s to formulate the next fragment, so `try_recombine` (`continuation_window.py:79-93`) saw an expired deadline and started a fresh turn. The deadline is checked against the *finalized* follow-up text, not against the moment the user *resumed speaking*.
- **Layer 2 (DELIBERATELY NOT FIXED) — `is_incomplete` does not hold "…das".** Turn 1 ended "…und zwar möchte ich das". `is_incomplete` (`jarvis/speech/completion.py:186-232`) treats "das" as ambiguous (determiner *or* complete object pronoun) and returns `None` by its "precision over recall" contract. Widening it would wrongly hold complete sentences ending in "das/die/der". We leave this layer alone — it is correct as designed.

### Out of scope (tracked, not fixed here)
- **Security follow-up:** the Telegram bot token is logged in cleartext in `data/jarvis_desktop.log` (hundreds of `getUpdates` lines via `python-telegram-bot`'s httpx logging). Rotate the token and silence that logger separately. Not part of this plan per maintainer scope.
- **Bug B (Telegram read tool):** explicitly out of scope — the maintainer confirmed the Computer-Use fallback was correct behavior.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `jarvis/plugins/tool/computer_use_tool.py` | Add `suppress_response` flag; return localized ACK instead of English instruction | 1 |
| `tests/unit/plugins/tool/test_computer_use_tool.py` | Cover the ACK localization + suppress contract | 1 |
| `jarvis/audio/vad.py` | Arm long-utterance patience autonomously from accumulated speech frames | 2 |
| `tests/unit/audio/test_vad_turn_taking.py` | Cover autonomous patience (long arms, short stays snappy) | 2 |
| `jarvis/speech/continuation_window.py` | Freeze the grace deadline when the user resumes speaking | 3 |
| `jarvis/speech/pipeline.py` | Call the new freeze hook from the speech-start path | 3 |
| `tests/unit/speech/test_continuation_window.py` | Cover the speech-resume freeze | 3 |

**Parallelism:** Task 1 (tool) and Task 2 (VAD) touch disjoint modules and may run fully in parallel. Task 3 touches `continuation_window.py` + `pipeline.py`; keep its `pipeline.py` edit hunk-isolated (the tree is shared — never `git add -A`).

---

## Task 1: Bug C — `ComputerUseTool` suppresses its response and returns a localized ACK

**Files:**
- Modify: `jarvis/plugins/tool/computer_use_tool.py:74-101` (add flag) and `:150-157` (return value)
- Test: `tests/unit/plugins/tool/test_computer_use_tool.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/plugins/tool/test_computer_use_tool.py` (create the file if absent; reuse existing fixtures/fakes for `EventBus` + a stub `HarnessManager` so the `bus is not None` background path is taken):

```python
import asyncio
import pytest

from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.computer_use_tool import ComputerUseTool
from jarvis.voice.action_phrases import action_phrase


def _ctx(utterance: str) -> ExecutionContext:
    # Minimal ExecutionContext carrying the user's words for language detection.
    return ExecutionContext(user_utterance=utterance, trace_id=None)


@pytest.fixture
def tool(fake_bus):  # fake_bus: any EventBus that accepts .publish()
    return ComputerUseTool(bus=fake_bus)


def test_suppress_response_flag_is_set():
    # The tool-use loop only skips the second brain iteration when this is True.
    assert ComputerUseTool.suppress_response is True


@pytest.mark.asyncio
async def test_dispatch_ack_is_german_for_german_turn(tool):
    res = await tool.execute({"goal": "öffne Telegram"}, _ctx("benutz mal mein Telegram"))
    assert res.success is True
    assert res.output == action_phrase("cu_dispatch_ack", "de")


@pytest.mark.asyncio
async def test_dispatch_ack_is_english_for_english_turn(tool):
    res = await tool.execute({"goal": "open Telegram"}, _ctx("use my Telegram please"))
    assert res.success is True
    assert res.output == action_phrase("cu_dispatch_ack", "en")


@pytest.mark.asyncio
async def test_no_english_steering_instruction_leaks(tool):
    res = await tool.execute({"goal": "open Telegram"}, _ctx("benutz mal mein Telegram"))
    # The internal English instruction must never be the user-facing output.
    assert "Reply with a brief acknowledgement only" not in (res.output or "")
    assert "Desktop mission started in the background" not in (res.output or "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/plugins/tool/test_computer_use_tool.py -v`
Expected: FAIL — `test_suppress_response_flag_is_set` (AttributeError / False) and the ACK tests (output is the English instruction).

- [ ] **Step 3: Add the `suppress_response` class flag**

In `jarvis/plugins/tool/computer_use_tool.py`, in the `ComputerUseTool` class body, immediately after `risk_tier: str = "monitor"` (line 78), add:

```python
    # The mission runs in the BACKGROUND and is announced on completion. Take
    # THIS output verbatim as the final answer and skip the second brain
    # iteration, exactly like spawn_worker — otherwise the model sees the
    # internal English steering instruction below and echoes it as its own
    # assistant text (live bug 2026-06-18, session 71f2d2de). tool_use_loop
    # honours this flag at jarvis/brain/tool_use_loop.py:662-666 / 709-728.
    suppress_response: bool = True
```

- [ ] **Step 4: Return the localized ACK instead of the English instruction**

In `jarvis/plugins/tool/computer_use_tool.py`, replace the `return ToolResult(...)` block at lines 150-157 with:

```python
        # suppress_response=True (class attr) → tool_use_loop takes this output
        # VERBATIM and stops, so there is no second iteration to echo an
        # internal instruction. Speak the pre-existing, localized dispatch ACK
        # (AP-11: pure dict lookup, no LLM). Language detected from the user's
        # own words, mirroring _run_background below.
        ack_lang = resolve_phrase_language(None, ctx.user_utterance)
        return ToolResult(
            success=True,
            output=action_phrase("cu_dispatch_ack", ack_lang),
        )
```

(`action_phrase` and `resolve_phrase_language` are already imported at lines 35-39 — no new import.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/plugins/tool/test_computer_use_tool.py -v`
Expected: PASS (all four).

- [ ] **Step 6: Regression-check the tool-loop suppress contract**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/ -k "tool_use_loop or suppress" -v`
Expected: PASS — confirms the existing `suppress_response` plumbing still behaves with a second tool wired to it.

- [ ] **Step 7: Commit (hunk-isolated — shared tree)**

```bash
git add jarvis/plugins/tool/computer_use_tool.py tests/unit/plugins/tool/test_computer_use_tool.py
git commit -m "fix(voice): computer_use suppresses response, speaks localized dispatch ACK

ComputerUseTool lacked suppress_response, so its internal English steering
instruction was fed into a second brain iteration and echoed verbatim as a
user-facing bubble (session 71f2d2de). Set suppress_response=True and return
the existing localized cu_dispatch_ack phrase instead."
```

---

## Task 2: Bug A / Layer 1 — VAD arms long-utterance patience autonomously

**Why:** The adaptive 3000 ms patience only ever armed via the STT probe surfacing a live partial. When the probe surfaces nothing (this session), the window stays at the snappy 1472 ms base and a long sentence is cut at the first thinking pause. The VAD already tracks `speech_frames` reliably — use it as a probe-independent trigger.

**Files:**
- Modify: `jarvis/audio/vad.py` — constructor params + the `utterances()` speech-frame branch (~line 277-279)
- Test: `tests/unit/audio/test_vad_turn_taking.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/audio/test_vad_turn_taking.py` (reuse the file's existing synthetic-frame helpers that feed `utterances()` with loud/quiet frames; mirror the existing "adaptive-patience block"). The intent of the assertions:

```python
@pytest.mark.asyncio
async def test_long_utterance_autonomously_extends_silence_window(make_vad, speech, silence):
    # A long active-speech run (>= long_utterance_speech_ms) with NO probe wired
    # must grow the effective silence window past the base, so a thinking pause
    # below the wider window does not end the turn.
    vad = make_vad(silence_ms=1500, probe_callback=None,
                   long_utterance_speech_ms=2000, long_utterance_silence_ms=3000)
    base = vad._silence_frames
    # Feed ~2.5 s of continuous speech (no probe involved).
    await _drive(vad, speech(ms=2500))
    assert vad._effective_silence_frames > base
    # And it matches the requested wider window.
    assert vad._effective_silence_frames == max(1, 3000 // 32)


@pytest.mark.asyncio
async def test_short_command_stays_snappy(make_vad, speech):
    # A short command (< long_utterance_speech_ms of speech) must NOT widen the
    # window — the anti-confirmation-fatigue snappy default is preserved.
    vad = make_vad(silence_ms=1500, probe_callback=None,
                   long_utterance_speech_ms=2000, long_utterance_silence_ms=3000)
    base = vad._silence_frames
    await _drive(vad, speech(ms=900))   # ~"open Chrome"
    assert vad._effective_silence_frames == base


@pytest.mark.asyncio
async def test_patience_resets_at_next_speech_start(make_vad, speech, silence):
    # The autonomous grant must not leak across utterances.
    vad = make_vad(silence_ms=1500, probe_callback=None,
                   long_utterance_speech_ms=2000, long_utterance_silence_ms=3000)
    await _drive(vad, speech(ms=2500) + silence(ms=1600))  # long turn ends
    await _drive(vad, speech(ms=300))                       # new short turn starts
    assert vad._effective_silence_frames == vad._silence_frames
```

> Implementation note for the worker: if the file has no `_drive`/`speech`/`silence` helpers, add minimal ones that build int16 PCM frames at `VAD_SAMPLE_RATE` (loud sine for speech, zeros for silence) and pump them through `vad.utterances()`. Keep them local to the test module.

- [ ] **Step 2: Run tests to verify they fail**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/audio/test_vad_turn_taking.py -k "autonomously or snappy or resets_at_next" -v`
Expected: FAIL — constructor rejects the new kwargs / window never widens.

- [ ] **Step 3: Add constructor parameters**

In `jarvis/audio/vad.py`, add two keyword params to `__init__` (alongside the existing probe params around line 57-61):

```python
        long_utterance_speech_ms: int = 2000,
        long_utterance_silence_ms: int = 3000,
```

and store them in `__init__` (near the probe-frame fields, ~line 96-102):

```python
        # Autonomous long-utterance patience (probe-independent). Once this much
        # ACTIVE speech has accumulated in the current utterance, the user is
        # clearly dictating a long request, not issuing a short command — grant
        # the wider silence window so a thinking pause is not cut. Fixes the
        # session-71f2d2de split where the STT probe never surfaced a partial,
        # so the probe-driven extend_silence_window never armed. Resets per
        # utterance via the same _extra_silence_frames=0 at speech start.
        self._long_utterance_speech_frames = max(1, long_utterance_speech_ms // 32)
        self._long_utterance_silence_ms = int(long_utterance_silence_ms)
```

- [ ] **Step 4: Arm it from the speech-frame branch**

In `jarvis/audio/vad.py`, in `utterances()`, inside the `if is_speech:` branch of the `else` (already-speaking) block, immediately after `speech_frames += 1` (currently line 278), add:

```python
                        # Probe-independent patience: a long active-speech run
                        # means a long dictation — widen the natural silence
                        # window so a mid-sentence thinking pause is not cut.
                        # extend_silence_window only ever grows and is reset at
                        # the next speech start, so short commands stay snappy.
                        if speech_frames >= self._long_utterance_speech_frames:
                            self.extend_silence_window(self._long_utterance_silence_ms)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/audio/test_vad_turn_taking.py -v`
Expected: PASS (new tests + the whole existing turn-taking suite, including the probe-driven adaptive-patience block, which is unchanged).

- [ ] **Step 6: Commit (hunk-isolated)**

```bash
git add jarvis/audio/vad.py tests/unit/audio/test_vad_turn_taking.py
git commit -m "fix(voice): VAD arms long-utterance patience from speech frames, not only the STT probe

The 3000 ms patience only armed when the STT probe surfaced a live partial; in
session 71f2d2de it never did, so a long request was cut at the first pause
(2976 ms < the patience that never armed). Grant the wider window once enough
active speech has accumulated — probe-independent, reset per utterance, short
commands stay snappy."
```

---

## Task 3: Bug A / Layer 3 — ContinuationWindow freezes its grace on speech resume

**Why:** After Turn 1's brain call was aborted by the continuation interrupt, the 2500 ms grace ran while the user was *thinking*, and `try_recombine` checked it only when Turn 2 was *finalized* (~3 s later) → expired → fresh turn. Freeze the deadline the moment the user resumes speaking, so a slow follow-up still recombines.

**Files:**
- Modify: `jarvis/speech/continuation_window.py` — add `note_speech_resumed()`
- Modify: `jarvis/speech/pipeline.py` — call it from the speech-start handler
- Test: `tests/unit/speech/test_continuation_window.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/speech/test_continuation_window.py` (the window takes an injected `clock`, so time is deterministic — no sleeps):

```python
from jarvis.speech.continuation_window import ContinuationWindow


def _win(grace_ms=2500):
    clk = {"t": 0}
    w = ContinuationWindow(grace_ms=grace_ms, clock=lambda: clk["t"])
    return w, clk


def test_speech_resume_within_grace_freezes_deadline():
    w, clk = _win(grace_ms=2500)
    w.note_dispatch("erster teil", continued=False)
    w.mark_idle()                       # grace starts at t=0 → deadline 2.5 ms-units
    clk["t"] = 1_000 * 1_000_000        # 1 s later, still within grace
    w.note_speech_resumed()             # user starts the follow-up → freeze
    clk["t"] = 5_000 * 1_000_000        # finalization arrives 4 s after dispatch
    assert w.try_recombine("zweiter teil") == "erster teil zweiter teil"


def test_speech_resume_after_grace_does_not_revive():
    w, clk = _win(grace_ms=2500)
    w.note_dispatch("erster teil", continued=False)
    w.mark_idle()
    clk["t"] = 3_000 * 1_000_000        # already expired (> 2.5 s)
    w.note_speech_resumed()             # too late — must not resurrect
    assert w.try_recombine("zweiter teil") is None


def test_speech_resume_while_in_flight_is_noop():
    w, clk = _win()
    w.note_dispatch("erster teil", continued=False)  # deadline None (in flight)
    w.note_speech_resumed()                          # no crash, still in flight
    assert w.try_recombine("zweiter teil") == "erster teil zweiter teil"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/speech/test_continuation_window.py -k "speech_resume" -v`
Expected: FAIL — `AttributeError: 'ContinuationWindow' object has no attribute 'note_speech_resumed'`.

- [ ] **Step 3: Add `note_speech_resumed()`**

In `jarvis/speech/continuation_window.py`, add this method after `mark_idle` (after line 77):

```python
    def note_speech_resumed(self) -> None:
        """The user started speaking again — freeze the grace countdown.

        The grace started at turn end (``mark_idle``) measures THINKING silence.
        Once the user is actually forming the follow-up, the clock must not keep
        running against them: a slow-to-finalize continuation would otherwise
        miss ``try_recombine`` even though it began well inside the grace
        (live bug 2026-06-18, session 71f2d2de: ~3 s to formulate the next
        fragment > the 2.5 s grace, so it became a fresh turn). Re-enters the
        'in flight' state (deadline cleared) — but ONLY while the window is
        still armed and not already expired, so a genuinely late resume cannot
        resurrect a dead window. Fail-open / idempotent.
        """
        if not self.is_armed:
            return
        if self._deadline_ns is not None and self._clock() > self._deadline_ns:
            return
        self._deadline_ns = None
```

- [ ] **Step 4: Run the window tests to verify they pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/speech/test_continuation_window.py -v`
Expected: PASS.

- [ ] **Step 5: Wire it into the pipeline speech-start path**

In `jarvis/speech/pipeline.py`, locate the handler registered as the VAD `on_speech_start` callback (the method that runs when the user begins speaking; grep for `on_speech_start=` in the pipeline's VAD construction and follow it to its handler — it is the same place the turn transitions toward `USER_SPEAKING`). At the top of that handler, add a fail-open call:

```python
        # A resumed utterance freezes the continuation grace so a slow follow-up
        # still recombines with the in-flight/just-finished turn (session
        # 71f2d2de). Fail-open: continuation hygiene must never crash the turn.
        try:
            win = getattr(self, "_continuation_window", None)
            if win is not None:
                win.note_speech_resumed()
        except Exception:  # noqa: BLE001
            log.debug("continuation note_speech_resumed failed (non-fatal)", exc_info=True)
```

> Worker note: if the speech-start signal is delivered as an async event rather than a direct callback, place the call wherever the pipeline first observes `USER_SPEAKING` for a new fragment. The only requirement: it must run **before** the next `try_recombine` (which happens in `_maybe_recombine_continuation`, `pipeline.py:4423`).

- [ ] **Step 6: Run the speech suite to verify nothing regressed**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/speech/ -v`
Expected: PASS (continuation window + buffer + pipeline turn-taking tests).

- [ ] **Step 7: Commit (hunk-isolated — pipeline.py is shared)**

```bash
git add jarvis/speech/continuation_window.py jarvis/speech/pipeline.py tests/unit/speech/test_continuation_window.py
git commit -m "fix(voice): freeze continuation grace when the user resumes speaking

The 2500 ms recombine grace ran against the user's thinking pause and was
checked only at follow-up finalization, so a ~3 s reformulation (session
71f2d2de) expired the window and split the sentence. note_speech_resumed()
freezes the deadline on speech start while still armed and unexpired."
```

---

## Final verification (run after all three tasks)

- [ ] **Targeted suites**

```bash
& "C:\Program Files\Python311\python.exe" -m pytest \
  tests/unit/plugins/tool/test_computer_use_tool.py \
  tests/unit/audio/test_vad_turn_taking.py \
  tests/unit/speech/ \
  tests/unit/brain/test_output_filter.py -v
```
Expected: all PASS.

- [ ] **Lint the touched files only**

```bash
& "C:\Program Files\Python311\python.exe" -m ruff check \
  jarvis/plugins/tool/computer_use_tool.py jarvis/audio/vad.py \
  jarvis/speech/continuation_window.py jarvis/speech/pipeline.py
```
Expected: clean (or only pre-existing warnings unrelated to the edits).

- [ ] **Restart the app to load the fixes** (editable install picks up source, but the running process must restart):
  `POST /api/settings/restart-app` — NOT `Stop-Process` (Access Denied under the tray `pythonw.exe`).

- [ ] **Live (maintainer) mic check** — these are runtime-behavior fixes; CI cannot prove the VAD timing or the spoken ACK. Confirm: (1) a long, paused request stays one turn; (2) a Computer-Use dispatch speaks/shows "Mach ich — ich erledige das direkt am Bildschirm …" and no English instruction bubble appears.

---

## Self-Review notes (author)

- **Spec coverage:** Bug C → Task 1. Bug A Layer 1 → Task 2. Bug A Layer 3 → Task 3. Layer 2 intentionally excluded with rationale. Out-of-scope items (token leak, Telegram read tool) listed, not silently dropped.
- **Type consistency:** new VAD kwargs `long_utterance_speech_ms` / `long_utterance_silence_ms` are referenced identically in tests and impl; `note_speech_resumed` signature matches across window impl, tests, and the pipeline call site.
- **Shared-tree discipline:** every commit is pathspec-scoped; never `git add -A`. Task 3's `pipeline.py` edit is a single isolated hunk.
- **No-LLM-in-voice-path (AP-11) preserved:** the ACK is a static dict lookup; the VAD and window changes are pure arithmetic.
