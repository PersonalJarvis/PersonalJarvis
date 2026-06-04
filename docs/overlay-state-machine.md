# Overlay State Machine

> Reference for the 8-state Overlay state model. Extracted from
> `OS-Level/OS-LEVEL_PLAN.md` §6 (State Model), §6.1 (States), §6.2
> (Transitions), §6.3 (Event-Sources), §6.4 (Latency-Budget) and AD-17
> (Coalescing).

---

## 1. The 8 States (Plan §6.1)

| State | Description | Glow-Visual | Mascot-Animation | `intensity` Default |
|---|---|---|---|---|
| **idle** | Nothing running | Glow off, mascot idle pulse | gentle 8-s sine breathing | 0.0 |
| **listening** | Wake word detected, STT running | Glow off (no PC-action) | mouth open, eyes alert | 0.6 |
| **thinking** | LLM inference running, no PC-action | Glow off (no PC-action) | head-tilt, dots above head | 0.7 |
| **typing** | Hauptjarvis types via keyboard input | **Glow on** (yellow conic-sweep), bottom-edge accent sweep | hands typing | 1.0 |
| **clicking** | Hauptjarvis clicks via mouse | **Glow on** (yellow), Ripple at click coord | finger-point | 1.0 |
| **speaking** | TTS output running | Glow off (no PC-action) | mouth movement synced to RMS | 0.8 |
| **error** | Recoverable error in Hauptjarvis | Glow shifts to amber/red flash 1× then off | confused expression | 1.0 |
| **hidden** | Overlay completely hidden (fullscreen, manual hide, feature disabled) | Glow off, mascot hidden | n/a | 0.0 |

**Critical Invariant — Plan §6.1:** Glow is **only** active in `typing` and
`clicking`. The other states change the mascot but not the edge-glow. The whole
point of the glow is "Jarvis is operating my computer right now" — using it for
listening/thinking would dilute that signal.

---

## 2. Transitions (Plan §6.2)

```
                ┌─────────┐
       ┌────────│  IDLE   │◄────────┐
       │        └────┬────┘         │ action_ended
       │             │ wakeword     │ utterance_done
       │             ▼              │
       │        ┌─────────┐         │
       │        │LISTENING│─────────┤
       │        └────┬────┘         │
       │             │ utterance    │
       │             ▼              │
       │        ┌─────────┐         │
       │   ┌───►│THINKING │─────────┤
       │   │    └────┬────┘         │
       │   │         │ tool_call    │
       │   │         ▼              │
       │   │    ┌─────────┐         │
       │   │    │ TYPING  │─────────┤   ← GLOW ON
       │   │    └────┬────┘         │
       │   │         │ click_event  │
       │   │         ▼              │
       │   │    ┌─────────┐         │
       │   │    │CLICKING │─────────┤   ← GLOW ON + RIPPLE
       │   │    └────┬────┘         │
       │   │         │ response_ready
       │   │         ▼              │
       │   │    ┌─────────┐         │
       │   └────│SPEAKING │─────────┘
       │        └────┬────┘
       │             │ recoverable error
       │             ▼
       │        ┌─────────┐
       └────────│  ERROR  │
                └─────────┘

Special transitions:
  any → HIDDEN  (fullscreen detected, manual hide, feature disabled)
  HIDDEN → idle (fullscreen ended, manual unhide, feature re-enabled)

Coalescing (AD-17):
  any → same state within 16 ms : ignored (1 frame @ 60 Hz)
  TYPING ↔ CLICKING : free transition (no intermediate IDLE step required)
```

Notes:
- **`HIDDEN` is reachable from any state** — fullscreen detection, manual hide
  via tray, or `[overlay].enabled = false` in `jarvis.toml` all funnel through
  the same path.
- **`TYPING ↔ CLICKING` is direct.** If a tool issues a keystroke and an
  immediate click, the overlay does not flicker through `idle`.
- **Click events do not coalesce.** Even rapid click bursts produce one ripple
  per click.

---

## 3. Event-Sources (Plan §6.3)

| Event | Source (in Hauptjarvis) | Trigger | Resulting Transition |
|---|---|---|---|
| `wakeword_detected` | `jarvis.speech.wakeword` | Porcupine / openWakeWord | `idle → listening` |
| `utterance_started` | `jarvis.speech.stt` | VAD starts | `listening` enter / refresh |
| `utterance_ended` | `jarvis.speech.stt` | VAD ends | `listening → thinking` |
| `inference_started` | `jarvis.brain.router` | Brain-Call started | `thinking` enter |
| `inference_done` | `jarvis.brain.router` | Brain-Call finished | `thinking → speaking` (or `→ typing/clicking` via tool) |
| `action_started{kind=typing}` | `OverlayBridge.action(...)` | Decorator/Context-Manager fired | `* → typing` |
| `action_ended` | `OverlayBridge` | Same | `typing|clicking → idle` |
| `click_event{x, y, monitor}` | `OverlayBridge.click(...)` | Direct emit after `pyautogui.click()` | `* → clicking` + ripple |
| `tts_started` | `jarvis.speech.tts` | TTS starts | `* → speaking` |
| `tts_ended` | `jarvis.speech.tts` | TTS ends | `speaking → idle` |
| `tts_audio_rms{rms_db}` | `jarvis.speech.tts` | Optional, for mascot mouth sync; 30 Hz | mascot-only update, no state change |
| `error{recoverable, message}` | `jarvis.core.errors` | Recoverable exception caught | `* → error → previous-state` |

**Implementer-Discretion:** The exact event names and signatures are up to the
implementer, as long as **every state transition is derivable from exactly one
event source**.

---

## 4. Latency-Budget (Plan §6.4)

| Transition | Budget Hauptjarvis-Event → Overlay-Visual |
|---|---|
| State-Change (any → any) | ≤ 50 ms |
| Click-Event → Ripple visible | ≤ 50 ms (perceptual threshold; Plan §1.4) |
| Cursor-Move → Trail-Point visible | ≤ 33 ms (1 frame @ 30 Hz, with 60 Hz cursor stream) |
| Typing-Indicator-Update | ≤ 100 ms (less critical, ambient) |
| Hauptjarvis-Crash → Overlay-Process killed | ≤ 1 s (Job-Object guarantee) |
| Overlay-Crash → Restart-Spawn started | ≤ 3 s (heartbeat timeout) |

Backing research: Forch et al. 2017 — mouse-interaction latency-perception
threshold ~60 ms; Pubnub blog — visual-perception ceiling ~13 ms but
acute-awareness at 75–100 ms. We pick **50 ms** as the safe target.

---

## 5. Coalescing (AD-17)

When two events of the **same type** arrive within 16 ms (= 1 frame @ 60 Hz),
the **later one is ignored**. Example: a race-condition that double-fires
`state=typing` will collapse to a single transition.

**Click events are never coalesced** — every click deserves a ripple even if
they arrive in rapid succession.

Coalescing happens at **send-time** in the Hauptjarvis bridge, **before**
queueing onto the IPC channel. This keeps the bandwidth budget honest even when
upstream event-sources misbehave.

---

## 6. Source of Truth

- **State enum + transition logic:** `OS-Level/src/overlay/state.py`
- **Pydantic models for `state` payloads:** `OS-Level/src/overlay/schema.py`
- **Renderer state-driver (CSS variable mapping):** `OS-Level/overlay-ui/src/state.ts`

When changing the state machine: update `state.py` first, then re-derive the
diagram above, update the Pydantic model, and bump the schema version in
`overlay/schema.py` if the wire format changes.
