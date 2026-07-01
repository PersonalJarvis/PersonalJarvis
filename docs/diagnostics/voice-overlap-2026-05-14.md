# Voice-Output-Overlap — Root-Cause Diagnosis

**Date:** 2026-05-14
**Branch:** `main`
**Commits in scope:** through `0715df8d`
**Status:** Diagnosis only — no fix applied
**Reproduced:** Yes (live log evidence, multiple turns)

---

## 1. Symptom

Two TTS audio streams overlap on the speakers within roughly the same voice
turn. The user hears two Jarvis voices simultaneously — typically a short
pre-acknowledgment ("I'm checking the status of the Spotify app on your system.")
overlapping with the first sentence of the main answer. Both overlapping
phrases are LLM-generated text (not pre-rendered fillers), so the bug can
sit anywhere on the path from LLM-emit to speaker output.

## 2. Root Cause (one-sentence)

`AudioPlayer.play_chunks()` has **no global mutex** — the Pre-Thinking Flash-
Brain pre-ack path (`_on_announcement`) and the main streaming-brain answer
path (`_brain_streaming → _speak`) both call `self._player.play_chunks(...)`
concurrently when the suppress-gate fails to win the race against the main
brain's first emitted sentence. The two calls open two independent
`sd.OutputStream` instances and feed them in parallel; WASAPI shared-mode
mixes both signals on the same output device.

## 3. Evidence — log replay of one offending turn

File: `data/jarvis_desktop.log`, lines 19990–20069 (turn at 19:36:43 .. 19:37:01)

| Timestamp | Event | Path |
|---|---|---|
| 19:36:43.840 | `VAD endpoint reason=max_utterance` | user finished speaking |
| 19:36:44.353 | `→ Brain …` + `ack_called_total provider=gemini` | both brain + Flash-Brain start |
| 19:36:45.036 | `ack_latency_ms_histogram value=681.937` | Flash-Brain returned (~682 ms) |
| 19:36:45.058 | `ack_emitted_total provider=gemini` | scrub passed, ack text ready, **suppress-gate begins polling** |
| 19:36:47.162 | `📢 Announcement: 'Ich analysiere das Protokoll …' (prio=normal lang=de)` | **suppress-gate window elapsed (2 100 ms)** → `AnnouncementRequested` published → `_on_announcement` → `tts.synthesize` → `play_chunks` (path A) |
| 19:36:49.507 | `turn-state: PROCESSING -> JARVIS_SPEAKING` | streaming-brain emitted its first sentence → `_speak` → `tts.synthesize` → `play_chunks` (path B) |
| 19:36:54.559 | HTTP `gemini-3.1-flash-tts-preview:generateContent 200` | path A TTS bytes back |
| 19:36:55.355 | HTTP `gemini-3.1-flash-tts-preview:generateContent 200` | path B TTS bytes back (≈ 800 ms later) |
| 19:36:59.722 | `AudioOutFirst published` | **first** `play_chunks` flushed its first chunk |
| 19:37:01.307 | `AudioOutFirst published` | **second** `play_chunks` flushed its first chunk (1.585 s later) |

Two `AudioOutFirst` events from the *same turn* prove two distinct
`play_chunks()` calls were alive at the same time, because
`first_audio_published` is a closure-local variable scoped to one
`play_chunks` invocation (`jarvis/audio/player.py:433, 453-459`).

Same pattern repeats throughout the log:

```
data/jarvis_desktop.log:
  8975 / 8988   (16:49:13.341 / 16:49:15.674 — Δ 2.3 s)
 11854 / 12018 / 12020   (17:08:12 / 17:08:40 / 17:08:46)
 13738 / 13742   (17:26:50.339 / 17:27:07.556)
 13856 / 13860   (17:27:26.107 / 17:27:41.295)
 14063 / 14067   (17:28:31.054 / 17:28:43.561)
 14822 / 14827   (17:33:10.803 / 17:33:13.520 — Δ 2.7 s)
 14937 / 14939   (17:33:27.701 / 17:33:29.206 — Δ 1.5 s)
 15546 / 15552   (17:37:34.156 / 17:37:42.313 — Δ 8.2 s, same turn)
 20013 + above (the 19:36 turn detailed above)
```

