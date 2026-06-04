# ADR-0016 — Visible-Feedback Contract

**Status:** Accepted · **Date:** 2026-05-18 · **Phase:** UX-Reliability (post-BUG-027)

## Context

BUG-027 (2026-05-18): the orb-drag-and-pin feature persisted a drag
position to `jarvis.toml [overlay.mascot]`. After a restart, the orb
spawned at the persisted position — even when that position lived on
a secondary monitor outside the user's attention. Every part of the
runtime worked: wake-word detected, `OrbBridge._on_state: IDLE →
LISTENING` fired, `orb.show()` deiconified the Tk window. From the
user's perspective the orb was simply *gone*.

The bug is one instance of a recurring class. `docs/BUGS.md` records
eight entries with the same shape — the runtime emits the expected
lifecycle event (`AudioOutFirst`, `orb.show called`, `state =
LISTENING`) but the actual user-visible outcome (audible sample,
mascot pixel, toast on screen) is missing or off-screen:

| Bug | Date | Surface | Failure mode |
|---|---|---|---|
| BUG-003 | 2026-04-25 | TTS | silent SAPI5 fallback masked Gemini failure |
| BUG-007 | 2026-05-03 | TTS | dead-code `return` statements after `_speak` |
| BUG-010 | 2026-05-03 | TTS | Gemini-TTS HTTP 200 with empty audio body |
| BUG-014 | 2026-05-10 | TTS | WDM-KS host-API blocking-write trap |
| BUG-016 | 2026-05-10 | TTS | Kontrollierer pickup missing after spawn |
| BUG-020 | 2026-05-16 | TTS | four orthogonal silent-return paths |
| BUG-024 | 2026-05-16 | TTS | wake detected during warm-up, mic still closed |
| BUG-027 | 2026-05-18 | Orb | drag-pin honoured onto invisible secondary screen |

The codebase has rich infrastructure for the *causation* layer
(EventBus, trace_id correlation, flight recorder), but **zero
infrastructure that compares expected user feedback to actual user
feedback**. Every fix in the table above was an ad-hoc patch on the
specific failure mode. The next surface to silently regress (toasts,
tray balloons, pywebview window) will repeat the cycle.

The complementary anti-drift pattern documented in
[`docs/anti-drift-three-layer.md`](../anti-drift-three-layer.md)
solves a related class — *string-enum drift between Python, SQL,
Pydantic, TypeScript, and UI labels* — by treating the vocabulary as
a versioned contract with a tuple-of-strings source of truth and a
parity test. That pattern works because the *shape* of the regression
is observable at boot: `set(literal) == set(constants)` is true or
false. For visible-feedback bugs the regression is not observable at
boot — it surfaces at runtime, in production, on the user's machine.

## Decision

Every UI surface that the runtime intends the user to receive
publishes a `UserVisibleFeedback` event after the attempted
side-effect, with an `observed` payload that the runtime can compare
to an `expected` payload. The orb is the first adopter.

```python
@dataclass(frozen=True, slots=True)
class UserVisibleFeedback(Event):
    surface: str                  # "orb" | "tts" | "toast" | "tray"
    expected: dict[str, Any]      # runtime intent
    observed: dict[str, Any]      # post-effect measurement
    correlation_id: str           # ties to the triggering event
```

**Contract terms:**

1. **Publish-after-effect.** The event MUST be published from the
   actual side-effect site (`orb._publish_visibility_feedback`,
   `tts.player.on_audio_out_first`, `pywebview.window.shown`),
   NOT from the call site that scheduled the effect. This makes
   `expected` vs `observed` a true comparison of intent and outcome.
2. **Correlation.** `correlation_id` links back to the triggering
   event (the `SystemStateChanged` trace_id for orb show events; the
   `TextToSpeak` trace_id for TTS, etc.). The flight-recorder can
   reconstruct `intent → outcome` pairs in batch.
3. **No side-effects in publishers.** The publish call is
   fire-and-forget. A bad bus or a stale Tk root must not propagate.
4. **Surface-specific schemas, surface-agnostic event.** The dict
   shapes inside `expected` / `observed` are owned by each surface.
   Consumers do exact-match dispatch on `surface`.

**Three orb-side adopter components** (BUG-027 implementation):

- **L1 — Selective boot flash.** When the persisted pin is honoured
  on a non-primary monitor (Power-User mode), the orb deiconifies at
  the primary anchor for 800 ms before migrating to the pin. The user
  always *sees* the orb on boot regardless of pin location. Skipped
  in single-monitor / primary-pin / default-anchor boots — no visual
  noise in the 99% case.
- **L2 — Discovery-independent recovery.** Voice phrases "Orb
  zurück", "wo bist du", "reset orb" are matched by
  `jarvis.brain.local_action_gate` and dispatched to the new
  `reset_orb_position` tool, which publishes `OrbResetRequested`.
  The orb bridge subscribes and resets via the Tk-thread marshal.
  The previous recovery path (right-click → "Reset position") only
  worked when the orb was already visible — a henne/ei-loop.
- **L3 — Post-condition assertion.** `resolve_placement` validates
  its own contract at the end of each branch via the
  `_assert_visibility_contract` helper. When `require_primary=True`
  and screens is non-empty, the returned monitor MUST be primary —
  any other outcome is a logic regression inside the function and is
  caught at the source instead of producing an invisible orb at
  runtime.

