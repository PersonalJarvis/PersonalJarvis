# Screen Context — architecture, flow, privacy plan, MVP roadmap

**Date:** 2026-07-29
**Status:** design approved, Wave 1 implemented
**Scope:** a local, on-demand screen-context service for the on-screen bar and
the live voice session, on Windows, macOS and Linux, extensible to a fourth
platform through the same adapter seam.

---

## 1. What this is

When the user says something that unambiguously asks Jarvis to *look* — "can you
see this?", "what does that say?", "look at the error" — Jarvis takes **one**
capture of the screen the user is actually working on, enriches it with the
active application, the window title and whatever visible UI text the platform's
accessibility layer exposes, filters it against the user's privacy rules, and
hands it to the running conversation. Then it is gone.

Everything about that sentence is a constraint:

| Constraint | Consequence in the design |
|---|---|
| **on demand** | One capture per trigger. No loop, no timer, no background sampler. |
| **unambiguous** | A three-valued intent verdict. Ambiguous → Jarvis *asks*, never captures. |
| **the screen the user is working on** | Monitor under the mouse cursor at trigger time; bar's monitor as fallback. |
| **filtered** | Redaction runs *before* the context leaves the process. |
| **then it is gone** | Ephemeral handle with a TTL, single consumption, no disk write without explicit consent. |

### Non-goals

- Continuous screen understanding / ambient awareness. That is a different
  feature with a different consent model, and it is explicitly out of scope.
- Driving the desktop. Acting on the screen is Computer-Use
  (`jarvis/cu/`, gated by `jarvis/brain/cu_gate.py`); Screen Context only
  *reads*, once, and never moves a cursor or presses a key.
- OCR as a primary text source. OCR is a supplement, off the critical path,
  used only where accessibility text is unavailable or empty.

### Relationship to what already exists

This is deliberately **not** a new capture stack. The platform primitives are
already in the tree and battle-tested; Screen Context is the policy layer above
them.

| Reused | What it gives us |
|---|---|
| `jarvis/platform/mouse.py` | Cursor position, per-OS, `None` on headless/Wayland. |
| `jarvis/platform/monitors.py` | `work_area_at`, primary resolution, virtual bounds. |
| `jarvis/platform/window_state.py` | Foreground window, title, pid, frame rect. |
| `jarvis/platform/window_capture.py` | Native per-window capture (macOS SCK). |
| `jarvis/vision/screenshot.py` | `capture_region`, DPI awareness, screen-recording probe. |
| `jarvis/vision/tree_factory.py` | `make_ui_tree_source()` → UIA / AX / AT-SPI / Null. |
| `jarvis/platform/permissions.py` | `PermissionId.SCREEN_RECORDING`, `.ACCESSIBILITY`. |

What is genuinely new: intent classification with an *ambiguous* verdict,
cursor-first monitor targeting, redaction, and an ephemeral single-use context
handle.

---

## 2. Architecture

### 2.1 Layering

Screen Context sits in its own package, `jarvis/screen_context/`, above the
platform seam and below the brain. It imports platform modules; nothing in
`jarvis/platform/` imports it. That direction is the 8-layer dependency rule
(CLAUDE.md §5) and it is what keeps the package testable without a display.

```
        voice session / bar / REST / brain tool
                        │
                        ▼
        ┌───────────────────────────────────────┐
        │  ScreenContextService  (service.py)   │  orchestration, TTL, consent
        └───────────────────────────────────────┘
           │        │         │          │
           ▼        ▼         ▼          ▼
        intent   targeting  uitext   redaction        ← policy, pure-ish, testable
           │        │         │          │
           ▼        ▼         ▼          ▼
        ┌───────────────────────────────────────┐
        │        ports.py  (Protocols)          │  ← the ONE seam
        └───────────────────────────────────────┘
                        │
     ┌──────────┬───────┴────────┬──────────────┐
     ▼          ▼                ▼              ▼
  Windows     macOS            Linux        (fourth OS)
  UIA/GDI   AX/SCK/Quartz   AT-SPI/X11      new adapter only
```

### 2.2 The seam: `ports.py`

Four `Protocol`s, each with a per-OS implementation and a logged null fallback.
Adding a fourth platform means writing four small classes and one line in each
factory — no change anywhere above the seam. That is the entire extensibility
claim, and it is testable: the service is constructed with fakes in every unit
test, and no test needs a screen.