The Δ values cluster around 1.5–8 s, all within a single utterance, all
on turns where `ack_emitted_total` had been logged earlier.

## 4. Full trace — voice turn → speaker

```
User speech
  └─ VAD endpoint (audio.capture → speech.pipeline)
       └─ _handle_utterance (jarvis/speech/pipeline.py:1696)
            ├─ STT (final transcript)
            ├─ Skills direct trigger? (line 1810) ──► run skill (may TTS) ┐
            ├─ turn-state -> PROCESSING (line 1814)                       │
            ├─ FORK A: asyncio.create_task(_spawn_flash_brain_ack(...))   │
            │           (line 1830, "flash-brain-ack")                    │
            │     └─ jarvis/speech/pipeline.py:1045-1116                  │
            │          ├─ ack = await ack_brain.run(...)                  │
            │          ├─ suppress-gate poll (lines 1085-1103)            │
            │          │     ◆ 100-ms steps, max 2000 ms,                 │
            │          │       drops ack only if state ∈                  │
            │          │       {JARVIS_SPEAKING, LISTENING, IDLE}         │
            │          └─ publish AnnouncementRequested(                  │
            │               source_layer="brain.ack_brain",               │
            │               priority="normal", kind="preamble")           │
            │                ▼                                            │
            │           _on_announcement (line 974) ◄────────── Bus       │
            │             ├─ priority=="interrupt"? → stop player (1006)  │
            │             │   ◆ But ack uses priority="normal", so NO     │
            │             │     pre-stop happens.                         │
            │             ├─ scrub_for_voice (1020)                       │
            │             ├─ tts.synthesize(...)         (1038/1040)      │
            │             └─ self._player.play_chunks   (line 1041) ──► speaker
            │                                                              │
            └─ FORK B: streaming path (line 1839, "if _streaming_enabled") │
                  └─ _brain_streaming (line 2112)                          │
                       └─ async for chunk in brain.generate_stream():      │
                            └─ on sentence boundary:                       │
                                 ├─ scrub_for_voice                        │
                                 ├─ if not spoken_anything:                │
                                 │     turn-state -> JARVIS_SPEAKING       │
                                 └─ await self._speak(sentence)            │
                                       │  jarvis/speech/pipeline.py:2236   │
                                       ├─ tts.synthesize(...)              │
                                       ├─ asyncio.create_task(             │
                                       │   self._player.play_chunks(...))  │
                                       │   (line 2257) ────────────────► speaker
                                       └─ asyncio.create_task(_barge_monitor)

    Other speak sources on the same player (not in this turn but on the
    same un-synchronised resource):
      ├─ _on_background_completed (line 1118)    → player.stop + play_chunks
      ├─ _on_spawn_announcement   (line 1177, currently muted)
      ├─ task-ack PCM             (_brain_with_ack, line 2218; non-streaming path)
      ├─ chime/ack PCM            (lines 1591/1604/1606)
      └─ privacy ack              (line 1795)
```

### The unsynchronised resource

`jarvis/audio/player.py` defines `class AudioPlayer` (line 231).
`play_chunks` (line 391) and `play_pcm` (line 280) are plain `async def`
methods that wrap `asyncio.to_thread(...)`. There is:

- **no `asyncio.Lock`**,
- **no `threading.Lock`**,
- **no internal queue**,
- **no "is busy" flag**.

A concurrent caller opens a fresh `sd.OutputStream` (line 334) on the same
device. PortAudio in WASAPI shared mode mixes both streams; the user
hears both phrases at once.

### Why the suppress-gate doesn't save it

`_spawn_flash_brain_ack` polls `self._turn_state` every 100 ms for
`suppress_if_brain_faster_than_ms` (default 2000 ms,
`jarvis/speech/pipeline.py:1085-1103`). It drops the ack only if the
state has *already* moved out of `PROCESSING`. Two distinct races defeat
it:

1. **Inter-poll race.** The 100-ms granularity leaves a window in which
   the brain can flip `PROCESSING → JARVIS_SPEAKING` *and start TTS*
   between two polls. The next poll catches it, but the gate doesn't
   re-check *after* the loop exits — once the loop ends "naturally"
   after `suppress_ms`, the ack is published unconditionally
   (`pipeline.py:1105-1116`).

