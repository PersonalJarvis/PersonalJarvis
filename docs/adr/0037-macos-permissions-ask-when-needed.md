# ADR-0037 — macOS permissions are asked for when a feature needs them

**Status:** Implemented on the branch that introduces it; **not yet exercised on a
physical Mac**. The behaviour is proven against fake frameworks and Linux gates; the
macOS runners have run the .dmg packaging probe and the hot-key spike (a macOS lane
step list that also covers the permission tests was extended later and has not been
run yet). See "Verification status".
**Date:** 2026-10-02
**Reference:** AP-35 (`AGENTS.md`); `docs/macos-permissions.md` (full design, evidence,
risk register and the manual Mac checklist); BUG-225 (`docs/BUGS.md`); supersedes
the permission behaviour described in BUG-159, BUG-161, BUG-217, BUG-222, BUG-223
and BUG-224; revives frozen decision AD-13 of the cross-platform plan.

## Context

Before this change the app put a wall of its own in front of macOS. Every protected
feature (microphone capture, computer-use input and capture, global shortcuts,
window control, screen context, dictation paste) asked
`SystemPermissionPort.runtime_access_granted()` first. That call was true only for a
stable bundle identity AND an already-granted probe. When it said no, the feature
refused before touching the operating system. macOS was therefore never asked from a
feature path. The only route to a system dialog was the app's own UI: an app-wide
banner, a Settings wizard ("Set up everything") and an onboarding step.

The plan the project froze in May 2026 said the opposite. AD-13 (`docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md`)
reads "probe at first use; if missing, log an English onboarding message and fall
back; never hard-block". The shipped code inverted it on 2026-07-15, in two commits
whose messages do not mention it, and no written human decision to ask up front
exists anywhere in the repository (history reconstructed in `docs/macos-permissions.md`
section 2). The only stated reason in code was about where a prompt would appear.

Every later layer repaired a symptom of the wall instead of removing it: the banner
(a wake word that was dead because the app had never opened the microphone, so macOS
never showed its dialog), the dead-Allow-button fixes, the sticky restart flag
(BUG-159), the identity-reset note, the "Set up everything" wizard with an
automatic restart, the Automation consent file with a hidden launch of Music and
Spotify (BUG-217), and the `wanted` flag with the banner's "Not now" (BUG-224). One
wrong identity constant disabled the whole surface twice (BUG-161, BUG-222). The
wall also cost time on hot paths (per-frame and per-keystroke probes).

Some constraints in that history are real and stay (below). None of them requires a
banner, a wizard, a readiness gate or refusing to let the OS show its own dialog.

## Decision

**Ask when needed.** A feature asks the operating system at the moment the user first
uses it or switches it on, from a user gesture, through one module,
`jarvis/platform/permission_service.py`. macOS shows its own dialog. A denial degrades
that one feature honestly, with one click to the right System Settings pane. Nothing
is asked at launch, nothing nags, and Settings > Privacy is a passive page.

This is rule AP-35. Its nine principles are binding:

1. **Just in time.** Ask when a feature that needs the permission is first used or
   switched on by the user. Never at launch, never in a banner, never as a wizard.
2. **The feature may ask; it must never act on anything but a live grant.** No path
   refuses because our own preflight says "not granted" before the OS was asked, and
   no path acts on a permission the OS has not granted. `EnsureResult.granted` is true
   only for GRANTED and NOT_REQUIRED; pending, needs-settings, denied and unavailable
   never proceed; the return value of a native request is never evidence of success.
   Silent-failure traps are checked at the source (wallpaper-only screen capture,
   all-zero audio, input dropped without trust).
3. **Let the OS ask where it asks by itself; call the explicit prompt API where it does
   not.** Never ask by touching an API that fails silently. Never create an event tap
   to provoke a prompt (BUG-058 class): the ask is `CGRequestListenEventAccess` or
   `IOHIDRequestAccess`, and the tap is created only after the preflight is true.
4. **Denied is a stable state, not an error to retry.** Degrade the one feature, say
   why where the user is, offer one click to the right pane, stop. No repeated
   prompting, no polling banner. `tccutil reset` stays an explicit "Ask again".
5. **Grants are detected by silent probing owned by the backend** and applied in
   process. A restart is only a hint, never forced.
6. **Identity decides who may reset and whether we may auto-ask, not whether we may
   act.** We act on whatever grant exists. Native requests are made only from an
   installed app bundle, or after an explicit confirmation that names the grantee.
   Reset runs only from the installed app's own bundle id.