**Diagnostic + test layers:**

- **L4 — Visual regression test.** New
  `tests/unit/ui/test_orb_visibility_contract.py` (28 cases): 20
  parametric placement contract checks (topology × pin × flag), a
  real-Tk visibility gate on Win32, and a drag-threshold boundary
  guard around `DRAG_THRESHOLD_PX = 16`. The Tier-2 real-Tk test is
  the deepest end-to-end visibility contract assertion we can make
  without a graphics-comparison harness.
- **L5 — `python -m jarvis --orb-doctor`.** Dry-run diagnostic that
  reads the persisted pin, enumerates live monitors via
  `EnumDisplayMonitors`, and computes where the orb *would* spawn —
  all without opening a Tk window. Sits alongside `--check`,
  `--phase5-doctor`, and `--plugins` in the existing CLI family.

## Consequences

**Positive:**

- The `UserVisibleFeedback` event is a clean architectural seam. TTS
  adoption is one new publish call in `jarvis/audio/player.py` after
  `AudioOutFirst`. Toast adoption is one publish call in
  `pywebview` window.created. No redesign needed for either.
- A flight-recorder consumer can batch-compute drift events: for
  every `correlation_id`, find the triggering event and the
  `UserVisibleFeedback`; pair them; emit a `UserVisibleFeedbackDrift`
  when they disagree. Operates without runtime intrusion.
- The boot-flash (L1) is opt-in via the existing
  `allow_secondary_monitor_pin` config. The user who explicitly
  enabled secondary-monitor pinning is the only one who needs the
  visual safety net — single-monitor users see no change.
- The recovery path (L2) is discoverable: any user who recovers from
  a lost orb once will remember the phrase. The phrase corpus is
  intentionally small and anchored at `^`, so false-positive risk on
  general queries ("wo bist du gerade?", "weißt du wo der Bus ist?")
  stays low. The false-positive corpus in
  `tests/unit/brain/test_local_action_gate.py` documents 10
  intentional non-matches as a regression guard.

**Negative / trade-offs:**

- `_publish_visibility_feedback` adds ~1 ms of work on the Tk thread
  after every state-show transition. Measured impact: zero — the
  call is scheduled via `_root.after(50, …)` so it lands after the
  deiconify frame, not during it.
- The boot-flash creates a brief visual transition for
  `allow_secondary_monitor_pin=true` users on every restart. This is
  the intended UX (the user explicitly opted into a non-default
  policy), but it is a behaviour change documented here as a
  consequence.
- ADR-0016 adopters need three call sites each (publish at
  side-effect site, subscribe in a consumer if needed, test). That
  is a small adopter cost but a real one.

## Alternatives considered

**Alt-1 — "Persist `(x_relative, y_relative)` only, drop `monitor`."**
Single-shot fix. Eliminates the bug subclass entirely because every
boot resolves onto the primary monitor at the stored relative
offset. **Rejected** because it kills the deliberate Power-User UX
of "orb pinned to specific secondary monitor". Memory entry
`feedback_max_autonomy.md` explicitly values pinning over auto-reset.

**Alt-2 — "Always boot with a primary-anchor flash."**
Simpler than L1's selective gate (no condition, just always
deiconify-then-withdraw). **Rejected** because it adds 800 ms of
visual noise on every cold boot of every user, single-monitor
included, to defend the 1% multi-monitor secondary-pin case.

**Alt-3 — "Ad-hoc per-surface fixes, like every previous silent
regression."** **Rejected** because the BUGS.md table proves the
pattern does not scale. Every silent regression got an ad-hoc patch;
seven more arrived afterwards. The contract approach makes the next
silent regression observable at the seam, not at the user.

**Alt-4 — "Generalise the anti-drift-three-layer pattern to
geometry."** Tempting, but the anti-drift pattern only works because
the regression is observable at *boot* (set comparison). Geometry
correctness depends on runtime monitor topology, which is observable
only when a window exists. ADR-0016 is the geometry/visibility
sibling of anti-drift-three-layer, not an extension of it.

## Cross-references

- BUG entry: [BUG-027 in `docs/BUGS.md`](../BUGS.md#bug-027-orb-invisible-after-accidental-drag-onto-secondary-monitor-high-2026-05-18)
- Sibling pattern: [`docs/anti-drift-three-layer.md`](../anti-drift-three-layer.md)
- Implementation:
  - `ui/orb/drag_persistence.py` (L3 post-condition)
  - `ui/orb/overlay.py` (L1 boot flash, L0 publisher injection)
  - `ui/orb/bus_bridge.py` (L0 UserVisibleFeedback publish, L2 OrbResetRequested subscribe)
  - `jarvis/brain/local_action_gate.py` (L2 voice patterns)
  - `jarvis/plugins/tool/reset_orb_position.py` (L2 tool)
  - `jarvis/core/events.py` (UserVisibleFeedback + OrbResetRequested dataclasses)
- Tests:
  - `tests/unit/ui/test_orb_visibility_contract.py`
  - `tests/unit/brain/test_local_action_gate.py` (orb-reset cases)
  - `tests/unit/ui/test_orb_bus_bridge.py` (UserVisibleFeedback + OrbResetRequested cases)
- Diagnostic: `python -m jarvis --orb-doctor`