2. **Window-edge race.** When the brain genuinely needs ≥ 2 s
   (long context, vision attachment, deep reasoning), the suppress
   window expires *before* the brain emits. The ack is published, TTS
   for the ack begins, and ≤ 2 s later the brain finishes — at which
   point `_brain_streaming` issues its own `play_chunks` while the
   ack stream is still flushing. This is the 19:36:45 → 19:36:49 → 59.7
   → 01.3 cascade in §3.

`priority="normal"` on the announcement (line 1110) is also load-bearing:
only `priority="interrupt"` would call `self._player.stop()` before
synthesising (line 1006). Because the ack lands first, "interrupt" would
be the wrong direction anyway — the main answer would still need to
interrupt the ack, not the other way round.

## 5. Hypotheses considered and ruled out

| Hypothesis | Status | Why ruled out |
|---|---|---|
| Two LLM calls generate one combined text containing both phrases | **Ruled out** | Distinct `📢 Announcement: ...` log line and `JARVIS_SPEAKING` transition prove two *separate* text sources. Two distinct HTTP TTS calls are visible in §3. |
| Same TTS provider is called twice for the same text | **Ruled out** | The Announcement text ("Ich prüfe den Status der Spotify-App …") and the brain answer come from different LLM endpoints (Gemini Flash Lite for ack, Grok/Gemini for main brain).  <!-- i18n-allow --> |
| `play_chunks` re-enters because of a barge-in restart | **Ruled out** | Barge-in calls `self._player.stop()` (line 2267) and sets `barged=True`; the calling `_speak` returns instead of issuing a second `play_chunks`. No `🛑 Barge-in` log in the offending turns. |
| Skill-direct-trigger emits its own ack while main brain also speaks | **Possible but secondary** | `_try_skill_direct_trigger` returns early on a hit and *replaces* the brain call — no race in that branch. But if a skill were to publish an Announcement without returning early, the same overlap mechanism would apply. Not the cause of the 19:36 turn since no skill log entry is present. |
| Background-completed announcement collides with active playback | **Possible adjacent bug** | `_on_background_completed` *does* call `player.stop()` before its own playback (line 1165), so that path *intentionally* interrupts. Not the source of the steady-state Flash-Brain × main-brain overlap, but the same architectural gap (no per-player mutex) means a long-running playback can still be cut off mid-sentence by a Background-completed event. |
| Two simultaneous `_speak` calls inside `_brain_streaming` (sentence after sentence) | **Ruled out** | `_brain_streaming` does `await self._speak(...)` per sentence (line 2168) — strictly sequential within that coroutine. No internal overlap on the streaming path alone. |
| Double subscriber to `AnnouncementRequested` | **Ruled out** | `EventBus._safe_dispatch` shows one subscriber, `_on_announcement`. The legacy `brain.router.ack` source is explicitly dropped when Flash-Brain is wired (lines 997-1005). |

## 6. Fix options

### Option A — Player-level serialisation (recommended)

Add an `asyncio.Lock` on `AudioPlayer` and acquire it around `play_chunks`
and `play_pcm`. Callers behave the same; the lock guarantees only one
audio stream is ever open at a time.

```python
# jarvis/audio/player.py
class AudioPlayer:
    def __init__(self, ...):
        ...
        self._play_lock = asyncio.Lock()

    async def play_chunks(self, chunks):
        async with self._play_lock:
            # existing body
            ...

    async def play_pcm(self, pcm, sample_rate=None):
        async with self._play_lock:
            # existing body
            ...
```