| Port | Question it answers | Win | macOS | Linux | Absent |
|---|---|---|---|---|---|
| `CursorLocator` | Where is the pointer? | `GetCursorPos` | pynput/Quartz | pynput/X11 | `None` → bar fallback |
| `DisplayEnumerator` | Which monitors, which one holds a point? | mss + `MonitorFromPoint` | mss + Quartz | mss + xrandr | single virtual rect |
| `WindowProbe` | What is focused, titled, where? | Win32/DWM | Quartz/AX | xdotool/EWMH | empty `WindowFacts` |
| `SurfaceCapturer` | Give me these pixels, once. | GDI rect grab | ScreenCaptureKit | X11 root grab | `CaptureUnavailable` |
| `UiTextReader` | What text is visible? | UIA | AXUIElement | AT-SPI | empty, flagged |

Every port obeys the same two rules the existing platform seam already follows:
**never raise into the caller** and **degrade to a value that reads as "not
available"**, never to a value that reads as "nothing was there". The
distinction matters — a silent empty string would let the model narrate a blank
screen with confidence (the 2026-04-28 blank-desktop regression), so absence is
carried explicitly in `ScreenContext.degradations`.

### 2.3 Data model (`models.py`, all `frozen=True`)

- `VisualIntent` — `NONE` | `AMBIGUOUS` | `SCREEN` | `WINDOW`.
- `IntentVerdict` — the intent, the matched evidence, and the confidence signal.
- `CaptureTarget` — what will be captured: kind (`monitor` | `window`), bbox,
  monitor identity, window facts, and the *reason* it was chosen (for the log
  and for the receipt the user sees).
- `WindowFacts` — app name, window title, pid, frame rect.
- `ScreenContext` — the finished, redacted artifact: image bytes + mime +
  dimensions, `ui_text`, `WindowFacts`, `RedactionReport`, `captured_at_ns`,
  `expires_at_ns`, `degradations`.
- `RedactionReport` — what was removed and by which rule. Shipped *with* the
  context so the model is told the truth about its own evidence and the user
  can see it in the receipt.

`ScreenContext` holds bytes in memory and never a path — a path would be a file,
and a file is persistence.

---

## 3. Flow — from utterance to context

```
user speaks
    │
    ▼
[1] intent.classify(text, locale)
    ├── NONE      → normal text turn. No capture. No prompt. (the common case)
    ├── AMBIGUOUS → Jarvis asks one short question in the turn's language,
    │               arms a short confirmation window, and STOPS. No capture.
    └── SCREEN / WINDOW ↓
    │
    ▼
[2] permission check  (screen recording; accessibility for UI text)
    ├── denied → honest, actionable message naming the OS setting. STOP.
    └── granted ↓
    │
    ▼
[3] targeting.resolve()
    ├── WINDOW intent + a focused window → that window's frame rect
    └── otherwise → monitor under the cursor
                    ├── cursor unavailable → monitor under the bar
                    └── bar unavailable    → OS primary monitor
    │
    ▼
[4] indicator.show()   ← visible BEFORE the shutter, dismissed after
    │
    ▼
[5] capture ONCE  +  read window facts  +  read UI text (AX first, OCR only to fill a gap)
    │
    ▼
[6] redaction.apply()   image regions blacked out, text scrubbed, report built
    │
    ▼
[7] service stores an ephemeral handle (TTL, single consumption)
    │
    ▼
[8] the turn consumes it → brain sees image + text + facts + redaction report
    │
    ▼
[9] TTL expires or consumption completes → bytes dropped
```

Two details in that flow are load-bearing and easy to get wrong:

**The indicator precedes the shutter.** Showing it afterwards, or concurrently,
means there is a window in which a capture happened with no visible sign. The
service awaits the indicator's acknowledgement (bounded by a short timeout) and
only then grabs pixels. If the indicator cannot be shown at all, the capture
still proceeds *only* if the user has an alternative signal configured; the
default is to proceed and log the degradation, because a silently-failed
indicator must not brick the feature, but the degradation is surfaced in the
receipt. This is a deliberate trade-off, stated rather than hidden.

**Ambiguity does not capture, and does not silently drop the turn either.**
`AMBIGUOUS` produces a question, in the resolved output language, through the
one resolver (`jarvis/core/turn_language.py`) — never a per-layer phrase table
(CLAUDE.md §1). The user's next turn resolves it; a bare "yes" inside the
confirmation window promotes the *previous* utterance to `SCREEN`.

### 3.1 Intent classification

Deterministic, regex-based, O(1), no model call. An LLM classifier on the voice
path is exactly the latency tax AP-9 forbids, and it would make the "did it look
at my screen?" question unanswerable after the fact.

Three tiers of evidence, all defined per supported locale (de/en/es today, and a
new locale is a data entry, not code):

- **Explicit** → `SCREEN`: a look-verb bound to a screen object or a deictic
  ("look at this", "schau dir das an", "mira esto"), a screen/window noun with
  a demonstrative, a read-out request ("what does it say there").