7. **Opt-in features own their permission.** A feature the user has not switched on
   never touches its permission (wake word, mute music, computer use, global shortcuts).
8. **Honest degradation, always.** Every refusal carries a stable reason and a full
   English sentence; the UI renders one card with an action.
9. **An AI agent never answers a system dialog.** Agent-facing text is prohibitive; the
   computer-use engine pauses while a macOS consent window is frontmost.

The contract is the `PermissionGate` protocol (`jarvis/core/protocols.py`): `check`
(silent, never prompts), `ensure` and `ensure_async` (interactive, only from a gesture
entry), `ensure_all` and `open_settings`. The service
(`jarvis/platform/permission_service.py`) adds `outstanding`, `add_listener`,
`report_failed_use`, `note_reset` and `attach_bus`, which consumers reach only through
the service or by capability lookup. Off macOS it returns NOT_REQUIRED
before touching the port. Two frozen events, `PermissionNeeded` and
`PermissionResolved`, ride the existing `/ws` forwarder; the Python and TypeScript
sides are pinned by parity tests (AP-4).

## Consequences

**Deleted.** The app-wide permissions banner and its dismissal store; the Settings
wizard (`setupAll`, ordering, auto-restart, polling) and the refresh-event plumbing;
the onboarding `permissions` step (a stored legacy step id maps to `voice`); the
readiness aggregate, `wanted`, `active`, `identity_reset`, the global
`restart_required` and `foreground` in the snapshot; the Automation consent file and
the hidden launch of Music and Spotify; the identity-reset marker file. Leftovers on
upgrade (`macos-tcc-reset.json`, `macos-automation-consent.json`, the stored banner
dismissal key, a stored onboarding step `permissions`) are never read, or mapped,
and never prompt. The port's old members (`FEATURE_REQUIREMENTS`, `active_features`,
`runtime_access_granted`, the legacy `snapshot` / `request`) are deleted from
`jarvis/platform/permissions.py`, and a ratchet test
(`tests/unit/platform/test_no_legacy_permission_api.py`) fails if one returns. The
source installer removes the two leftover state files once, on its next run
(`remove_leftover_state_files`, called from `ensure_macos_app_bundle`). The frozen
`.dmg` app never gets them deleted, because `ensure_desktop_integration` returns early
for frozen builds; that is harmless, since nothing reads either file.

**Stays, because the constraint is real.** A stable signed identity (BUG-060, BUG-217,
BUG-223); live uncached state reads; the own-bundle `tccutil reset` from the installed
app; the restart hint; the honest wallpaper-only and zero-audio checks; the CI probe
of the built app; acceptance of both bundle ids for the reset gate and the
outside-app logic; the Keychain and login-item rows.

**The identity rule changes meaning.** Identity used to decide whether a feature may
run. It now decides only who may ask automatically and who may reset. A process that
is not the installed app (a terminal-launched dev run) does not fire a native request
on its own; the UI offers an explicit confirmation naming the app that would receive
the grant. It still acts on any grant macOS reports.

**Restart is a hint.** Evidence about whether a running process sees a new Screen
Recording grant conflicts, so the design works either way: the backend re-probes,
tries a real capture, and offers "Quit and reopen" only after a real failed attempt.
The app never restarts itself for a permission.

**Event catalog exclusion.** `jarvis/tasks/event_catalog.py` excludes
`PermissionNeeded` and `PermissionResolved` from routine triggers. A routine or agent
reacting to a permission event could end up answering or steering a system dialog,
which principle 9 forbids; the exclusion is a one-way decision recorded here. No agent
or router tool exposes ensure, request, open-settings or reset
(`tests/unit/platform/test_permission_agent_boundary.py`).

**Boot rule.** With every permission undecided, including `wake_word_enabled=True`,
launch makes no OS dialog, no input-stream open, no event-tap creation and no capture
call; an upgrader with every grant present sees zero cards and zero requests. The
service singleton is lazy, imports no framework at module scope and does no I/O at
import (AP-26). `scripts/ci/check_boot_budget.py` runs after touching startup.

**No persisted "asked" memory.** The once-per-process and cooldown rules live in
memory. A persisted memory would resurrect the dead end of BUG-083 after a
`tccutil reset`, a re-sign or another bundle id.

**Cost.** The first use of a feature can now show an Apple dialog in the middle of a
task (a held dictation key pressed while the microphone dialog is open is not started
retroactively; the user presses again). That is the intended trade for removing a
wall that blocked the OS from asking at all.

### Verification status