| Property | Value |
|---|---|
| Code-change surface | Two methods, single class. |
| Behaviour change | Concurrent callers queue up (FIFO) instead of mixing. |
| Latency impact | **Adds queue wait when overlap would have occurred**, i.e. *exactly* the bug case — by design. No latency impact when no overlap. |
| Risk | Medium — must verify barge-in still works (it calls `self._player.stop()` which doesn't touch the lock; `stop()` is synchronous via PortAudio, the awaiting `play_chunks` will release when its body returns). Also: if a caller acquires the lock then awaits indefinitely (e.g. a stuck TTS HTTP call), other audio is silenced until that completes. Mitigate with a `wait_for` timeout. |
| Architectural fit | High — `AudioPlayer` is the natural choke-point for "one speaker, one stream". |
| Side benefit | Also fixes any future overlap (background-completed × main, skill × main, etc.) for free. |

### Option B — Suppress-gate hardening at the pipeline level

Tighten `_spawn_flash_brain_ack` so it cannot publish once the main brain
has *also* started TTS, regardless of `_turn_state` timing.

1. Replace the timed poll with a `_brain_first_sentence_event:
   asyncio.Event` that `_brain_streaming` sets before its first `_speak`
   call.
2. The ack task `await asyncio.wait_for(event.wait(), timeout=suppress_ms / 1000)`
   and publishes only if the wait *timed out* (i.e. brain still silent).

| Property | Value |
|---|---|
| Code-change surface | `_handle_utterance`, `_brain_streaming`, `_spawn_flash_brain_ack`, plus an `Event` field on the pipeline. |
| Behaviour change | Ack is dropped the moment the brain commits to its first sentence — strictly tighter than `_turn_state` polling. |
| Latency impact | None — replaces polling with an event wait. |
| Risk | Low to medium — must be set/cleared per turn correctly; missed clear on a previous turn would gate forever. Also doesn't help when the brain *legitimately* takes ≥ `suppress_ms` and only finishes shortly after the ack starts — the post-window race in §4 still exists. |
| Architectural fit | Medium — solves the right race but in the wrong layer; the root architectural gap (unsynchronised player) remains. |

### Option C — `priority="interrupt"` on the ack + delay the main TTS until ack flushes

Have the Flash-Brain announcement publish with `priority="interrupt"` so
that any in-flight playback is stopped before the ack speaks, *and* have
`_brain_streaming` wait on an "ack-complete" event before its first
`_speak`. This makes the ack pre-emptive by contract.

| Property | Value |
|---|---|
| Code-change surface | `_spawn_flash_brain_ack`, `_brain_streaming`, plus a "current ack handle" on the pipeline. |
| Behaviour change | The ack always wins the start; the brain answer is *delayed* until the ack is done speaking. |
| Latency impact | **Adds the ack-playback duration (≈ 1.5–3 s) to user-perceived response latency on every turn that emits an ack.** That is exactly the latency that the suppress-gate was designed to avoid in the first place. |
| Risk | Medium — UX regression: the user already started hearing the ack and now has to wait for the *real* answer instead of getting an overlap that, while ugly, was at least informative. |
| Architectural fit | Low — fights against the existing "let them race, suppress if the brain wins" design. |

## 7. Recommendation

**Option A.** It is the smallest, most localised change; it solves the
overlap at the only resource that can actually be contended (the speaker)
rather than chasing every producer; and it is robust against future
additions (skills, background-completed announcements, voice-control
acks) that would each otherwise need their own coordination logic.

Open follow-ups for the implementation phase:

- Decide the lock's wait policy: pure FIFO vs. last-writer-wins with
  `player.stop()`. FIFO is safer to land first; the existing
  `priority="interrupt"` path can later acquire the lock with a `stop()`
  preamble to keep its semantics.
- Audit the synchronous `player.stop()` call sites (lines 1008, 1165,
  1367, 2267) — they don't hold the lock today; under Option A they
  should be safe because `stop()` only signals PortAudio, but the
  awaiting `play_chunks` body will still need to finish its current
  `stream.write()` block. Add a `play_lock` timeout (e.g. 8 s) to bound
  worst-case wait.
- Keep Option B's `Event`-based gate as a *cheap latency win on top* of
  Option A once Option A is verified — they are orthogonal, not
  alternatives.

## 8. Open questions

None blocking. All claims above are anchored to specific file:line
references or specific log timestamps. No "probably" / "somewhere"
language remains.

## 9. Reproduction recipe

The bug reproduces on essentially every voice turn that satisfies all
of:

1. `[ack_brain].provider = gemini` (or any provider that succeeds).
2. The user's utterance triggers a real Flash-Brain ack (not a smalltalk
   suppression).
3. The main brain takes ≥ `suppress_if_brain_faster_than_ms` (default
   2000 ms) to emit its first sentence. With Vision-Inject + Wiki
   context this is the *common* path, not the corner case.

Grepping `data/jarvis_desktop.log` for pairs of `AudioOutFirst published`
within a 10-second window inside one turn yields dozens of hits, all
matching the pattern in §3.
