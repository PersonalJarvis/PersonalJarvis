# Voice Latency Collapse — Root-Cause Fix Implementation Plan

> **STATUS 2026-06-08: ALL 4 WAVES IMPLEMENTED + TESTED (TDD).** Wave 1 (audio playback watchdog), Wave 2 (router-vision decoupled from Computer-Use + OFF by default, live `jarvis.toml` flipped), Wave 3 (vision-collect + backup-provider timeouts), Wave 4 (Computer-Use offloaded off the voice turn). 20 new tests + full audio/speech/brain unit suites green; net −1 ruff across 9 changed files. Restart required to take effect (Python-only changes; no frontend rebuild). 3 pre-existing unrelated failures (parallel-session drift) left untouched.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the 60–156 s voice-answer hangs and roughly halve the per-turn think-time, so ordinary turns stay at 2–5 s and complex work is offloaded — cross-platform, no regressions.

**Architecture:** Four independent waves, each a self-contained, testable fix. Wave 1 (audio-playback self-heal) is the dominant fix and removes the 60–156 s hang. Wave 2 (router-vision gate) removes an always-on token tax that doubles think-time. Waves 3–4 are defensive hardening + a structural offload of Computer-Use from the voice critical path.

**Tech Stack:** Python 3.11, asyncio, `sounddevice`/PortAudio, pytest (asyncio_mode=auto), `tomlkit` config, FastAPI/React (UI signal only).

---

## Diagnosis Summary (evidence, not assumptions)

Two read-only deep-dive agents (code-path tracer + runtime forensics on the live machine) converged on this:

**The slow turns are EXCLUSIVELY Computer-Use / "open app" commands.** Ordinary Q&A turns are healthy at 2.7–6.7 s, served by Gemini (53× HTTP 200 today, **zero** brain 4xx/5xx). The Google-Cloud credit is fine and being consumed normally — **the API keys are NOT the cause** for the brain. (`claude-api` 401 / `grok` 403 in the log are background model-list metadata fetches only, not the voice path.)

**Root cause #1 — dominant (60–156 s):** On a Computer-Use turn the optimistic ACK ("Mach ich — ich erledige das direkt am Bildschirm") plays, then streaming-TTS playback **wedges against an unstable output device** and the only escape is a hard 120 s ceiling (`pipeline.py:187 _TTS_PLAYBACK_CEILING_S = 120.0`). Live proof: ACK at `19:17:16`, then 120 s of silence with **zero** brain/worker activity, then `WARNING | Streaming-TTS playback exceeded 120s ceiling — aborting` and an **empty** answer. Three occurrences today, **zero** in the May 30–Jun 2 "fast" log. The device throws 54 PortAudio `-99xx` errors today (`-9983 Stream is stopped`, `-9997 invalid sample rate`); the blocking `stream.write` (`player.py:527`) runs in a `to_thread` that cannot be cancelled — only `stream.abort()` unblocks it.

**Root cause #2 — always-on tax (doubles think-time):** `[brain.router.vision] enabled = true` injects a ≤500 KB screenshot into **every** router turn (`refresh_interval_s = 2.0`). `tokens_in` rose from ~25 k (May) to 50 k–143 k (June); the >80 k bucket averages 13.6 s think / 26.5 s total vs 8.6 s / 21.0 s for <20 k. Sinnlos on a headless VPS (no screen) — a cloud-first violation.

**Root cause #3 — latent landmines (not currently firing):** `_collect_vision_images` is awaited with **no timeout** (`manager.py:2682`); `openai`/`grok`/`openrouter` clients use the SDK default **600 s** read timeout (`openai.py:31`, `grok.py:39`, `openrouter.py:36`) — a hung backup provider could hold a turn for up to 90 s (stall ceiling) today and far longer if the stall guard were ever bypassed.

**Root cause #4 — structural (aligns with user intent):** Computer-Use is awaited INLINE on the voice path for up to 31 s (`manager.py:2086`, `harness_timeout_s + 1.0`). The user's stated design is "complex → Jarvis-Agents / Computer-Use in the background." It should ACK + offload, never block the spoken turn.

---

## File Structure