- **Window-scoped** → `WINDOW`: the same, but naming the window/app/document
  ("in this window", "this tab", "the dialog").
- **Weak** → `AMBIGUOUS`: a bare deictic with no visual anchor ("what is that?",
  "and that one?"), or a look-verb with no object ("can you check?"). These are
  precisely the utterances a capture-happy heuristic gets wrong in both
  directions, so they get a question instead of a guess.
- Everything else → `NONE`.

Negative guards matter as much as the vocabulary. The existing `cu_gate` learned
this the hard way: product names containing a vehicle token ("Open AI", "context
window", "edge case") read as commands. Screen Context reuses that masking
approach before matching, and adds its own: "see" in "I see" / "you see" /
"let's see", "look" in "look into it" / "look for", "schauen wir mal", "a ver".

### 3.2 Targeting

The requirement is explicit and it differs from every existing capture path in
the tree: **the monitor under the mouse cursor at trigger time**, not the
foreground window's monitor. The two diverge constantly — the user reads an
error on the right screen while the focused window sits on the left.

```
cursor position ──► monitor containing that point  (MONITOR_DEFAULTTONEAREST
        │                                            semantics: a point in a
        │                                            layout gap maps to the
        │                                            nearest screen, never fails)
        │
   unavailable (headless / Wayland / no pynput)
        │
        ▼
bar position ─────► monitor containing the bar
        │
   bar not running / position unknown
        │
        ▼
OS primary monitor  (resolve_primary_monitor, honouring the main_monitor override)
```

The cursor is sampled **once, at trigger time**, and that sample is threaded
through the whole capture. Re-reading it later would let a mouse move between
decision and shutter change which screen gets captured — a race that would show
up as "it photographed the wrong monitor" and would be nearly impossible to
reproduce.

Window preference is a narrower rule: only a `WINDOW` verdict prefers the active
window, and only when the window's frame rect is readable and non-degenerate.
Otherwise it falls back to the selected monitor, because a failed window probe
must not produce a capture of nothing.

### 3.3 UI text

Accessibility first, through the existing `make_ui_tree_source()` factory, which
already resolves UIA / AX / AT-SPI / Null per platform. Nodes are filtered to
the target rect (a monitor capture should not carry text from a window on
another screen), stripped of `is_password` nodes at the source, and truncated to
a configured character budget.

OCR is a *supplement*, and only under three conditions simultaneously: the
accessibility path yielded (near-)nothing, an OCR backend is actually installed,
and the user enabled it. It never runs on the accessibility-rich path, because
it costs hundreds of milliseconds and adds transcription errors to text the OS
already knows exactly.

---

## 4. Permissions and privacy plan

### 4.1 Permissions

| OS | Capture | UI text | Failure mode |
|---|---|---|---|
| Windows | none required | none required | — |
| macOS | Screen Recording (TCC) | Accessibility (TCC) | Capture without the grant returns *wallpaper only*, with no error — so it is probed on every capture and refused honestly rather than returned as a successful blank. |
| Linux/X11 | none required | AT-SPI session | — |
| Linux/Wayland | no addressable global capture | AT-SPI | Refused with the X11/XWayland message; the compositor owns capture. |

Permission state is never cached across captures: macOS can revoke a grant while
the app runs. The probe is one call and it is the first thing after intent.

Every denial produces a message that names the exact setting to change and is
recoverable in-app (CLAUDE.md §3) — never a stack trace, never a silent no-op.

### 4.2 Privacy rules

Five layers, in the order they run:

1. **Consent to the feature.** `[screen_context].enabled`. Default is on for the
   explicit-intent path *only*; there is no configuration in which a capture
   happens without a matching utterance.
2. **App denylist.** If the target window's app or title matches a denylist
   entry (default entries cover password managers, banking, and private-browsing
   windows by title pattern), **no capture is taken at all**. Not captured and
   redacted — not captured. The user is told which rule blocked it.
3. **Region redaction.** Accessibility nodes marked `is_password`, plus nodes
   whose text matches a sensitive pattern, have their bounds filled with opaque
   black in the image before it leaves the process.
4. **Text scrubbing.** The aggregated UI text is run through the same pattern
   set; matches are replaced with a typed placeholder (`[redacted:card]`), never
   silently dropped, so the model knows something was there.
5. **Egress.** The context is handed to the session with a purpose tag, over the
   transport the brain provider already uses (TLS to the provider, in-process
   for a local one). Nothing is written to disk.

Default patterns ship for card numbers, IBANs, API-key shapes, and
`Authorization:`-style headers. They are configurable, additive, and each entry
carries a label that appears in the redaction report.

### 4.3 Retention

- Image bytes live in memory inside a `_Handle`, keyed by an opaque id.
- One consumption. `consume()` removes the handle; a second call gets nothing.
- TTL (default 120 s) sweeps unconsumed handles.
- `POST /api/screen-context/{id}/save` is the **only** path that writes a file,
  it requires an explicit call with an explicit destination, and it records the
  save in the session log so "I never agreed to that" is checkable.
- Nothing about a capture reaches the flight recorder except metadata: target
  kind, monitor identity, sizes, redaction counts, degradations. Never pixels,
  never scrubbed text.

This is a deliberate departure from `ScreenshotSource`, which writes every frame
to `data/flight_recorder/blobs/`. That behaviour is correct for Computer-Use
replay and wrong for this feature; Screen Context therefore does not use
`ScreenshotSource` at all, only the stateless `capture_region` primitive.

---

## 5. MVP roadmap

Priority order. Each wave is independently shippable and independently
verifiable — the repo convention (`feedback_plans_as_independent_chunks`).

### Wave 1 — the service and its seam ✅ *implemented*

`models.py`, `ports.py`, `intent.py`, `targeting.py`, `uitext.py`,
`redaction.py`, `service.py`, `[screen_context]` config, REST routes, unit
tests. Fully testable with fakes, no display required. Ships the four
non-maintainer paths: headless Linux degrades to an honest refusal, macOS
degrades on a missing grant, a fresh install works with any single brain key,
and no provider name appears anywhere in the package.

**Done when:** `pytest tests/unit/screen_context/ -q` is green and
`jarvis api screen-context capture` returns a redacted context on Windows and an
honest refusal in a `python:3.11-slim` container.

### Wave 2 — voice-session wiring

Replace the two-valued `vision_gate.should_attach_screenshot()` call sites with
the three-valued verdict; route `AMBIGUOUS` to a clarifying question through
`resolve_output_language`; make the router consume a `ScreenContext` handle
instead of the background `VisionContextProvider` observation. Add `es` to the
marker vocabulary (today's gate is de/en only, which violates §1's
equal-locales rule).

**Done when:** an ambiguous voice turn ("what does that say?") produces a
question, an explicit one ("look at this") produces a capture from the cursor's
monitor, and a plain content question ("what did we discuss?") produces neither
— verified in all three supported locales.

### Wave 3 — the indicator and the receipt

A capture indicator on the bar: a brief, unmistakable shutter affordance shown
before the grab, plus a receipt line in the transcript naming what was captured
("captured: right monitor, 2 regions redacted"). Reuses the existing
`jarvis/cu/indicator/` sidecar shape rather than inventing a second overlay
mechanism, extended to the non-Windows backends it currently lacks.

**Done when:** every capture is preceded by a visible indicator on all three
OSes, or by a logged degradation naming why it could not be shown.

### Wave 4 — settings surface

A Screen Context group in Settings: master switch, denylist editor, redaction
pattern editor with a live test field, OCR toggle, TTL. Recoverable entirely
in-app, no TOML editing (§3).

### Wave 5 — OCR supplement

Optional local OCR behind a capability probe, used only when accessibility text
is empty. Off by default, base install stays torch-free.

---

## 6. Anti-patterns this design is written against

| Register entry | How this design avoids it |
|---|---|
| AP-9 (awareness on the voice path) | Intent is regex, O(1). No model call, no network, no tree walk before the verdict. |
| AP-21/22 (provider coupling) | The package names no provider. It produces bytes + text; whatever brain is configured consumes them. |
| AP-23 (maintainer's box as baseline) | Every port has a null fallback; the whole package is unit-tested with fakes and imports cleanly on a slim container. |
| AP-26 (init on the boot path) | Nothing initializes at import. Ports are constructed lazily on first capture. |
| AP-30 (silent exception handlers) | Every degradation is both logged *and* carried in `ScreenContext.degradations` to the user. Absence is never rendered as emptiness. |
| AP-31 (config that nothing reads) | Every `[screen_context]` key is read in Wave 1 code; the settings UI in Wave 4 adds no key that is not already wired. |
| §1 (English artifacts) | Package, docs, log lines, errors and UI strings are English; the de/es vocabulary in `intent.py` is speech-input *matching data*, which is the closed-list exception, and it is marked as such. |
| §4 (dynamic brand) | No user-visible string hardcodes an assistant name. |

---

## 7. Open questions for the maintainer

1. **Indicator without a bar.** When the bar is not running (headless-ish
   desktop, bar disabled), Wave 3 has no host for the shutter affordance.
   Options: a transient always-on-top window of its own, or refusing capture
   without an indicator. Current default: capture proceeds, degradation logged
   and surfaced. Worth confirming.
2. **Denylist defaults.** Shipping title patterns for password managers and
   banking is a guess about what users consider sensitive. The mechanism is
   right; the default list deserves review before it is advertised.
