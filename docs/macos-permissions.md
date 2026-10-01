# macOS permissions: just in time, one at a time

Status: implemented on branch `feature/permissions-jit-remmk1` (draft PR, not merged).
Scope: macOS TCC grants (Microphone, Screen Recording, Accessibility, Input
Monitoring, Input Control, Automation) and the Keychain prompt. Windows and
Linux are unaffected (see [Platform behaviour](#platform-behaviour)).

## Summary

- **Before:** a macOS-only permissions step in first-run setup, a permanent
  full-width banner above every view listing every missing grant, and a "Set up
  everything" wizard that walked all seven grants in a row, whether or not the
  user had touched the feature behind them.
- **Why that was wrong:** every grant was labelled "required" by some feature,
  so the banner could never go away and the app opened with an interrogation.
  Apple's guidance, and every app we compared, ask in context, at first use.
- **Now:** nothing is asked up front. A feature the user starts that meets a
  missing grant raises one `PermissionNeeded` event; the main window answers
  with one small card that names that access, says in one sentence what it is
  for, and offers the next honest step (system dialog, System Settings pane,
  restart). Settings keeps a quiet status list for recovery.
- **Core function needs no grant.** Text chat, agents and the API Keys page work
  with zero macOS permissions, so there is deliberately no mandatory
  onboarding screen for permissions.
- **Kept on purpose:** the backend permission service (live probes, tccutil
  recovery, restart handling, identity-change detection) was sound and is
  unchanged. The fix is when and how the UI asks, not how grants are read.

## Root cause from the history

The git history here is a squashed baseline (186 commits, root `742c4bcb`,
2026-09-30), so blame cannot explain the design. The dated entries in
`docs/BUGS.md` carry the real chronology:

| When | What | Effect on the design |
|---|---|---|
| BUG-056/057/058 (2026-07-14) | First macOS boots aborted natively: a `CGEventTap`, a PortAudio stream or a mic open without a grant killed the process (SIGABRT, uncatchable from Python). | Rule: never touch a TCC-gated API before the user was guided to grant it. This is why the voice pipeline stays parked until Microphone is granted (`jarvis/ui/desktop_app.py`, voice activation gate). **A real technical reason.** |
| 2026-07-14/15 | The app became a signed native bundle so TCC attaches to "Personal Jarvis", not to Terminal or Python. | Grants are bound to the bundle identity. **Real.** |
| BUG-083 (2026-07-18) | Grants looked "auto-denied" after an update because ad-hoc signing makes the CDHash the identity. | Scoped `tccutil reset` ("Ask again"), restart-pending state, closing System Settings before opening a pane. **Real.** |
| BUG-159 / BUG-161 (2026-08) | Grants read as missing while System Settings showed them on (stale per-process preflight, stale signature). | Live probes, `can_reset`, self-clearing restart flag. **Real, but layered fixes on fixes.** |
| 2026-09-30 | First run became one guided card inside the real app, with a permissions step pointing at the Settings rows. | The upfront step. **Organic growth**, not a constraint. |
| (unknown, before squash) | Global banner plus "Set up everything" wizard. | Built to rescue users whose grants were stranded. Useful as recovery, wrong as the default path. **Organic growth.** |

Conclusion: the *probing and recovery* machinery has real technical causes. The
*up-front bulk asking* does not; it grew because the one place that knew about
all grants was a list, and a list shows everything.

## How other apps do it

Method and limits: one round of web searches (2026-10-01), no hands-on testing.
The per-app cells come from support pages, issue trackers and community posts.
Anything marked *unverified* is inference or a single source. Slack, Loom and
CleanShot X have thin sourcing; treat those rows as low confidence. The Apple
HIG page could not be fetched as text, so the HIG statement below is from
memory and also *unverified*.

| App | Permissions | When asked | Explained how | On denial | Route to System Settings |
|---|---|---|---|---|---|
| ChatGPT macOS (computer use) | Screen Recording, Accessibility | Just in time, when the feature is first enabled (*unverified*: whether an earlier onboarding step exists) | In-app dialog before the system prompt | Tool reports permissions pending. Reported pitfall: the capturing helper is a separate bundle that needs the grant too, and nothing says so | In-app flow plus the macOS prompt |
| Claude Desktop (computer use) | Screen Recording, Accessibility | Setting-driven: user turns on the computer-use toggle, then grants | Third-party guides explain each grant; Anthropic's own wording *unverified* | Clicks silently do nothing without Accessibility; relaunch after granting | User opens the Privacy panes; whether the app deep-links is *unverified* |
| Raycast | Accessibility, plus Screen Recording / Mic for newer AI features | Just in time, on first use of a command that needs it | Per-feature explanation in its manual | Feature does not run; granting restarts Raycast and setup resumes | Prompts the user to enable it; deep-link behaviour *unverified* |
| Zoom | Screen Recording, Camera, Mic | Just in time, on first share or first camera/mic use | macOS system text plus help pages | Share fails until granted; macOS offers Quit Now / Later | System prompt has an Open System Settings button |
| Slack | Camera/Mic (huddles), Screen Recording (share), Notifications | Just in time (*low confidence*) | *unverified* | *unverified* | *unverified* |
| 1Password 8 | Accessibility (Universal Autofill), Notifications | Opt-in setting, not a launch prompt | Support and community posts | Universal Autofill stays off; browser extension still works | Manual steps in support docs |
| Loom | Camera, Mic, Screen Recording, Accessibility | Just in time, at first record | Support articles | Recording or camera fails; enable in Privacy and restart | Manual steps in help articles |
| CleanShot X | Screen Recording, Accessibility for some features | On first capture (*low confidence*) | *unverified* | Capture fails; Sequoia re-prompts reported | *unverified* |

What the comparison supports: every app that was sourced asks in context or
behind an opt-in toggle, none interrogates at launch, and most tell the user to
relaunch after a Screen Recording grant.

### Technical split (what macOS allows)

| Class | APIs | Behaviour |
|---|---|---|
| Real system dialog | `AVCaptureDevice.requestAccess` (camera, mic), Contacts, Calendar, `UNUserNotificationCenter` | Dialog with the app's usage string, shown once per identity |
| Prompt API, then a manual switch | `AXIsProcessTrustedWithOptions` (prompt option), `CGRequestScreenCaptureAccess`, `CGRequestListenEventAccess` / `IOHIDRequestAccess` | Shows a one-shot dialog with an Open System Settings button; the user still flips the switch |
| No API | Full Disk Access | Deep link only (`...?Privacy_AllFiles`). Jarvis does not need it |
| Apple Events | `AEDeterminePermissionToAutomateTarget` | Asks per target app, only while the target runs |

- After a denial macOS does not prompt again for that identity. The recovery is a
  Settings switch, or `tccutil reset <Service> <bundle-id>` (scoped to this
  app's own bundle id, offered as "Ask again").
- Screen Recording is applied to a fresh process only: the preflight is frozen
  per process, so a restart is part of the flow.
- `IOHIDCheckAccess` has three states; collapsing it to a boolean hides the
  difference between "not asked" and "denied". The service already uses the
  tri-state.
- macOS 15 (Sequoia) adds a recurring "bypass the window picker" prompt for
  older capture APIs (monthly after the betas). Using ScreenCaptureKit's content
  picker avoids it; that is an open point below, not part of this change.
  macOS 26/27 changes were not verified.
- Apple HIG, Privacy (*unverified, from memory*): ask only when needed, in
  context, with a clear purpose string, and degrade gracefully on denial.
  Reference: https://developer.apple.com/design/human-interface-guidelines/privacy

Sources: 9to5mac.com/2024/08/14/macos-sequoia-screen-recording-prompt-monthly,
mjtsai.com/blog/2024/08/08/sequoia-screen-recording-prompts-and-the-persistent-content-capture-entitlement,
github.com/openai/codex/issues/46776, code.claude.com/docs/en/desktop,
manual.raycast.com/screen-awareness, support.loom.com (Mac reset accessibility),
scu.edu Zoom screen-share guide, 1password.community (Accessibility thread).

## Target architecture

```
 user starts a feature ──► feature already fails closed on a missing grant
                                   │  (cu capture/actuate, dictation key,
                                   │   screen question, window control)
                                   ▼
        SystemPermissionPort.announce_needed(permission, feature)
          · macOS only; silent for granted / restricted / unavailable
          · one card per permission per 45 s (a retry loop cannot stack cards)
                                   │  PermissionNeeded event on the EventBus
                                   ▼                (existing /ws fan-out)
        main window: PermissionPrompt card (one at a time)
          why (per feature) ─► Continue ─► system dialog      (can_request)
                             └► Open System Settings           (no dialog left)
                             └► Restart now                    (applies on relaunch)
                             └► Ask again                      (stale grant)
          closes itself when the grant lands; "Not now" always works
```

Pieces:

- `jarvis/platform/permissions.py`: `announce_needed()`, `require()` and a
  process-wide sink; `publish_needs_to(bus, loop)` installs the bus sink
  (called once from `jarvis/ui/web/server.py`, thread-safe). Existing probes,
  `request`, `open_settings`, `reset` and restart handling are unchanged.
- `jarvis/core/events.py`: `PermissionNeeded(permission, feature)`, both stable
  tokens; the UI owns every sentence.
- Frontend: `store/permissionPrompt.ts`, `components/permissions/PermissionPrompt.tsx`
  (mounted once in `App.tsx`; the status polling in `usePermissions` runs only
  while the card is open), `PermissionNeeded` handling in `hooks/useWebSocket.ts`,
  and `useWakeWord.ts`, which asks for the microphone right when the user turns
  the wake word on.
- Settings keeps the status list (`views/settings/PermissionsPanel.tsx`) as the
  quiet place to review or repair a grant. The bulk wizard is gone.
- First-run setup no longer has a permissions step
  (`jarvis/setup/onboarding_meta.py`, `setupSteps.ts`).

### Permission, trigger, request kind, denial

| Permission | Trigger (feature) | Request kind | After a denial |
|---|---|---|---|
| Microphone | Turning the wake word on (`useWakeWord`); pressing dictation with the mic gate closed | System dialog | No re-prompt: card switches to the Settings pane, plus "Ask again" for a stale record |
| Screen Recording | Computer-Use capture; a question about the screen (`screen_context`) | Prompt API, then a Settings switch | Restart card (grant applies to a fresh process); Settings pane + "Ask again" |
| Accessibility | Computer-Use input; window switch / maximize (`window_control`) | Prompt API, then a Settings switch | Settings pane + "Ask again". Input Control rides on this grant, so only one card is raised |
| Input Control (event posting) | Computer-Use (shares the Accessibility card) | Rides on Accessibility | As Accessibility |
| Input Monitoring | Global shortcuts | Prompt API, then a Settings switch | **Not announced automatically** (see open points); Settings row |
| Automation (Music, Spotify) | Audio ducking while dictating | Apple Events dialog per player | **Not announced automatically** (see open points); Settings row |
| Keychain | Storing API keys | Keychain prompt per item | Not TCC; Settings row replays the read |
| Full Disk Access, Camera, Contacts, Calendar, Notifications | Not used by the app | none | none |

Info.plist usage strings and the Hardened Runtime entitlements
(`jarvis.spec`, `packaging/macos/entitlements.plist`) were reviewed and left as
they are: they already carry microphone, camera, Apple Events, screen capture,
speech and folder strings. The app is not sandboxed.

## Platform behaviour

`announce_needed` returns immediately off macOS, so Windows and Linux never see
the event or the card, and a headless Docker run is unchanged. The new frontend
code is platform-neutral and the card closes itself when the status snapshot
says the platform is not `darwin`.

## Assumptions

1. The instruction named a native macOS app and placeholders for the repo and
   branch. This repo's macOS app is a Python application in a WebView, so the
   work is Python plus the React frontend, not Swift.
2. Branch: the session was assigned `feature/permissions-jit-remmk1`; the brief
   suggested `feature/permissions-jit`. The assigned name was used.
3. "Core function" is read as: chat, agents and API keys. None needs a grant,
   so no onboarding permission screen is kept.
4. The permission service itself was kept; only the asking surface changed.
5. The new document is English because everything committed here is English.
6. Per-app research is secondary-source only (see the method note above).
7. `screen_context` was added to `FEATURE_REQUIREMENTS` because answering a
   screen question demonstrably needs Screen Recording and had no entry.
8. A cooldown of 45 s per permission is enough to stop retry loops from stacking
   cards while still re-asking after a plausible "Not now".

## Open points

- **Global shortcuts (Input Monitoring, Accessibility)** are not announced.
  The hotkey backend starts at boot; announcing from there would bring back a
  launch-time prompt. Decide the trigger (for example when the user opens the
  shortcut setting, or on the first foreground launch after setup).
- **Audio ducking (Automation)** has no `announce_needed` call yet; the Apple
  Events dialog still fires from the ducking script path. Needs the same
  one-line call in `jarvis/audio/ducking/macos.py`.
- **Voice call from the UI** with a closed microphone gate: only the dictation
  key and the wake-word toggle announce today.
- **Screen Recording on macOS 15+:** the recurring monthly prompt for older
  capture APIs is unaddressed; evaluating ScreenCaptureKit's content picker is
  a separate change.
- **Helper binaries:** one reported pitfall is a helper process that needs the
  grant separately from the app. Not checked here for any Jarvis helper.
- **Ad-hoc signing:** a rebuild still orphans grants until builds are
  Developer-ID signed and notarized (tracked in `docs/BUGS.md`).
- **Copy review:** the German and Spanish card text was written without a native
  review.
- The unrelated unhandled `window is not defined` error printed after the
  frontend suite (`AgenticTerminal.test.tsx` timer) was not investigated.

## Verification

Run in a Linux container (no macOS, no `tccutil`):

- Frontend: `tsc -b` clean, full `vitest run` green (595 files, 5579 tests).
- Backend: see the PR description for the exact pytest scope and results.

Not run, because it needs a Mac: a production build of the app bundle, and
every item in the checklist below.

## Manual test checklist (macOS, installed bundle)

Reset first, one service at a time, with the app quit:
`tccutil reset Microphone <bundle-id>` (likewise `ScreenCapture`,
`Accessibility`, `PostEvent`, `ListenEvent`, `AppleEvents`). The bundle id is
`EXPECTED_BUNDLE_ID` in `jarvis/core/branding.py`.

1. Fresh install, fresh data directory: first-run setup shows welcome, keys,
   subscriptions, voice, ready. No permissions step, no banner, no dialog.
2. Open Settings > Privacy permissions: rows show the real state, no "Set up
   everything" button, nothing prompts by itself.
3. Wake word: turn it on with Microphone reset. The card appears with the voice
   sentence; Continue shows the macOS dialog; Allow closes the card and shows
   the success toast. Repeat with Don't Allow: the card now offers System
   Settings (no second dialog); flip the switch in Settings and confirm the card
   closes.
4. Dictation key with Microphone denied: one card, not a stack, on repeated
   presses within 45 s.
5. Computer-Use with Screen Recording reset: card appears at the first capture;
   after granting, the card offers Restart; the run works after relaunch.
6. Computer-Use with Accessibility reset: one card (not two); after granting,
   clicks and typing work.
7. A screen question with Screen Recording denied: card with the screen
   sentence; the answer degrades as before.
8. Window switch / maximize with Accessibility denied: card with the window
   sentence.
9. "Not now" closes the card; the feature fails with its existing message.
10. Stale grant (rebuild the bundle, grant still ticked in Settings): "Ask again"
    appears and clears it, then the dialog fires once more.
11. Grant already present: triggering the feature shows no card at all.
12. Windows and Linux: no card, no event, setup has the same five steps.
13. Light and dark appearance: card is readable in both (not inspected here).
14. Languages: card text in English, German and Spanish.