| File | Responsibility | Waves |
|---|---|---|
| `jarvis/audio/player.py` | Add write-progress counter + `abort_active()` recovery hook | 1 |
| `jarvis/speech/pipeline.py` | Replace flat 120 s ceiling with progress-based playback watchdog; lower hard backstop | 1 |
| `jarvis/core/config.py` | `router.vision.enabled` default → `false`; new `playback_stall_s` knob | 1, 2 |
| `jarvis/brain/manager.py` | Per-turn router-vision relevance gate; `asyncio.wait_for` around vision collection; CU offload | 2, 3, 4 |
| `jarvis/plugins/brain/{openai,grok,openrouter}.py` | Explicit 30 s read timeout on the SDK client | 3 |
| `jarvis.toml.example` | Document the new defaults | 1, 2 |
| `tests/unit/audio/test_player_stall_recovery.py` | Wave 1 unit tests | 1 |
| `tests/unit/speech/test_playback_watchdog.py` | Wave 1 pipeline watchdog tests | 1 |
| `tests/unit/brain/test_router_vision_gate.py` | Wave 2 gate tests | 2 |

---

# Wave 1 — Audio-Playback Self-Heal (DOMINANT FIX)

**Why first:** removes the 60–156 s hang entirely. Without it, nothing else matters to the user.

**Approach:** The player records a monotonic "last successful write" timestamp and a frames-written counter. A watchdog coroutine in the pipeline polls it during playback; if no audio frames are written for `playback_stall_s` (default 5 s) the watchdog calls `player.abort_active()` (PortAudio `Pa_AbortStream`, which unblocks the wedged `stream.write` in its worker thread), the turn unwinds, the session returns to IDLE, and the wake loop re-arms. The flat 120 s ceiling drops to a 20 s backstop. Cross-platform: pure asyncio + PortAudio abort.

### Task 1.1: Player write-progress counter

**Files:**
- Modify: `jarvis/audio/player.py` (`__init__`, `_write_samples:525-535`, add `abort_active`)
- Test: `tests/unit/audio/test_player_stall_recovery.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/audio/test_player_stall_recovery.py
import time
import numpy as np
import pytest
from jarvis.audio.player import AudioPlayer


class _FakeStream:
    """Stand-in for sd.OutputStream: write() blocks until released."""
    def __init__(self):
        self.aborted = False
        self._block_forever = False

    def write(self, chunk):
        if self._block_forever:
            while self._block_forever and not self.aborted:
                time.sleep(0.01)
        return False  # not underflowed

    def abort(self):
        self.aborted = True
        self._block_forever = False

    def close(self):
        pass


def test_write_progress_advances_on_each_subblock():
    player = AudioPlayer.__new__(AudioPlayer)
    player._init_progress()  # new helper
    stream = _FakeStream()
    arr = np.zeros(48000, dtype=np.int16)  # 1 s @ 48k
    before = player.frames_written
    player._write_samples(stream, arr, 48000, 48000)
    assert player.frames_written > before
    assert player.last_write_ns > 0


def test_abort_active_unblocks_wedged_stream():
    player = AudioPlayer.__new__(AudioPlayer)
    player._init_progress()
    stream = _FakeStream()
    player._active_stream = stream
    player.abort_active()
    assert stream.aborted is True
    assert player._active_stream is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/audio/test_player_stall_recovery.py -v`
Expected: FAIL — `AttributeError: '_init_progress'` / `frames_written` / `abort_active` not defined.

- [ ] **Step 3: Implement progress counter + abort hook in `player.py`**

In `__init__` (and a reusable `_init_progress` so `__new__`-built test instances work):

```python
def _init_progress(self) -> None:
    self.frames_written: int = 0
    self.last_write_ns: int = 0
```

Call `self._init_progress()` at the end of `AudioPlayer.__init__`.

In `_write_samples`, after `underflowed = stream.write(chunk)` (currently line 527), record progress:

```python
            underflowed = stream.write(chunk)
            self.frames_written += chunk.shape[0]
            self.last_write_ns = time.monotonic_ns()
```

(`import time` at module top if not present.)

Add a public recovery hook next to `stop()`:

```python
def abort_active(self) -> None:
    """Force-abort the live OutputStream to unblock a wedged ``stream.write``.

    PortAudio's blocking write runs in a worker thread that Python cannot
    cancel; ``Pa_AbortStream`` (``stream.abort()``) is the only way to make it
    return. Called by the pipeline playback watchdog on a device stall so the
    voice turn can unwind and the session can re-arm. Idempotent.
    """
    stream = self._active_stream
    self._active_stream = None
    self._active_source_rate = None
    self._active_device_rate = None
    if stream is not None:
        try:
            stream.abort()
        except Exception as exc:  # noqa: BLE001
            log.debug("abort_active: stream.abort() failed: %s", exc)
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/unit/audio/test_player_stall_recovery.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/audio/player.py tests/unit/audio/test_player_stall_recovery.py
git commit -m "feat(audio): write-progress counter + abort_active recovery hook"
```

