# Screen Context — architecture, flow, privacy plan, MVP roadmap

**Date:** 2026-07-31
**Status:** Production implementation complete across the brain, desktop, REST,
settings, indicator, receipt, privacy, and optional OCR paths
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
| `jarvis/platform/window_capture.py` | Native per-window capture (Windows Graphics Capture, macOS SCK). |
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
| `UiTextReader` | What text is visible? | UIA | AXUIElement | AT-SPI | unavailable, flagged |

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
  `degradations`.
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
    │               arms a short confirmation window, and STOPS. No capture,
    │               and every other screen path stays shut for this turn.
    └── SCREEN / WINDOW ↓
    │
    ▼
[2] permission check  (screen recording; accessibility for UI text)
    ├── denied → "technical" refusal: logged, turn CONTINUES on the old path
    │            (see Wave 2 — a missing permission is not a prohibition)
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
[5] capture ONCE  +  read window facts  +  read UI text (accessibility first; optional OCR adds pixel boxes)
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
proceeds only for an explicit request and carries a named degradation in its
receipt. Headless and Wayland hosts cannot reach the shutter in the first
place. This preserves the user's requested action without pretending that the
visual signal succeeded.

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

**A deictic only counts as a pronoun, never as a determiner.**
"Was ist das?" points at something; <!-- i18n-allow: quoted German matcher phrase -->
"Was ist das Beliebteste?" is *about* the <!-- i18n-allow: quoted German matcher phrase -->
word after "das", and a screenshot answers nothing about it. Every deictic
branch therefore requires the pronoun to end its clause (spoken filler
particles allowed), and the bare look-verb openers ("schau mal, …",
"have a look, …", "kannst du mal schauen, ob …") count only when nothing
follows them — with content they open a statement or a lookup request. This
rule exists because a mid-conversation topical follow-up
("Was ist das Beliebteste?", boxing <!-- i18n-allow: quoted German matcher phrase -->
conversation, voice session 2026-08-06 18:33) derailed the topic twice with
the German clarifying question. When extending the vocabulary, never match a
deictic followed by an open noun slot.