This is a shared-contract change (T3). Proven so far: logic against a stateful
framework-level simulator (`tests/fakes/fake_tcc.py`) and the contract scenario table
(`tests/contract/test_permission_service_contract.py`) on macOS-shaped and non-macOS
hosts; frontend unit tests; the Linux static gates; the `.dmg` packaging and frozen-app
probe on macOS runners (BUG-222, ad-hoc signed). **Unverified, because nobody ran it
on a physical Mac:** every real dialog and its wording, the behaviour of a grant in a
running process, hold-key semantics, the Developer ID or notarized build, macOS 26 and
27, the double dialog in the embedded WebView, the Appshot both-Option permission need,
and the light and dark look of the card on a real window. Each is a row of the manual
checklist in `docs/macos-permissions.md` section 7 awaiting a sign-off, and the live
sign-off checklist (`docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md`,
rows TCC-1 to TCC-7) is rewritten to this behaviour. Windows and Linux are unchanged
(NOT_REQUIRED, empty request log).

## Alternatives considered

- **Keep the banner and the wizard.** Rejected: they are repairs for a wall that
  stopped the OS from asking, they nag users about features they never turned on, they
  need a polling loop and a restart machine, and every extra layer produced its own
  bugs (BUG-159, BUG-161, BUG-224).
- **Ask during onboarding.** Rejected: onboarding cannot know which features a person
  will use, a dialog without the feature in front of the user has no context and is
  easy to deny by reflex, and Apple's guidance is to wait until a feature needs access.
  A required permissions step also reads as mandatory (the wording problem corrected in
  commit `930f8bc9c`). The onboarding step is removed instead.
- **A persisted "already asked" memory.** Rejected: it contradicts a live state read
  after `tccutil reset`, a re-sign or another bundle id, and recreates the dead end of
  BUG-083 (no dialog, no button, a switch that does nothing).
- **Carbon `RegisterEventHotKey` hotkeys now.** Rejected for this change: it needs no
  Input Monitoring according to community reports (Apple's documentation does not say
  so), but it cannot express modifier-only, Fn, side-specific or two-key chords, its
  crash surface has not been exercised, and a flag nothing reads would break AP-31.
  Only the evidence tool ships (`macos-hotkey-spike.yml`, dispatch only; the
temporary branch push trigger of commit `59749f859` was reverted in `945fe7949`).
- **Per-turn computer-use permission cards.** Rejected for the first version: one
  floating card plus the mission's own `blocked_permission` ending (the spoken or
  written sentence; no separate deck journal line was built, and nothing resumes the
  mission after a grant: the person asks again) is enough, and a card per agent
  turn invites the model or the user to treat a system dialog as part of the task
  (principle 9).

## Follow-ups

- **Carbon backend flip rule.** The default may become Carbon-first only after the
  runner spike is green on macOS 15 arm64, 15 Intel and the current release; a
  built-app probe on both architectures passes; one physical-Mac sign-off from a
  clean reset (hold-to-dictate including modifier-first release, no dialog, Secure
  Input, quit in under 5 s) is recorded; and one opt-in release cycle ends with no
  quarantine reports. The crash-surface list is in `docs/macos-permissions.md` 4.15.
  The spike ran once (run `36954304202`, `macos-15` arm64 and `macos-15-intel`, both
  jobs green); its arm64 report is marked unusable as TCC evidence because runners
  pre-grant TCC, and variant G is not covered, so the criterion stays open. Runner
  evidence only.
- **macOS Call and Hangup defaults.** The shipped defaults (`f3+f4`, `f1+f2`) stay on
  the event tap. Platform-specific defaults are a maintainer decision and need the
  five-layer pattern with a parity test (AP-4, AP-31).
- **WKWebView media-capture delegate.** The embedded window's WebKit decision sits on
  top of the TCC dialog and has no handler. The UI asks from the gesture before
  `getUserMedia`; the delegate grant stays an open item (risk R1).
- **`report_failed_use` coverage.** `report_failed_use` produces the `restart_hint` only
  for Screen Recording and Input Monitoring (`_RESTART_HINT_FAMILIES`). Its two callers are
  the Input Monitoring tap (`jarvis/trigger/backends/quartz.py`, once per tap when no raw
  events arrive) and the Screen Recording capture path
  (`screen_access._unusable_grant_refusal`, when other apps' windows show no readable
  title while the state reads granted). Accessibility input and the ducking module have
  no such path; the ducking module maps a `-1743` after a granted read to `needs_settings`
  instead.
- **Sign-off.** Run section 7 of `docs/macos-permissions.md` on an Apple Silicon and an
  Intel Mac, on a `.dmg` and a managed install, and record the results before any row
  is called verified.