### Task 1.2: Pipeline playback watchdog (replace flat 120 s ceiling)

**Files:**
- Modify: `jarvis/speech/pipeline.py:187` (constant), `:4505-4564` (`_speak_streaming` wait), `:4854-4885` (`_speak` wait)
- Test: `tests/unit/speech/test_playback_watchdog.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/speech/test_playback_watchdog.py
import asyncio
import time
import pytest
from jarvis.speech.pipeline import _playback_progress_stalled


def test_progress_stalled_true_when_no_writes():
    last_write_ns = time.monotonic_ns() - int(6e9)  # 6 s ago
    assert _playback_progress_stalled(last_write_ns, stall_s=5.0) is True


def test_progress_stalled_false_when_recent_write():
    last_write_ns = time.monotonic_ns() - int(1e9)  # 1 s ago
    assert _playback_progress_stalled(last_write_ns, stall_s=5.0) is False


def test_progress_stalled_false_before_any_write():
    # last_write_ns == 0 means playback hasn't produced its first frame yet;
    # the brain/producer stall guard owns that window, not this watchdog.
    assert _playback_progress_stalled(0, stall_s=5.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/speech/test_playback_watchdog.py -v`
Expected: FAIL — `ImportError: cannot import name '_playback_progress_stalled'`.

- [ ] **Step 3: Implement the helper + watchdog**

Add a module-level pure helper near the ceiling constant (`pipeline.py:~188`):

```python
def _playback_progress_stalled(last_write_ns: int, stall_s: float) -> bool:
    """True when audio frames stopped reaching PortAudio for ``stall_s``.

    ``last_write_ns == 0`` (no frame yet) is NOT a stall — the first-token /
    producer window is owned by the brain stall guard. Only a *mid-playback*
    gap (device wedge) trips this. Cross-platform: a healthy 60 ms sub-block
    write completes far inside ``stall_s``; only a wedged device exceeds it.
    """
    if last_write_ns <= 0:
        return False
    return (time.monotonic_ns() - last_write_ns) >= int(stall_s * 1e9)
```

Lower the hard backstop and add the stall knob (`pipeline.py:187` + `__init__`):

```python
_TTS_PLAYBACK_CEILING_S: float = 20.0   # was 120.0 — backstop only; the
# progress watchdog below catches a device wedge in ~5 s. 20 s still clears
# the longest legitimate single spoken turn.
_TTS_PLAYBACK_STALL_S: float = 5.0      # mid-playback no-frame gap → abort+recover
```

In both playback waits (`_speak_streaming` ~4534 and `_speak` ~4876), replace the single `asyncio.wait(..., timeout=ceiling)` with a poll loop that also watches player progress. Concretely, factor the wait into a helper `_await_playback(play_task, extra_tasks)` used by both sites:

```python
async def _await_playback(self, play_task, extra_tasks):
    """Wait for playback, aborting on a device stall well before the ceiling."""
    ceiling = getattr(self, "_speak_playback_ceiling_s", _TTS_PLAYBACK_CEILING_S)
    stall_s = getattr(self, "_speak_playback_stall_s", _TTS_PLAYBACK_STALL_S)
    poll = 0.25
    start = time.monotonic()
    watch = {play_task, *extra_tasks}
    while True:
        done, _pending = await asyncio.wait(
            watch, timeout=poll, return_when=asyncio.FIRST_COMPLETED
        )
        if done:
            return done
        player = getattr(self, "_player", None)
        last_write = getattr(player, "last_write_ns", 0) if player else 0
        if _playback_progress_stalled(last_write, stall_s):
            log.warning(
                "TTS playback stalled (no audio frames for %.1fs) — "
                "aborting device + unwinding turn (device wedge recovery).",
                stall_s,
            )
            if player is not None and hasattr(player, "abort_active"):
                player.abort_active()
            else:
                self._player.stop()
            return set()  # treat as aborted
        if (time.monotonic() - start) >= ceiling:
            log.warning(
                "TTS playback exceeded %.0fs ceiling — aborting.", ceiling
            )
            self._player.stop()
            return set()
```