Negative guards matter as much as the vocabulary. Product how-to questions
such as "How can you look at my screen?" are explicitly not capture consent.
The existing `cu_gate` learned
this the hard way: product names containing a vehicle token ("Open AI", "context
window", "edge case") read as commands. Screen Context reuses that masking
approach before matching, and adds its own: "see" in "I see" / "you see" /
"let's see", "look" in "look into it" / "look for", "schauen wir mal", "a ver".

### 3.1a Looking is not operating — the Computer-Use boundary (BINDING)

Maintainer mandate 2026-08-02, after Computer-Use answered "what is on my
screen?" by taking over the desktop. **A question about the screen is answered
with one screenshot. Computer-Use runs only when an action is asked for.** The
two paths are enforced apart in three places, and all three are load-bearing:

1. **`intent.requests_screen_operation`** — a turn that asks for an on-screen
   action ("click the button on my screen") is *not* a look request, so Screen
   Context stands down and leaves the tools the user asked for in place.
2. **`jarvis/brain/cu_gate.py`** — the mirror image. Its vocabulary is split
   into ACTION verbs and SURFACE nouns, because a noun names *where*, not *what
   to do*. A turn this module reads as a look request cannot start a desktop
   mission even when it names a surface, and even inside the recent-run
   follow-up window. Before the split, the bare noun "Bildschirm" was itself
   sufficient authority — which is how a question moved the user's mouse.
3. **`BrainManager.generate`** — a successful capture strips every tool from
   the turn, so the two paths can never both run for one utterance.

Two consequences worth stating, because both were regressions waiting to
happen. A screenshot request ("mach mal einen Screenshot") is the *least*
ambiguous look request there is: it must classify as `SCREEN`, and the German
pattern that missed the most common spoken phrasing of it is exactly why the
turn used to reach Computer-Use. And naming the harness ("mit Computer-Use ...")
is the strongest desktop signal there is: it stands Screen Context down and
passes the gate unconditionally.

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

OCR is optional and runs only when the user enables it and a backend is
installed. When enabled, it always retains line geometry so matching sensitive
text can be burned out of the pixels. Its extracted text is appended only when
the accessibility result is sparse relative to the captured area, because the
OS text is otherwise both faster and more accurate.

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

Screen pixels, OCR, and accessibility text are untrusted evidence. They are
wrapped in fixed `SCREEN_EVIDENCE` delimiters with an instruction boundary, and
the production brain exposes no tools on a captured look turn. Text rendered by
a webpage or document therefore cannot turn a read-only inspection into an
action or tool call.

Default patterns ship for card numbers, IBANs, API-key shapes, and
`Authorization:`-style headers. They are configurable, additive, and each entry
carries a label that appears in the redaction report.

### 4.3 Retention

- Image bytes live in memory inside a `_Handle`, keyed by an opaque id.
- One consumption. `consume()` removes the handle; a second call gets nothing.
- TTL (default 120 s) owns an active monotonic expiry callback; deletion does
  not depend on a later API call. Conversation turns consume immediately.
- There is no save endpoint. Screen Context has no code path that writes image
  bytes or extracted text to disk.
- Nothing about a capture reaches the flight recorder except metadata: target
  kind, monitor identity, sizes, redaction counts, degradations. Never pixels,
  scrubbed text, app name, or window/document title.

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

### Wave 2 — voice-session wiring ✅ *implemented*

`turn.py` plus the production `BrainManager.generate` / `generate_stream`
wiring. `RouterBrain.handle` delegates to the same policy for compatibility.
Screen Context is consulted **before** desktop-action and permanent-vision
paths and takes precedence when it answers. Confirmation state is short-lived
and isolated by conversation, so a reply in another web thread or on another
surface cannot authorize a capture. The model-facing description rides in the
dispatcher's existing `turn_context` channel.

The shared `turn_planner` also assigns explicit and ambiguous visual turns to
the orchestrator. Realtime therefore uses the same deterministic capture path
in both delegate and direct-tool modes; the native live model never answers a
screen question without receiving the one-shot image.

Two decisions were made during implementation and are worth stating here,
because both were wrong in the first draft:

**It is additive, not a replacement.** The original plan was to swap out
`vision_gate.should_attach_screenshot()`. That is wrong: the vision gate also
fires on on-screen *action* turns ("click the button", "close this window"),
which are not look-requests but still need an image. Replacing it would blind
Computer-Use. The two gates answer different questions and both stay.

**A refusal is typed, but every requested look fails closed.** Policy and
diagnostic categories stay distinct so the UI can give useful remediation.
Neither category may fall through to permanent vision or Computer-Use: doing so
would bypass the one-shot consent and privacy boundary.

| Kind | Cause | Ends the turn? | Fallback path |
|---|---|---|---|
| `policy` | denylist match, feature switched off | yes, spoken | **shut** — falling through would photograph the very window the rule protects |
| `technical-unavailable` | no display, no permission | yes, spoken | **shut** — the host reports the limitation without starting another screen path |
| `technical-failure` | unexpected classifier/capture failure | yes, spoken | **shut** — a second, unindicated screenshot attempt would violate the privacy contract |

Collapsing their diagnostics would hide the remediation, while opening either
fallback would risk a second, unindicated capture.

An `AMBIGUOUS` turn also shuts the fallback — attaching an image while asking
whether to look at one is exactly what the three-valued verdict exists to
prevent.

**Verified by:** `tests/unit/brain/test_router_screen_context.py` (precedence,
both refusal kinds, the ambiguous path) and `tests/unit/screen_context/test_turn.py`.
Two pre-existing test files pin Screen Context to `none` via an autouse fixture,
because they cover the permanent-vision path and would otherwise pass or fail
depending on whether the host running them has a screen.

### Wave 3 — the indicator and the receipt ✅ *implemented*

A capture indicator is acknowledged as visible before the grab, blanked during
the grab so it is not photographed, and dismissed in a `finally` path. The
receipt reports dimensions and redaction count without leaking a window title.
The shared sidecar works on Windows, macOS and Linux/X11; its Windows capture
exclusion has the same blank/unblank correctness backstop as the other OSes.

**Done when:** every capture is preceded by a visible indicator on all three
OSes, or by a logged degradation naming why it could not be shown.

### Wave 4 — settings surface ✅ *implemented, simplified 2026-08-02*

**One switch, and nothing else** (maintainer mandate 2026-08-02). The Settings
card shows the master switch plus one honest readiness line — "Ready on N
monitor(s)", or the first real blocker while the feature is on. That is the
whole card.

Everything it used to expose — denylist editor, extra redaction patterns, the
default-rule and OCR toggles, the retention field, the readiness grid, and the
Save / Test / Discard buttons — is gone from the UI, not from the product. The
privacy rules still run at their shipped defaults on every capture (default
patterns on, monitor-wide denylist check, 120 s TTL, nothing on disk), and every
key remains readable and writable through the REST surface and therefore through
`jarvis api screen-context get-settings|put-settings`. A settings card that asks
a non-technical user to write regular expressions before a feature works is a
card they switch off instead of using; the CLI-first contract (CLAUDE.md §5) is
what makes hiding it safe rather than lossy.

### Wave 5 — OCR supplement ✅ *implemented*

Optional local OCR behind a capability probe. It contributes image-local boxes
for pixel redaction on every enabled run and supplements model text only when
accessibility coverage is sparse. Off by default; the base install stays
torch-free.

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

## 7. Operating limits

1. **Wayland.** Global screen capture and foreground-window identity require a
   compositor portal with interactive user mediation. This implementation
   refuses honestly and points users to X11/XWayland; it never returns a blank
   frame as success.
2. **Headless.** Status and settings remain available through REST/CLI, while a
   capture returns a named no-display refusal.
3. **Optional accessibility/OCR.** A missing UI-text backend does not block the
   image. OCR is opt-in, capability-probed, and absent from the base install.
