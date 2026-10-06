# ADR-0038 — The live reasoning model operates the computer itself

**Status:** Accepted · **Date:** 2026-10-03 · **Supersedes:** the computer-use
part of [ADR-0035](0035-realtime-native-tools-except-computer-use.md) for the
continuous voice sessions of [ADR-0036](0036-continuous-voice-and-agent-selection.md)

## Context

A continuous voice session has two models: the voice model and a thinking
model that runs Jarvis tools (GPT-Live delegation with an API key, the ChatGPT
subscription's client-side delegation, or the native Gemini/local live model
itself). Screen work still went through the `computer_use` tool, which started
a Computer-Use mission. That mission ran its own loop and made its own vision
calls through the operation's model pin.

Live failure on 2026-10-03, ChatGPT subscription, `gpt-6.1-sol`: the user
asked to open Chrome and the newest post of an account. The mission's first
vision call sent `reasoning.effort = "none"`; the model only offers
`low`..`ultra` and answered HTTP 400. Every other provider was refused by the
billing pin ("cannot switch its selected model or billing account"), so the
mission ended after one screenshot and Jarvis said it had no model that can
see the screen. The thinking model that could see and reason was one level
above, waiting for a text summary.

The maintainer's mandate: the live session's own reasoning model controls the
computer; no second model is called for it.

## Decision

1. **One tool, owned by the session's model.** Live sessions get `computer`
   (`jarvis/plugins/tool/computer.py`, logic in `jarvis/cu/direct.py`). One call
   carries up to eight steps — `screenshot`, `click`, `double_click`,
   `right_click`, `middle_click`, `move`, `drag`, `scroll`, `type`, `key`,
   `wait` — and always answers with a fresh screenshot, its `frame_id`, whether
   the screen changed, and the front window title. The model decides every next
   step from what it sees; there is no inner loop and no second model.
2. **Jarvis owns geometry and staleness.** Coordinates are pixels of the latest
   screenshot and resolve through that frame's `CoordinateMapper` (DPI, Retina
   points, negative monitor origins). Only the first step of a call may carry
   coordinates, and they must name that screenshot's `frame_id`. Any input
   needs a screenshot first. A stale or missing `frame_id`, or a front window
   that differs from the one the frame showed (checked before the first input
   step of every call, keyboard included), presses nothing and returns the new
   screen. A failed capture after input invalidates the old frame. One call
   stays inside the 5 s voice tool budget (at most 2.5 s of waits, 300 typed
   characters).
   Pointer input keeps the landing read-back of `verified_click`/`verified_drag`.
3. **Permissions are checked per OS before anything happens** (`readiness`):
   - macOS: Screen Recording for any look; Accessibility and Input Control for
     input; Secure Input blocks typing. The answer names the missing grant and
     where to allow it.
   - Windows: the secure desktop (UAC prompt, lock screen) blocks everything;
     an elevated front window blocks input from a non-elevated Jarvis (UIPI).
   - Linux: Wayland and hosts without a display are refused with the reason.
   - `[computer_use].enabled = false` turns the tool off and removes it from the
     catalog.
   A blocker returns `blocked: <code>` and a plain sentence; the instructions
   tell the model to explain it and not retry in a loop.
4. **Control signals stay.** The first input step publishes `CUControlStarted`
   (control border and agent pointer, Escape armed) and registers a cancel token, so Escape, the
   voice hang-up and the emergency stop stop it. `CUControlEnded` follows 12 s
   after the last input. Input is serialized with the mission harness desktop
   lock; while a mission runs the tool answers `blocked: busy`.
5. **Catalog.** The voice catalog no longer offers `computer_use`,
   `dispatch_to_harness` or the screen-unit primitives (`click`, `move_mouse`,
   `drag`, `scroll`, `type_text`, `hotkey`, `screenshot`). `computer` is
   declared directly under its own name in every live mode (deferred catalog
   included). Text chat, scheduled tasks and the CLI keep the mission harness.
6. **Budget.** A subscription delegation that uses `computer` may run
   `max(40, [computer_use].max_steps)` extra rounds and up to
   `[computer_use].mission_timeout_s`. Only the three newest screenshots stay
   in the request; older ones become a one-line note.
7. **Effort mapping.** A "no thinking" request to a subscription model maps to
   its lightest advertised level instead of sending an unsupported value.

## Consequences

- One model sees, reasons and acts; the result it reports is the screen it
  last looked at. Measured 2026-10-03 on Windows 11 with `gpt-6.1-sol`
  (medium): "open a new Chrome tab with the newest post of @elonmusk" finished
  in 145 s with 14 calls, including two refused stale-window clicks and a
  foreign session switching the active Chrome tab mid-task.
- Each step costs one reasoning round. Keyboard routes (address bar, app
  launcher) and step batches keep that count low; this is slower than a fast
  vision model per step but avoids a second credential and billing account.
- macOS and Linux X11 run the same code paths through the existing actuators;
  they are covered by unit tests with platform probes, not yet by a live run.

## Alternatives considered

- **Keep the mission, fix the effort value.** Fixes the 400, but still runs a
  second loop the live model cannot see or steer, which is what the mandate
  rejects. The effort fix is kept for every other caller.
- **Provider-hosted computer tools.** Not available on the subscription's
  Codex endpoint, and not provider-neutral (AP-21).
- **Expose the old primitives with screen units.** The model never sees screen
  units; every click would depend on its guess of the scale factor.