Then at both call sites replace the `asyncio.wait({play_task, barge_task, hangup_task}, timeout=ceiling, ...)` block with:

```python
        try:
            done = await self._await_playback(play_task, {barge_task, hangup_task})
            if not done:
                pass  # already aborted + logged inside _await_playback
            elif hangup_task in done and not hangup_task.cancelled():
                log.info("📵 Hangup während TTS — Turn abbrechen")
                barged = True
                self._player.stop()
            elif barge_task in done and not barge_task.cancelled() and barge_task.result():
                log.info("🛑 Barge-in — stoppe TTS-Playback")
                barged = True
                self._player.stop()
            elif play_task in done and not play_task.cancelled():
                exc = play_task.exception()
                if exc is not None:
                    log.exception("Streaming-Playback-Fehler: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("Streaming-TTS-Turn-Fehler: %s", exc)
        finally:
            ...  # unchanged
```

Wire the knobs in `__init__` next to `self._speak_playback_ceiling_s` (~820):

```python
        self._speak_playback_ceiling_s = _TTS_PLAYBACK_CEILING_S
        self._speak_playback_stall_s = _TTS_PLAYBACK_STALL_S
```

- [ ] **Step 4: Run tests**

Run: `py -3.11 -m pytest tests/unit/speech/test_playback_watchdog.py tests/unit/audio/test_player_stall_recovery.py -v`
Expected: PASS.

- [ ] **Step 5: Regression run + commit**

Run: `py -3.11 -m pytest tests/unit/speech/ -q`
Expected: no new failures.

```bash
git add jarvis/speech/pipeline.py tests/unit/speech/test_playback_watchdog.py
git commit -m "fix(voice): progress-based playback watchdog — device wedge recovers in ~5s not 120s"
```

### Task 1.3: Manual live verification

- [ ] Restart Jarvis (`run.bat`), issue an "open app" voice command, and confirm: ACK plays, and if the device wedges the turn unwinds within ~5 s (log shows `TTS playback stalled … device wedge recovery`) and "Hey Jarvis" still works afterward. Capture the log lines.

---

# Wave 2 — Router-Vision Gate (always-on token tax)

**Why:** halves think-time on ordinary turns and restores cloud-first (a VPS has no screen). The screenshot should ride along ONLY on vision-relevant turns.

### Task 2.1: Default `router.vision.enabled` → false

**Files:**
- Modify: `jarvis/core/config.py` (the `RouterVision`/`router.vision` model — default `enabled`)
- Modify: `jarvis.toml.example` (document the new default + when to enable)
- Test: `tests/unit/brain/test_router_vision_gate.py` (create)

- [ ] **Step 1: Read first** — `grep -n "class.*Vision\|router\|enabled" jarvis/core/config.py` to find the exact model and field, and confirm `ConfigDict(extra="allow")` is present (AP-16).

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/brain/test_router_vision_gate.py
from jarvis.core.config import JarvisConfig

def test_router_vision_disabled_by_default():
    cfg = JarvisConfig()
    assert cfg.brain.router.vision.enabled is False
```

- [ ] **Step 3: Run** `py -3.11 -m pytest tests/unit/brain/test_router_vision_gate.py -v` → FAIL.

- [ ] **Step 4:** Flip the model default to `enabled: bool = False`. Update `jarvis.toml.example` with a comment: "Per-turn screenshot injection. OFF by default (cloud-first; VPS has no screen). Enable only on a desktop where you want spatial/pointing answers."

- [ ] **Step 5: Run** → PASS. Commit `feat(brain): router-vision OFF by default (cloud-first; halves per-turn tokens)`.

### Task 2.2: Per-turn relevance gate (when enabled)

**Files:**
- Modify: `jarvis/brain/manager.py:2682` + `_collect_vision_images:3030-3110`
- Test: extend `tests/unit/brain/test_router_vision_gate.py`

- [ ] **Step 1: Read first** — `manager.py:3030-3110` to see how `vcfg` and `max_image_kb` are read, and find the existing intent helpers (`is_pointing_intent`, spatial/"what's on screen" detection) already imported in this module.

- [ ] **Step 2: Write the failing test** — assert `_should_inject_router_vision("wie spät ist es")` is `False` and `_should_inject_router_vision("was siehst du auf dem bildschirm")` is `True`.

- [ ] **Step 3:** Add `_should_inject_router_vision(text) -> bool` reusing the existing spatial/pointing detectors; short-circuit `_collect_vision_images` to return `()` when the turn is not vision-relevant (even if `enabled`). Keep the headless capability probe as an additional guard (no screen → never inject).

- [ ] **Step 4–5:** Run → PASS; commit `feat(brain): inject router-vision only on vision-relevant turns`.

---

# Wave 3 — Defensive Timeouts (latent landmines)

### Task 3.1: Cap `_collect_vision_images`

**Files:** `jarvis/brain/manager.py:2682`

- [ ] Wrap the call: `images = await asyncio.wait_for(self._collect_vision_images(...), timeout=2.5)` inside a `try/except (asyncio.TimeoutError, Exception)` that logs and falls back to `images = ()`. Test with a fake vision provider that sleeps 10 s → turn proceeds in ~2.5 s with no images. Commit `fix(brain): bound vision-image collection at 2.5s (no hot-path hang)`.

### Task 3.2: Explicit 30 s read timeout on openai/grok/openrouter clients

**Files:** `jarvis/plugins/brain/openai.py:31`, `grok.py:39`, `openrouter.py:36`

- [ ] Pass `timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)` (or the SDK's `timeout=30.0`) to each `AsyncOpenAI(...)` so a hung backup provider can't hold 600 s. Add a unit test asserting the constructed client's `.timeout.read == 30.0`. Commit `fix(brain): cap backup-provider SDK read timeout at 30s (was 600s default)`.

---

# Wave 4 — Computer-Use off the voice critical path (structural)

**Why:** the user's design is "complex → background." A screen action should ACK + run async and speak the result at the next turn boundary (AD-OE1/OE5), never block the spoken turn for 31 s.

### Task 4.1: Make the local Computer-Use harness fire-and-forget like `spawn_worker`

**Files:** `jarvis/brain/manager.py:2054-2110` (the `asyncio.wait_for(self._tool_executor.execute(tool, ...), timeout=harness_timeout_s + 1.0)` block)

- [ ] **Step 1: Read first** — `manager.py:2054-2110` and compare with the `spawn_worker` fire-and-forget pattern (`spawn_worker.py:479-484`) and the `WorkerCorrectionNeeded`/announcement readback path (`pipeline.py:_on_announcement`).
- [ ] **Step 2:** Replace the inline `await asyncio.wait_for(...)` with: emit the optimistic ACK, dispatch the harness via `asyncio.create_task`, and route its completion through the existing announcement bus so the result is spoken at the next Silero turn-boundary through `scrub_for_voice` (AD-OE5). The voice turn returns in < 1 s.
- [ ] **Step 3:** Test that a CU command returns an ACK string immediately (no 31 s await) and that a later `ActionResult`/announcement triggers a spoken readback. Commit `refactor(voice): Computer-Use offloaded — ACK now, speak result at next turn boundary`.

---

## Self-Review

- **Spec coverage:** RC#1 → Wave 1; RC#2 → Wave 2; RC#3 → Wave 3; RC#4 → Wave 4. All four diagnosed causes have a wave.
- **Cross-platform:** Wave 1 uses PortAudio `abort()` + asyncio (Win/mac/Linux). Wave 2 restores headless/VPS behavior. No new Windows-only deps. ✅
- **No leaks:** Wave 1 frees the wedged stream + session (no permanent "deaf" state); Wave 3 bounds backup-provider sockets. ✅
- **Type consistency:** `frames_written`, `last_write_ns`, `abort_active`, `_playback_progress_stalled`, `_await_playback`, `_should_inject_router_vision` used consistently across tasks.
- **Anti-patterns respected:** config default change keeps `ConfigDict(extra="allow")` (AP-16); no new `SUB_TOOLS`/spawn tool in a worker set (AP-14); scrub stays regex-only (AP-11); Wave 4 readback reads only Kontrollierer/announcement-signed text (ADR-0009).

## Definition of Done

1. An "open app" voice command never produces > ~8 s of silence; a device wedge recovers in ~5 s and "Hey Jarvis" still works after (Wave 1, live-verified).
2. Ordinary turn `tokens_in` back to ~25 k; think-time roughly halved (Wave 2).
3. No hot-path await can exceed its bound (Waves 1 + 3).
4. Full suite green: `py -3.11 -m pytest tests/unit/audio tests/unit/speech tests/unit/brain -q`.
