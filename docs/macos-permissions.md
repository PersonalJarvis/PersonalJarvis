# macOS permissions: ask when a feature needs them

Status: implemented, **not yet exercised on a physical Mac** (section 7 is the checklist that
closes that gap). Last reviewed: 2026-10-02, reconciled with the finished code on that date
(AP-35 is in `AGENTS.md`, ADR-0038 and BUG-225; the legacy permission wall is deleted; the
custom permission UI was then reduced to one toast, 4.10 and the ADR-0038 amendment). Scope:
Personal Jarvis on macOS (the downloadable `.dmg` app and the managed source-install app).

## 1. Summary

1. **Wrong:** features refused to run until our own permission check passed, so macOS was never asked from a feature path.
2. **Why:** the plan said degrade, never hard-block (AD-13); the code inverted it on 2026-07-15 with no recorded decision (2).
3. **Real, kept:** stable signed identity, live state reads, usage strings, entitlements, silent-failure checks (wallpaper, all-zero audio).
4. **Now:** a feature asks at first use, macOS shows its own dialog, and the app draws nothing around it. After a denial macOS stays silent, so the app adds ONE short toast with ONE action (4.10); the way back to a clean slate is `jarvis permissions reset` (4.16).
5. **Proof:** fake-framework tests and Linux gates (counts in 7.4) plus one earlier macOS-runner packaging run; the macOS lane steps have no recorded result for this branch; the hotkey spike ran once on two macOS runners but its arm64 report is unusable as TCC evidence (4.15); nothing ran on a physical Mac (7.4).

## Conventions: evidence labels

Every statement about macOS behaviour carries a label. A statement without Apple documentation or a
macOS runner result is never presented as fact. Rule name in code comments and commits: AP-35 (4.2).

| Label | Meaning |
|---|---|
| **[A]** | Apple primary source: developer documentation, HIG, WWDC transcript, SDK header. |
| **[D]** | Post by an Apple Developer Technical Support engineer on the developer forums. Authoritative, not formal documentation. |
| **[C]** | Community report: forum user, GitHub issue or pull request, blog, search-result summary. A hypothesis. |
| **[I]** | Inference. Must be verified on a Mac before it is treated as fact. |
| **[code]** | Read in this repository (file path given). |
| **[commit]** | Read in git history (hash and date given). |
| **[runner]** | Result of a macOS CI runner run. Runners are not user Macs: they pre-grant TCC to their tools and show no dialogs. |
| **unverified** | Not proven by any of the above for macOS. The code comments say the same. |

## 2. Root-cause analysis from git history

Method: `git log -p`, `git show`, `git blame`, the bug register (`docs/BUGS.md`), the
cross-platform plan set (`docs/plans/cross-platform-mac-linux/`), ADRs and `docs/os-parity.md`.
Dates are commit dates. Author names are left out on purpose: git alone cannot say which
sentence is human intent, so rationales are quoted from the artefact and not attributed.

### 2.1 What the plan said

| Source | Text |
|---|---|
| `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md`, AD-13 | "Permission-UX is detect-and-degrade. macOS AX/Input-Monitoring ...: probe at first use; if missing, log an English onboarding message + fall back, never silently empty ... and never hard-block." |
| `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md`, HN-5 | "probe -> if missing, log an onboarding message AND fall back - never silently empty, never hard-block." |
| `docs/plans/cross-platform-mac-linux/DEEP-DIVE-AUDIT-2026-06-19.md`, H1 and M7 | H1: without the Screen Recording grant the capture library returns the bare wallpaper with no error; the remedy asked for is a probe plus an honest message. M7: a microphone denial degrades to "no mic" with no hint; the remedy asked for is a hint. |

Both audit findings describe real technical problems. Both ask for a probe and a message, not
for a gate that stops macOS from asking.

### 2.2 Timeline of the decisive commits

| Date | Commit | What happened | Reading |
|---|---|---|---|
| 2026-07-14 | `0eebcc051` | BUG-060: first real `.app` bundle. Stated reason: a bundle gives TCC an identity, so the microphone dialog names the app instead of "whichever terminal started the process". | Technically required (stable code identity). |
| 2026-07-14 | `86f99bf4a` | BUG-058: first fail-closed gate, a preflight before the keyboard event tap. The register entry itself says no log confirmed the crash theory. | Hypothesis. BUG-077 (2026-07-17, `3b924a249`) later found the real crash was a keyboard-layout API called off the main thread, and the preflight "gated the tap correctly" but was not what prevented a crash. |
| 2026-07-15 | `29f488e60` | Computer-use input and capture probe the permission before every action. | Start of the per-action wall. |
| 2026-07-15 | `562232023` | One commit creates the whole up-front stack: `SystemPermissionPort` with `runtime_access_granted()` = stable identity AND live grant, a feature-readiness table, request routes, Settings panel, an onboarding step whose Continue was disabled until all five permissions were granted, a 2.5 s polling hook, the microphone gate (re-checked every 0.25 s in the read loop) and the voice-activation gate. Empty commit body, no co-author trailer. | **The inversion.** In-code reason (`jarvis/ui/desktop_app.py`, lines 2381-2382 at that commit, since removed): "macOS must never discover microphone permission by opening the device: that would throw an unmanaged TCC prompt before the guided onboarding step." That is a UX preference about where the prompt appears. |
| 2026-07-15 | `f0d4f77ee` | Five minutes later, a commit titled as an architecture, bug-register, CLI and release-notes refresh, with an empty body, rewrites the live sign-off checklist (`LIVE-SIGNOFF-CHECKLIST.md`): row AX-2 changes from "falls back to the pixel path and still clicks (AD-13)" to "refuses to inject input. No pixel-click bypass"; new rows TCC-2 ("no dialog appears merely from installing, booting, or opening the page") and TCC-4 ("fails closed before touching the protected API"). | Where *degrade* became *refuse*. The message does not mention it. AD-13 and HN-5 stay in the frozen file unchanged. |
| 2026-07-17 | `4a53eb424` | App-wide amber banner. Commit body: "A missing TCC grant (microphone, screen recording, accessibility, ...) silently kills wake word, voice, and Computer-Use; until now only the Settings panel and onboarding knew." | [I] (our inference, not stated in the commit): the silent death was produced by the gate (it never opened the microphone, so macOS never showed its dialog). The banner treats the symptom. |
| 2026-07-17 | `9728bf57a`, `e2e5651dc` | Dead Allow buttons for Input Monitoring: a tri-state `IOHIDCheckAccess` probe added. Ducking `prewarm()` asks for Automation when the feature is switched on. | The first, narrow just-in-time behaviour in the history. |
| 2026-07-18 | `5ea2fcc83` | BUG-083, first live finding (an Intel Mac, macOS 15.7): ad-hoc signature pins grants to a per-build hash; System Settings ignores deep-link anchors while already running (the app now quits it first); frozen Screen Recording preflight. | Real constraints, found three days after the wall shipped. |
| 2026-07-18 | `b25e63520` | "Ask again" (`POST /{id}/reset`, `tccutil reset` for the app's own id); onboarding permissions step moved last. | Recovery action, legitimate. |
| 2026-07-20 .. 08-03 | `b0f9ff2e2`, `b66cb179a` | Per-action probes measured as a cost. `b66cb179a` says its per-keystroke probe inside the event-tap callback made the shortcuts "go intermittently dead"; its mechanism (a callback that overruns its deadline gets the tap disabled by macOS) is the commit author's hypothesis, unverified; `jarvis/trigger/backends/quartz.py` carries a comment in the same spirit [code]. | Bugs caused by the gate. |
| 2026-08-20 / 08-21 | `7d75c6fbc`, `d377bffe8`, `96ebffcc0` | BUG-159 and BUG-161: "Every start warned that permissions were not enabled" while they were on; the app rejected `/Applications` as a tampering signal although "TCC pins a grant to the bundle id and its signature, never to a path". | The gate itself is the bug. |
| 2026-09-16 | `b7d92746e` | Per-user self-signed certificate: the identity is bundle id plus certificate, not a per-build hash. Reported verified on macOS 15.7.4 in the BUG-217 register entry (a live Mac session; not re-verified for this branch) [commit `b7d92746e`, register]. | The structural fix for BUG-083/159/161. Technically required. |
| 2026-09-16 | `5e3d07db3`, `4af1cc766` | Automation row with a consent file and hidden launch of Music and Spotify; "Set up everything" wizard with auto-restart ("Six buttons and a restart to find, per permission, is how 'the permissions are extremely annoying' reads from the user's side"). | The wizard automates the up-front flow instead of removing the need for it. |
| 2026-09-30 | `4e2570d76`, `9af34286a` | Onboarding card rebuilt: the permissions step becomes non-blocking. | Softened, still a dedicated up-front step. |
| 2026-10-01 | `3597dbf5d` | BUG-222 / BUG-224 (details in 2.6). Adds the "wanted" flag and banner "Not now". | One wrong constant disabled the whole permission surface of the `.dmg` app. |
| 2026-10-01 | `930f8bc9c` | Doc fix: "Telling people they must grant everything is exactly the impression that makes the permission list feel mandatory." | The repo itself notes the effect. |
| 2026-10-02 | the UI-reset package of this branch | After the just-in-time service was in, the user judged the custom surfaces built around it ("this UI looks completely bad ... Apple already provides the permissions and its UI looks good already"): a floating card, inline notes, a Settings > Privacy page and "Enable global shortcuts" buttons. | **The last layer removed.** macOS shows its own dialog; the app adds one toast after a denial (4.10). The decision is recorded in the ADR-0038 amendment. |

### 2.3 Technically required versus grown

Separation, element by element. "Required" means an OS or signing fact forces something;
"grown" means a layer added in response to a symptom the up-front wall itself created.

**Technically required (carried forward):**

| Constraint | Evidence | What it justifies |
|---|---|---|
| TCC keys a grant on code identity, not on the app name; ad-hoc or unsigned builds look like a new app after every update and prompt again. | [D] Apple DTS, "On File System Permissions" (developer forums thread 678819); BUG-083, BUG-217 [commit `b7d92746e`]. | A stable signed identity (self-signed certificate for the managed app, Developer ID for the `.dmg`). Not a runtime refusal. |
| A responsible-code identity is a native Mach-O main executable, not a script. | [D] same thread; BUG-060 [commit `0eebcc051`]. | A real bundle with a native launcher. |
| Usage-description strings are mandatory for the microphone and Apple events and fatal when missing. | [A] Apple Info.plist key pages; BUG-058 class. | One table of strings, a capability check before any native request. |
| Under the hardened runtime, microphone and Apple-event access need the `audio-input` and `apple-events` entitlements or the prompt never appears. | [A] entitlement documentation; [D] DTS thread 741303; [C] CowAgent#3181. | Entitlements in `packaging/macos/entitlements.plist`. Never run on a notarized build yet (unverified). |
| TCC state changes outside the process (Settings toggle, `tccutil`, revocation) and there is no change notification for most services. | [A] status calls return a plain Bool for Accessibility and Screen Recording [A signatures]. | Live, uncached state reads; a silent episode watcher. |
| macOS does not ask again after a decision (per-permission wording in 3.6). | [A for iOS/UIKit, applied to macOS by inference [I]] UIKit "Requesting access to protected resources": "If the person denies permission, the access attempt that initiates the prompt, and any further attempts, fail" (wording not re-verified against the live page). | A stable "denied" state, one click to the right pane, an own-bundle `tccutil reset` as explicit recovery. |
| Silent failures: Screen Recording without the grant returns the desktop wallpaper, not an error; a denied microphone yields zeros; synthetic input without trust is dropped. | [A] "audio recordings contain only silence"; [C] driveshot#54, Slack screen-share reports; audit H1. | A pixel sanity check at the capture site and a digital-silence guard on the microphone. |
| `tccutil reset` is scoped to a bundle id; the wrong id wipes another app. | [A] Apple Files and Folders pages; [commit `38aa4fc08`]. | Reset only from the installed app, only its own id. |
| An Automation consent cannot be queried for a closed target (`procNotFound`, -600). | [A] `AppleEvents.h`: "The target ... must refer to an already running application." | Ask only while a player runs; never launch one to ask. |

**Grown (removed or demoted):**

| Element | Introduced | Stated reason | Verdict |
|---|---|---|---|
| Fail-closed identity gate in front of microphone, computer use, hotkeys, window control | `562232023`, `29f488e60` | Prompt placement (UX); BUG-058 hypothesis | **Accreted.** Identity matters for who may reset and whether to auto-ask, not whether to act on an existing grant. It disabled whole features twice by false negatives (BUG-161 #3, BUG-222). |
| Canonical-path rule for the bundle | `562232023`, repaired in BUG-161 | Tampering signal | **Accreted.** The register's own verdict: "the strict rule bought no safety and costs everything". |
| App-wide warning banner | `4a53eb424` | Dead wake word nobody knew about | **Accreted.** A response to a symptom of the gate. |
| "Set up everything" wizard, ordering table, auto-restart, 180 s per-row timeouts | `4af1cc766` | "extremely annoying" | **Accreted.** It automates the up-front flow. |
| `wanted` / `active_features` flag and banner "Not now" with one-week expiry | `3597dbf5d` (BUG-224) | Automation demanded of every Mac | **Accreted.** A real bug fix for over-asking, but a patch on the up-front model. |
| Restart machinery (`_RESTART_AFTER_CHANGE`, sticky pending-restart flag, global `restart_required`) | `562232023` (`_RESTART_AFTER_CHANGE` [code]) | "both only fully apply after a relaunch" (`b25e63520`, 2026-07-18; said about ordering the onboarding steps, not about the restart machinery) | **Mostly accreted.** The sticky flag caused BUG-159. Only the Screen Recording part rests on an OS observation, and that observation is contested (3.6). |
| `identity_reset` note and marker file | `7d75c6fbc` (BUG-159d) | A rebuild wiped grants invisibly | **Was justified, now vestigial.** After the certificate fix a rebuild changes nothing; per `5176bc0ff` only the managed installer ever wrote it. |
| Onboarding `permissions` step | `562232023`, softened `4e2570d76` | Guided first run | **Accreted.** Its position comment (added in `4e2570d76` / `9af34286a`, 2026-09-30; "... macOS microphone grant exists before the wake-word group's microphone test") only exists because the gate refused the test without a grant. |
| Feature-readiness aggregate (`FEATURE_REQUIREMENTS` rolled up to ready / not ready) | `562232023` | Banner sentence | **Accreted.** The feature-to-permission *map* is neutral domain knowledge and is kept as `used_for` metadata. |
| Automation consent file and hidden launch of Music/Spotify | `5e3d07db3` | Wanted an answer for a closed app | **Accreted.** The real defect was a 3 s timeout that tore the dialog down; the original `prewarm()` was already just in time. |
| Per-frame and per-keystroke probes (microphone read loop, tap callback) | `562232023` | Fail closed on revocation | **Accreted**, and measurably harmful (`b0f9ff2e2`, `b66cb179a`). |
| 2.5 s polling while any wanted grant is missing | `562232023` | Follow a user through Settings | **Accreted.** Focus refresh plus refresh-after-action is enough for a passive page. |
| The custom permission UI of the just-in-time rebuild: a floating card, inline notes in five places, a Shortcuts status note and tip, a Settings > Privacy page | the first just-in-time implementation (2026-10) | Say what is off and offer the way back, in the app | **Removed (2026-10-02).** About 4,200 frontend source lines and 5,500 test lines to re-explain what macOS already shows, plus 170 translated strings per language. The one thing macOS does not do, speaking after a denial, is now one toast (4.10). |

Each of these was a deliberate, reasoned response when it was written. The finding is not that
anyone acted carelessly; it is that every one of them answers a problem the first wall created.

### 2.4 The inverted AD-13, and the missing decision

- The repository currently contains two contradictory rules: AD-13 / HN-5 (degrade, never
  hard-block) and the rewritten checklist rows AX-2, TCC-2 and TCC-4 (refuse, fail closed). The
  frozen file "wins on any conflict" (`docs/plans/cross-platform-mac-linux/README.md`).
- **No human decision to ask up front is recorded anywhere** in git or in the documents. The
  plan files are marked as generated by a planning orchestrator; `f0d4f77ee` has an empty
  body; many permission commits carry an agent co-author trailer. The one documented human
  signal is the user complaint quoted in `4af1cc766`, and it was answered with more up-front
  tooling.
- The sign-off rows TCC-1 to TCC-5 were never filled in (`SIGNOFF-LOG.md` has no TCC rows).

### 2.5 The always-on wake word, fairly

A fair reading of the original intent: the wake word opens the microphone at boot, so "first
use" and "app start" coincide, and asking at a controlled moment is a legitimate wish. That does
not require a gate. The wake loop can wait, say why, and start when the permission is granted
(this is what the code does now, 4.6). Nothing in the history evaluated that alternative.

### 2.6 The BUG-058 / 083 / 159 / 161 / 217 / 222 / 223 / 224 chain

| BUG | Date | Real OS or signing constraint | Self-inflicted by the gate, restart or identity logic |
|---|---|---|---|
| 058 | 07-14 | None confirmed (hypothesis) | Preflight added on an unconfirmed crash theory; real cause was BUG-077 |
| 083 | 07-18 | Ad-hoc hash identity; System Settings ignores anchors while running; frozen Screen Recording preflight | A forced bundle rebuild orphaned the grants; dead Allow button |
| 159 | 08-20 | Grant stranded after a signature change | Sticky restart flag disabled features with every grant in place; reset hidden |
| 161 | 08-21 | Frozen Screen Recording preflight | `/Applications` rejected by the app's own rule; a rebuild loop wiped grants on every start |
| 217 | 09-16 | Ad-hoc signature = per-build hash; Automation consent not queryable for a closed app | Automation outside the model; a 3 s timeout killed the dialog |
| 222 | 10-01 | Frozen-app packaging (AVFoundation had to be named, `LSBackgroundOnly`) | The gate accepted one bundle id; the whole permission surface of the `.dmg` app was dead (no Allow button at all) |
| 223 | 10-01 | A public image needs an Apple account to be signed and notarized | The build script never read the certificate secrets |
| 224 | 10-01 | Automation consent needs a running target | Asked for a feature that is off and launched Music and Spotify hidden to show a dialog |

BUG-222, 223 and 224 were verified only against faked frameworks and macOS CI runners, never on a
user's Mac; their register entries say so.

### 2.7 What the history cannot answer

- Whether macOS really registers an app as denied when it created an input listener before being
  asked (the `b25e63520` claim rests on one Mac and is not corroborated by Apple).
- Whether the native first-use prompt works cleanly for every permission under the current
  bundles. Nothing has been tried; every "verified" statement is on fakes or runners.
- Whether any human explicitly asked for up-front or fail-closed behaviour: nothing shows it.

### 2.8 Verdict

It grew. There was a real technical cost to doing it just in time (the always-on wake word, the
need to detect silent failures) that was never weighed, and a small set of real constraints that
got conflated with it. None of the real constraints requires a banner, a wizard, a readiness gate
or refusing to let the OS show its own dialog.

## 3. What Apple says

The decision in section 4 rests on Apple's own guidance and APIs (3.5 to 3.8). Sections 3.1 to 3.4 held a survey of other products and were removed; the numbering of the remaining sections is kept so existing references stay valid.

### 3.5 Apple reference points

HIG, "Privacy" [A] (the page covers all Apple platforms; the quoted wording was not re-verified against the live page for this review):

> "Ideally, wait to request permission until people actually use an app feature that requires access."

> "Avoid requesting permission at launch unless the data or resource is required for your app to function."

> "If it's essential to provide additional details, you can display a custom screen or window before the system alert appears." Such a screen should "include only one button" with a term like "Continue" or "Next", should not use "Allow" as the button title, and must not offer a way to leave without viewing the system alert.

UIKit "Requesting access to protected resources" [A for iOS/iPadOS; applied to macOS by inference [I]; wording not re-verified against the live page]: "Because a person can change authorization at any time using Settings, always check the authorization status of a feature before accessing it. In cases without a dedicated API, prepare your app to gracefully handle access failures."

What Apple does **not** say (do not over-claim): the HIG has no sentence "do not nag after a denial". That rule is derived from the API behaviour (the system remembers the choice) and from the nagging reports for the Accessibility prompt call [C]. Apple's own `kAXTrustedCheckOptionPrompt` documentation suggests warning "on application startup"; the HIG governs product behaviour and says to avoid launch-time requests.

### 3.6 Per-permission API distinctions

"Request class" is how macOS asks. DIALOG: an OS dialog with an answer button. PROMPT-ONCE: a
request shows at most one dialog that, as far as is known, only offers "Open System Settings"
and the user must flip a switch (the shape is community-observed, **unverified**). NATIVE: the
OS prompts by itself on first access.

| Permission (service name for `tccutil`) | Status read without prompting | Ask | Class | Restart after a grant | After a denial | Info.plist key |
|---|---|---|---|---|---|---|
| Microphone (`Microphone`) | `AVCaptureDevice.authorizationStatus` (tri-state) [A] | `AVCaptureDevice.requestAccess` [A] | DIALOG | Not documented [A silent]; treat as none [I] | Never asked again; recording yields only silence [A] | `NSMicrophoneUsageDescription`; app exits without it [A] |
| Screen Recording (`ScreenCapture`) | `CGPreflightScreenCaptureAccess()` returns a Bool, no tri-state [A] | `CGRequestScreenCaptureAccess()` | PROMPT-ONCE | **Conflicting** [C]: some reports say the preflight is frozen per process and a relaunch is needed; one report (macOS 26.6.2) measured no relaunch. Apple documents neither. | Sticky; no re-prompt API. A removed-and-re-added entry can need a reboot [D] thread 818415. | None documented; the repo ships one for parity, effect **unverified** |
| Accessibility (`Accessibility`) | `AXIsProcessTrusted()` Bool, no tri-state [A] | `AXIsProcessTrustedWithOptions(prompt)` [A]; the prompt call can show again while untrusted [C], so we rate-limit it | PROMPT-ONCE | **Conflicting** [C] | Toggle in Settings | None |
| Input Monitoring (`ListenEvent`) | `CGPreflightListenEventAccess()`; `IOHIDCheckAccess` tri-state [A] | `CGRequestListenEventAccess()` / `IOHIDRequestAccess` [A]. A listen-only event tap needs Input Monitoring only; an active tap needs Accessibility [A, WWDC19 session 701]. | PROMPT-ONCE | Not documented; a tap created before the grant may stay deaf [C] | Sticky | None |
| Post synthetic events (`PostEvent`) | `CGPreflightPostEventAccess()`; `IOHIDCheckAccess` [A] | `CGRequestPostEventAccess()` | PROMPT-ONCE; **alias of Accessibility for asking** in this repo (one request, one pane, one episode) | Conflicting / [I] | Sticky | None |
| Automation (`AppleEvents`) | `AEDeterminePermissionToAutomateTarget(..., askUserIfNeeded=false)`: noErr granted, -1744 not yet decided, -1743 denied, -600 target not running [A header]. Not on the main thread; can hang (Apple forums thread 666528 [D], "clearly a bug"). | Same call with `true`, or the first Apple event | DIALOG | No; checked per send [I] | -1743 forever; `tccutil reset AppleEvents <id>` | `NSAppleEventsUsageDescription` plus the hardened-runtime entitlement [A] |
| Files and Folders (`SystemPolicy*Folder`, `RemovableVolumes`, `NetworkVolumes`) | None; attempt the access [D] | None; the first access prompts, only in a GUI login session [D] | NATIVE | No | Remembered; normal `EPERM` handling | `NSDesktopFolderUsageDescription` etc., optional but recommended [A] |
| Login / background item | `SMAppService.status` [A] | `SMAppService.register()` (non-prompting; the OS shows its own notice) [A] | n/a | No | `openSystemSettingsLoginItems()` is the supported opener [A] | None |
| Keychain | Not TCC | Not TCC | none | n/a | n/a | None |

Camera, speech recognition, Contacts, Calendars, Photos, Location, Bluetooth, Notifications and
Full Disk Access are **not used by Personal Jarvis** and carry no string and no entitlement.

Deep links: `x-apple.systempreferences:com.apple.preference.security?Privacy_<Service>` anchors
are an unsupported implementation detail [D]. They have been observed to land on the wrong pane
when System Settings is already running (BUG-083, macOS 15.7).

### 3.7 macOS version changes that matter

| Version | Change | Evidence |
|---|---|---|
| 10.14 | Automation consent, `AEDeterminePermissionToAutomateTarget`, mandatory usage strings, hardened-runtime Apple-events entitlement | [A] header and key pages |
| 10.15 | Screen Recording, Input Monitoring, Files and Folders; `CGPreflight/Request*Access`, `IOHIDCheckAccess/RequestAccess` | [A] WWDC19 session 701 |
| 13 | `SMAppService`, Login Items pane, "Background Items Added" notice; System Settings redesign broke old anchors | [A] SMAppService docs; [C] for the notice |
| 14 | `SCContentSharingPicker`; legacy `CGDisplayStream` triggers extra consent alerts | [A] docs; [D] thread 760483 |
| 15 | Screen Recording re-confirmation (weekly in betas, relaxed to monthly; cadence of 15.1 and later not verified); "requesting to bypass the system private window picker" alert for non-picker ScreenCaptureKit use; pane named "Screen & System Audio Recording"; Local Network enforcement | [D] thread 765103 for the alert; [C] press snippets for the cadence |
| 26 | No Apple-documented change to the consent model found; cadence of the re-confirmation contradictory across sources | [C] |
| 27 ("Golden Gate", reported released 2026-09-14, search snippets only) | The Accessibility pane is reported renamed "Device Control and Data Access"; local TCC database no longer readable by apps; MDM changes | [C] four developer issues; **unverified**, Apple's notes were unreachable |

Design consequence: "Screen Recording was granted but capture now fails or an alert appears" is a
normal runtime state, not an error. Pane names are static text (no OS sniffing).

### 3.8 Attribution (responsible code) and frozen apps

[D] Apple DTS: the exact algorithm "is not documented, has changed in the past, and may well
change in the future". Documented cases: run from Terminal, the responsible code is Terminal;
spawned as a child or helper, "the parent app is treated as the responsible code"; run by
launchd, the app must be registered through `SMAppService` or the plist needs
`AssociatedBundleIdentifiers`. Applied to Jarvis:

- Launched from Finder or as a login item: the app bundle is the client; children (the Python
  backend, `osascript`, `screencapture`) are **expected, not guaranteed** to be attributed to it [I].
- Launched from a terminal (`python -m jarvis`, the CLI, a dev run): the terminal is responsible,
  the dialog names it, and the grant lands on it. A "granted" state seen in a dev run says nothing
  about the shipped app [D, C].
- The `.dmg` app (`ai.personaljarvis.desktop`) and the managed app (`com.personal-jarvis.desktop`)
  are two TCC clients with separate grants [I, follows from bundle-id keying].
- An exec wrapper that replaces itself with the interpreter makes the interpreter the target [C].
- An ad-hoc or re-signed build loses its grants; a development-signed and a Developer-ID-signed
  build have incompatible designated requirements [D].

## 4. Target architecture

### 4.1 Decision

Remove the wall, then remove our own UI around it. The feature asks at the moment it first
needs the permission, from a user gesture; macOS shows its own dialog and the app draws nothing
around it (no wall, banner, wizard, card, inline note or Settings page). Denial degrades that one
feature honestly, and because macOS stays silent after a denial, the app says it ONCE in its
existing toast with one action (4.10); nothing is asked at launch; nothing nags. Where a
permission cannot be asked "on use" (a global shortcut is a background listener), the
user's save of the shortcut is the gesture (4.5). The way back to a clean slate is the local
command `jarvis permissions reset` (4.16).

### 4.2 Principles (rule AP-35)

AP-35 is the rule name used in code comments and commit messages. It is recorded in the
`AGENTS.md` register (section 3), in `docs/adr/0038-macos-permissions-ask-when-needed.md` (the
same nine principles) and as the prevention rule of BUG-225 in `docs/BUGS.md`; this table is the
detailed statement.

| # | Principle |
|---|---|
| P1 | **Just in time.** Ask when a feature that needs the permission is first used or switched on by the user, never at launch, never in a banner, never as a wizard (HIG, 3.5). |
| P2 | **The feature may ask; it must never act on anything but a live GRANTED.** No path refuses because our own preflight says "not granted" before the OS was asked, and none acts on a permission the OS has not granted. `EnsureResult.granted` is true only for GRANTED and NOT_REQUIRED; PENDING, NEEDS_SETTINGS, DENIED and UNAVAILABLE never proceed; a native request's return value is never evidence of success. Silent-failure traps are checked at the source. |
| P3 | **Let the OS ask where it asks by itself; call the explicit prompt API where it does not.** Never ask by "touching" an API that fails silently. Never create an event tap to provoke a prompt (BUG-058 class): the ask is `CGRequestListenEventAccess` / `IOHIDRequestAccess`, and the tap is created only after the preflight is true. |
| P4 | **Denied is a stable state, not an error to retry.** Degrade the one feature, say why where the user is, offer one click to the right pane, stop. No repeated prompting, no polling banner. `tccutil reset` stays an explicit, user-run reset (`jarvis permissions reset`). |
| P5 | **Grants are detected by silent probing owned by the backend** (an episode watcher), applied in process; a restart is only a hint, because the evidence conflicts (3.6). |
| P6 | **Identity decides who may reset and whether we may auto-ask, not whether we may act.** We act on whatever grant exists. Native requests are made only when running as an installed app bundle, or after an explicit confirmation that names the grantee (`outside_installed_app`). Reset only from the installed app's own bundle id. |
| P7 | **Opt-in features own their permission.** A feature the user has not switched on never touches its permission (wake word, mute music, computer use, global shortcuts). |
| P8 | **Honest degradation, always.** Every refusal carries a stable reason and a full English sentence (logs, support, agents); the app shows ONE toast with at most one action, in its own localized copy, only for a user-started use that failed. |
| P9 | **An AI agent never answers a system dialog.** Agent-facing text is prohibitive; the computer-use engine pauses while a macOS consent window is frontmost. |

### 4.3 Layers

| Layer | Module | Role |
|---|---|---|
| Port (OS adapter) | `jarvis/platform/permissions.py`, `SystemPermissionPort` | Live, uncached, non-prompting `state` reads (shallow by default); `request_native` (returns `dialog_shown`, `no_dialog`, `timed_out` or `unavailable`, never evidence of a grant); `usage_string_present`; `outside_installed_app`; `open_pane`; `reset_row` (own bundle id only). It decides nothing about whether a feature may run. Keeps `ACCEPTED_BUNDLE_IDS`, `AUTOMATION_TARGETS`, `PANE_FAMILY`, `REQUEST_CLASS` and the module-loader seams for fakes, plus `remove_leftover_state_files` (4.13). |
| Service | `jarvis/platform/permission_service.py`, `PermissionService` | The just-in-time layer: `check`, `ensure`, `ensure_all`, `ensure_async`, `ensure_all_async`, `outstanding`, `add_listener`, `open_settings`, `report_failed_use`, `report_use_ok`, `note_reset`, `note_app_activated`, `refresh_episodes`, `invalidate`, `check_deep`, `app_info`, `can_request`, `attach_bus`. No framework import at module scope, no I/O at import (AP-26); off macOS `ensure` returns NOT_REQUIRED before the port is asked anything. Resolves the port per call, so test stubs apply. |
| Protocol | `jarvis/core/protocols.py`, `PermissionGate` | `check`, `ensure`, `ensure_async`, `ensure_all`, `open_settings`. Consumers receive the gate through an injectable hook with a default resolver to the service singleton; `tests/fakes/fake_permission_service.py` injects there. `force_ask`, `report_failed_use`, `report_use_ok`, `note_reset` and `add_listener` are service-only; consumers reach them by capability lookup. |
| Consumers | audio, speech pipeline, desktop app gates, computer use, screen capture (`jarvis/platform/screen_access.py`) and context, dictation insert, window control, ducking, hotkeys | Low-level helpers only `check()` and degrade silently; interactive `ensure` exists only at entry points that carry a user gesture (4.6). |
| Routes | `jarvis/ui/web/permissions_routes.py` | Passive snapshot v2 and single-row GETs; `POST /{id}/request`, `/open-settings`, `/reset` (4.9). The toast calls `request` and `open-settings`; the CI probe, the CLI and agents read the snapshot. `/reset` has no UI caller any more and still refuses scripts. |
| CLI | `jarvis/cli_ctl/commands/permissions.py` | `jarvis permissions status`, `request`, `open-settings` (all through the routes) and `reset`, which runs `/usr/bin/tccutil` locally and never uses the route (4.16). |
| Events | `jarvis/core/events.py` | `PermissionNeeded`, `PermissionResolved` over the existing `/ws` wildcard forwarder (4.9). |
| Frontend | `jarvis/ui/web/frontend/src` (the frontend paths on this page are relative to it) | One toast (4.10): the event twin `lib/permissionEvents.ts`, the planner and state `lib/permissionToast.ts`, the app's existing toast layer and store (`components/ToastLayer.tsx`, `store/events.ts`) and the `permissions.toast.*` strings. Nothing else renders a permission. |

### 4.4 Service contract

`PermissionOutcome`: `granted`, `pending`, `denied`, `needs_settings`, `unavailable`,
`not_required`. `EnsureResult` carries `permission`, `outcome`, `state`, `asked`,
`outside_installed_app`, `agent_detail`, `user_detail`, `reason`, `can_prompt`,
`can_open_settings`, `target`; `granted` is true only for GRANTED and NOT_REQUIRED.

`ensure(permission, *, feature, interactive=True, wait_s=0.0, target=None, trace_id=None,
allow_outside_app=False, force_ask=False)`:

1. Non-darwin: NOT_REQUIRED, publishes nothing, no port access.
2. A read of the state. GRANTED resolves any open episode.
3. RESTRICTED: refuse with reason `restricted` (explanation only, no Settings button).
   UNAVAILABLE (no GUI session, framework missing): refuse with `unavailable`.
4. `interactive=False` (a background consumer): never a native request; opens a
   `background`-origin episode (snapshot only, never a toast, except the wake word in 4.10).
5. `interactive=True` outside an installed app and without `allow_outside_app`: no native
   request; the result says `outside_installed_app` so the toast can offer "Ask macOS now" with
   an explicit confirmation (`allow_outside_app`, a person at the window only).
6. `interactive=True` otherwise: first a capability check (`usage_string_present`; a missing key
   would terminate the process, BUG-058 class), then:
   - DIALOG class and `not_determined`: `request_native`, then wait up to `wait_s` in 250 ms
     polls (`wait_s=0` returns PENDING at once);
   - PROMPT-ONCE class: native request unless one was already made in this process within the
     cooldown, then PENDING or NEEDS_SETTINGS (never "denied" while a dialog may be open);
   - DENIED: no native call, outcome DENIED;
   - Automation: the target must be in `AUTOMATION_TARGETS` (else UNAVAILABLE, logged); asked
     through the killable runner, never under a lock.
7. Never raises for a native failure: UNAVAILABLE with a fixed-template sentence and a debug log
   (AP-30). An unknown permission id is a programming error (`ValueError`).

Per-process cooldown between two native requests, from `_REASK_AFTER_S` in the service
(these are our own figures, not Apple's): microphone 120 s and Automation 120 s (while a dialog
may still be open), Accessibility 600 s, Input Monitoring 600 s, Screen Recording once per
process. An explicit click (`force_ask`) skips the PROMPT-ONCE cooldown and the per-episode single request
(`PermissionService._claim`), never a denial and never a DIALOG dialog that may still be open. A failed native request is not retried automatically for
30 s.

**There is no persisted "asked" memory.** It would resurrect the BUG-083 dead end after
`tccutil reset`, a re-sign or another bundle id (red-team finding, recorded in the service
docstring).

`report_failed_use(permission, *, feature, target=None, trace_id=None, reason=None,
origin=None)` is how a consumer reports a REAL failed attempt to use a permission it was granted.
It never asks and never restarts anything. Two families have a producer, each with ONE reason it
can carry (`reason=None` means that default; a reason the family cannot carry, or any other
permission, falls back to a plain non-interactive `ensure`). `origin` (`user` or `background`)
overrides the family default; a value outside the event vocabulary is ignored, and the existing
callers pass neither argument and behave as before:

- **Screen Recording and Input Monitoring: `restart_hint`**, episode origin `user` by default. The
  one producer of that reason: a consumer calls it after a real capture or tap failure while the
  state reads granted (or after the user returned from Settings and the preflight is still
  negative). Never an automatic restart (`_RESTART_HINT_FAMILIES` in
  `jarvis/platform/permission_service.py`).
- **Automation: `needs_settings`**, episode origin `background` by default. A send to ONE player
  (`target`, a bundle id from `AUTOMATION_TARGETS`; anything else is refused with UNAVAILABLE, opens
  no episode and is never echoed) was refused with `-1743` although the probe read GRANTED. It is
  NOT a restart hint (Apple Events are checked per send) and it never asks: no native request,
  `can_prompt` false. The live state is read once through the guarded Automation read; if it no
  longer reads granted, the plain `ensure` path describes its real reason instead. Otherwise the
  slot of that (feature, player) episode is marked `refused_use` and ONE `blocked` /
  `needs_settings` event is published (a repeat is deduplicated, so a refusal on every voice session
  start stays one event and refreshes the ten minute clock). Its detail is a fixed sentence that
  names the player and says the access reads as allowed but the Apple Event was refused. **A granted
  probe never ends it** (the watcher, a status read and `ensure` all keep it open, because that
  probe is exactly what lied). It ends through `report_use_ok(permission, *, feature,
  target=None)` (a later send to that player landed: `PermissionResolved(granted=True)`), through
  `note_reset` (a reset dropped the decision), or after ten minutes without a report. The
  restart hints of the other two families still end through `note_reset`. While the state reads
  granted, a following `ensure` still answers GRANTED (P2: it is the send that decides, not the
  probe). The effect on a real Mac is unverified: Apple documents `-1743` only as an error code,
  and whether the child `osascript` is attributed to the grantee is open (A14, R5).

It has three callers, all exercised only against fakes:

- Input Monitoring: `jarvis/trigger/backends/quartz.py` (`report_if_deaf`), once per tap when the
  tap runs, the grant is visible, no raw event arrived, Secure Event Input is off and key activity
  was seen. A raw event arriving later ends the episode again (`note_reset`).
- Screen Recording: `jarvis/platform/screen_access.py` (`_unusable_grant_refusal`, reached from
  `verify_frame_is_real`), when the state reads granted yet other apps' windows show no readable
  title. Only a user-started capture reports; a background capture stays quiet. A later frame whose
  window list shows a readable title ends the episode (`_clear_failed_use`, through `note_reset`).
  `jarvis/cu/capture.py` deliberately does not report for a native-capture timeout while the grant
  reads live, because nothing failed for good there.
- Automation: `jarvis/audio/ducking/macos.py` (`mute_others`, a voice session start), when the
  duck script to a player whose probe read GRANTED fails with `-1743`. The player is skipped for
  that session (the other player still ducks), and the ducker reports with `reason="needs_settings"`
  and `origin="background"` on the player's bounded call slot (never longer than the probe timeout:
  the controller holds its lock around `mute_others`). A send that lands for a player that was
  reported calls `report_use_ok`; the switch-on ask (`prewarm`) does too when a fresh ask was
  allowed for such a player, and otherwise keeps answering `needs_settings` for it. A healthy
  session never calls either report. A gate without these methods (a scripted stub) gets the older
  non-interactive `ensure`, which opens an episode only when the live state is no longer granted.
  The ducker retries the player on every session start: that is how a fix in Settings is noticed.

Accessibility input still has no such path (not implemented).

`note_reset(permission)` drops a slot's restart hint or refused-use mark and forgets the per-process
ask cooldown; the reset route calls it after a successful reset. The local `jarvis permissions reset`
command (4.16) is a separate process and cannot call it: the running app notices the changed state
through its live reads, and the command tells the person to quit and reopen the app.

### 4.5 The permission table: trigger, request, denial, route back, after a grant

Class: DIALOG, PROMPT-ONCE, NATIVE (3.6). EVENT_POSTING is an alias of ACCESSIBILITY for asking.

| Permission | Request kind | Trigger (a user gesture) | How we ask | If not granted | Route back | After a grant |
|---|---|---|---|---|---|---|
| **Microphone** | DIALOG | Dictation key or button; push-to-talk; the Call chord and a voice session; switching the **wake word** on (the route asks with `wait_s=0`); the wake self-test button (`wait_s=60`, the test is the JIT moment). In the embedded window, "speak in this conversation" reaches the microphone through the web view's own `getUserMedia`; the app adds no pre-ask (R1) | `request_native` (`AVCaptureDevice requestAccess...`) when `not_determined`, then the stream opens | Voice and dictation refuse (`DictationRefused("microphone_unavailable")` plus `PermissionNeeded`): the native bar shows its own localized sentence, the app shows ONE toast; typed chat unaffected; zeros are never recorded silently | The toast's **Open System Settings** (System Settings > Privacy & Security > Microphone) | Usable at once [I; Apple does not document a restart requirement]. The wake loop that was parked wakes on `PermissionResolved`. A HOLD key pressed during the dialog is **not** started retroactively: press again. |
| **Screen Recording** | PROMPT-ONCE | First capture started by a user goal: computer-use perception, the `screen_snapshot` tool body, a user-requested screen-context or Appshot capture | `CGRequestScreenCaptureAccess()` (once per process; an explicit Allow always) | Capture refuses with an honest error, never a wallpaper frame; `PermissionNeeded(needs_settings)` and, for a user-started capture, ONE toast | The toast's **Open System Settings** (pane "Screen & System Audio Recording"; the pane naming per macOS version is [C], unverified; "Screen Recording" before macOS 15; the toast sentence names no pane). On macOS 15 and later the first capture may also show the OS "bypass the system picker" alert. A stuck entry may need a reboot [D 818415]; `jarvis permissions reset screen_recording` (4.16) forgets the stale record. | The window-title oracle (`_screen_capture_live_check`) decides. A capture timeout while the state reads granted is PENDING ("macOS may be asking you to confirm"), not DENIED. A restart is offered only after a real failed attempt. |
| **Accessibility** and post-event | PROMPT-ONCE | First time Jarvis must type, click or focus for the user: computer-use input, dictation auto-paste, window focus and maximize. AX **reads** (tree, element at point) are background: never prompt, degrade | `AXIsProcessTrustedWithOptions(prompt)`, at most once per 10 minutes per process; an explicit Allow always | Dictation falls back to `clipboard_only`; computer-use tools return `[permission_needed:accessibility] ...`; window tools return the same prefix; a user-started use also shows ONE toast | The toast's **Open System Settings** (pane "Accessibility", reported renamed on macOS 27, 3.7; the toast names no pane, no OS sniffing) | A silent watcher; taps and observers are rebuilt on a false-to-true edge. The first paste after an in-process grant reports `paste_sent` until delivery is observed. |
| **Input Monitoring** | PROMPT-ONCE | A global shortcut is a background listener, so nothing at the moment of USE can carry a dialog; the user's SAVE of a shortcut is the gesture: `PUT /api/settings/keybinds` asks after the save when the key tap is not listening (macOS only, never for a cleared shortcut, never for a re-save of the combo already in force, `wait_s=0`; any caller that passes the route's auth counts as the gesture, like the wake-word switch), and the onboarding voice step's "Skip, I'll use the Call shortcut" click asks once. Never at boot | `CGRequestListenEventAccess()` / `IOHIDRequestAccess` only; the event tap is created only after the preflight is true | The shortcut stays dead and the app keeps working from button and voice; the Esc-to-cancel pill never claims a dead key; a denied save shows ONE toast | The toast's **Open System Settings** (pane "Input Monitoring"; the toast never says "you denied": the entry is reportedly auto-registered after a request, BUG-083 live finding on one Mac, [C], unverified) | The watcher re-arms the hotkey in process (a listener sets the existing reload path). "No raw events arrived" is a restart hint (toast variant "Quit and reopen"), never an automatic restart. |
| **Automation** (Music and Spotify only) | DIALOG | Switching "Mute music while dictating" on **while a player runs**. Never mid-dictation, never by launching a player. (A sign-in or coding-agent window opened through AppleScript for Terminal is outside this service: macOS asks by itself, unverified.) | The killable `osascript` consent runner (120 s, own daemon thread) | Ducking skips that player; a denied switch-on shows ONE toast ("Personal Jarvis needs your permission to control another app.") | The toast's **Open System Settings** (pane "Automation") | Checked per send. A send refused with `-1743` after a GRANTED read becomes one background `needs_settings` episode naming the player (`report_failed_use`, 4.4); it ends when a later send to that player lands (`report_use_ok`), never because a probe reads granted. |
| **Files and Folders** | NATIVE | Whenever the user or an agent touches the folder | The OS prompts by itself; never pre-checked or enumerated "to check"; needs a GUI login session [D] | Normal `EPERM` handling | System Settings > Files & Folders | Immediate |
| **Keychain** | not TCC | Reading a stored key | The `security` vault, then the 0600 file fallback (existing) | File fallback; no UI of its own | `jarvis permissions status` reports the storage kind; `jarvis permissions request credential_store` replays the read | n/a |
| **Login item** | not TCC | Autostart (existing, default on) | A LaunchAgent; the OS shows its own notice [C], see R7 | n/a | Login Items | n/a |
| Camera, Contacts, Calendars, Notifications, Full Disk Access, Location | n/a | Not used | n/a | n/a | n/a | n/a |

**Input Monitoring is asked when a shortcut is saved, from the backend.** `PUT /api/settings/keybinds`
saves, live-applies and persists first, then (macOS only, only for a combo that is actually bound, only
while the key tap is not listening) calls `service.ensure(INPUT_MONITORING,
feature="global_shortcuts", wait_s=0)` from the sync handler (a worker thread). It never waits for the
dialog, never fails the save (a failing ask is logged, AP-30), never asks for a clear, from a GET, from
a rejected save, or off macOS, and never at launch. The outcome reaches the app as `PermissionNeeded` /
`PermissionResolved` (the hotkey trigger re-arms itself on the grant, 4.6). The same route used to
carry a `shortcuts_status` field for the deleted status note; it is gone (4.13). The onboarding voice
step has one more gesture: choosing the Call shortcut instead of a wake word posts
`/api/permissions/input_monitoring/request` once.

### 4.5a The whole user-visible surface: trigger, Apple's dialog, denial

Everything a person sees about a permission, in one table. Apple draws the dialog; the app draws
nothing before or around it. Labels as in the conventions: only the Info.plist keys, the toast and the
refusal texts are **[code]**; every statement about what macOS shows is community-observed or an
inference and **unverified** on a Mac (7.4).

| Permission | Triggered by (a user gesture) | What macOS shows itself | Our words inside Apple's dialog | After a denial or a silent failure |
|---|---|---|---|---|
| Microphone | Dictation key or button, push-to-talk, Call chord or voice session, switching the wake word on, the wake self-test | The system microphone dialog; the answer is final and macOS never asks again [A]; button labels are not documented ("Don't Allow" / "OK" is an inference [I]) | `NSMicrophoneUsageDescription` (en, de, es) | Toast "Personal Jarvis needs microphone access to hear you." with **Open System Settings**. A refused dictation also shows the native bar's own localized sentence (`ui/orb/bus_bridge.py`, unchanged). Typed chat works. All-zero audio while granted: one "denied or muted" notice |
| Screen Recording | The first capture a user starts: computer use, `screen_snapshot`, a screen-context request, an appshot | A one-shot alert that mainly leads to System Settings, no Allow button [C]; on macOS 15 and later an extra "bypass the system picker" alert may follow [D 765103] | None (no usage-string key exists; the string we ship is for parity, effect unverified) | Toast "Personal Jarvis needs screen recording access to see your screen." The capture itself refuses with an honest sentence, never a wallpaper frame; a computer-use mission ends with its spoken readback (`cu_blocked_*`, unchanged) |
| Accessibility | The first time Jarvis must type, click or focus for the user: computer-use input, dictation auto-paste, window control | The same one-shot alert pointing at System Settings [C] | None | Toast "Personal Jarvis needs accessibility access to control other apps for you." Dictated text stays on the clipboard (`clipboard_only`); tools return `[permission_needed:accessibility]` |
| Input Monitoring | Saving a global shortcut; choosing the Call shortcut in onboarding (4.5) | The same one-shot alert pointing at System Settings [C] | None | Toast "Personal Jarvis needs input monitoring access so your shortcuts work in other apps." The shortcut stays dead, buttons and voice work |
| Automation | Switching "Mute music while dictating" on while a player runs | The Automation consent dialog naming the app under control [A behaviour]; labels not documented | `NSAppleEventsUsageDescription`, target-neutral (the dialog names the app itself) | Toast "Personal Jarvis needs your permission to control another app." Ducking skips that player |
| Files and Folders | The first access to Desktop, Documents, Downloads, a removable or a network volume | The system prompt, quoted by an Apple engineer: "'AAA' would like to access files in your Desktop folder. [Don't Allow] [OK]" [D 678819] | `NSDesktopFolderUsageDescription` and its siblings, one short sentence each (en, de, es) | Normal `EPERM` handling; no event, so no toast |
| Keychain | The first read of a stored key | The Keychain prompt | None | The 0600 file fallback; no UI |

The toast sentences never name a settings pane (the names change per macOS version) and are
localized in en, de and es (`permissions.toast.*`). Two more variants exist for every permission: a
**restart** sentence ("Access to {what} is on, but it only works after Personal Jarvis restarts.",
action **Quit and reopen**, only after a real failed use) and, in a development run outside the
installed app, an **outside** sentence with the action **Ask macOS now** (an explicit confirmation of
the grantee, 4.9). A device-policy restriction gets a sentence and no action. The conditions under
which a toast appears at all are in 4.10.

### 4.6 Per-consumer behaviour (execution context is part of the contract)

Rule: `ensure(wait_s>0)` only from a worker thread or via `ensure_async`; a loop, Tk or tap
caller uses `check()` or `wait_s=0`.

| Site | Context | Behaviour |
|---|---|---|
| `jarvis/audio/capture.py`, `MicrophoneCapture` | async | One `ensure_async` at open (`permission_feature`, `interactive`). No per-frame or 0.25 s probes. The watchdog's existing 1 s tick calls `check(MICROPHONE)`; a revoke raises `MicrophoneAccessError` (carries the `EnsureResult`). **Digital-silence guard:** 5 s of exact zeros while the OS says granted triggers one re-check and one `PermissionNeeded` ("access is probably denied or muted"); unverified on a Mac. Restarts are non-interactive. |
| `jarvis/ui/desktop_app.py` gates, `jarvis/speech/pipeline.py` | loop | Two silent predicates: background capture (wake word, barge-in) needs a live GRANTED; a user gesture (push-to-talk, a voice session, dictation) needs "not denied", because the gesture itself asks. The wake loop parks on a reload event set by `PermissionResolved(microphone)`, never a 2 s churn. First press: `ensure("microphone", wait_s=0)`. |
| `jarvis/ui/web/settings_routes.py` | route | Wake switch on: blocking `ensure(wait_s=0)` from the sync handler (a worker thread); the response carries `permission:{outcome, reason, can_open_settings}`. Wake self-test: `ensure_async(wait_s=60)`. GET mic-level uses `check` only. Mute-music PUT asks only when switching on with a player running. `PUT /api/settings/keybinds` asks for Input Monitoring after the save (4.5): blocking `ensure(wait_s=0)`, macOS only, only for a bound combo and only while the key tap is not listening; `GET /api/settings/keybinds` never asks. |
| `jarvis/cu/actuate/base.py` | tool body, worker | `ensure_all([ACCESSIBILITY], wait_s=0)` after the executor authorised the call; non-granted raises `PermissionNeededError`, whose text starts `[permission_needed:accessibility] `. `ToolResult` is unchanged. |
| `jarvis/cu/engine.py` `_dispatch_tool` | worker | Keeps a **silent** Screen Recording gate before every engine action (the AP-3 choke point and the blind-input guard), never interactive there; maps the `[permission_needed:` prefix to a terminal `blocked_permission` mission state with a user Retry, not a model retry loop. **System-consent guard:** while a macOS consent window is frontmost (`jarvis/cu/system_dialogs.py`, an owner-name list that is itself unverified) nothing is dispatched. |
| `jarvis/cu/capture.py`, `jarvis/platform/screen_access.py`, `screen_snapshot`, screen context (`jarvis/screen_context/service.py`) | mixed | Helpers `check()` and degrade; gesture entries (`require_screen_recording[_async]`, `wait_s=0`) ask. A screen-context capture is always a user gesture, so its silent probe is followed by one `require_screen_recording_async`; the status route only runs the silent probe. `verify_frame_is_real` runs a pixel sanity check after the grab while the state claims GRANTED (other apps' windows on screen with no readable title refuses with honest text, flat frame or not) and reports the failed use (4.4). GET status routes never prompt. |
| `jarvis/vision/ax_tree.py`, `element_at_point.py` | worker | AX reads: `check()` and degrade, never a toast. |
| `jarvis/platform/window_state.py` | worker | `ensure(ACCESSIBILITY, feature="window_control", wait_s=0)`; refusal text starts with the `[permission_needed:accessibility]` prefix. |
| `jarvis/dictation/insert.py` | worker | First paste: `ensure(ACCESSIBILITY, feature="dictation_insert", wait_s=0)`; `clipboard_only` is a success state (the text is one paste away); `paste_sent` is the honest middle for the first paste after an in-process grant; never sends Return while a dialog could be frontmost. |
| `jarvis/audio/ducking/macos.py` | worker | Switching on is the only asking path (`prewarm`, off every lock); `mute_others` is non-interactive: it skips and opens a `background` episode. A duck script refused with `-1743` although the read said GRANTED skips that player for the session and calls `report_failed_use(AUTOMATION, target=<player>, reason="needs_settings", origin="background")`; the next send that lands calls `report_use_ok` (only for a player that was reported; a gate without the methods gets a plain non-interactive `ensure`). The controller lock is never held across an ask, and every service call made while it is held is bounded by the probe timeout on the player's own call slot. |
| `jarvis/trigger/hotkey.py`, `trigger/backends/quartz.py` | loop, tap thread | Requirement is Input Monitoring only. Boot start is silent. `HotkeyTrigger` registers a grant listener that re-arms the backend off the loop (`asyncio.to_thread`). The tap callback never calls `ensure` and takes no service lock; it reads a cached verdict. A raw-callback counter feeds `deaf_tap_suspected`, and `report_if_deaf` (worker thread) turns that into one `report_failed_use` per tap (4.4). |

### 4.7 Boot rule

With every permission `not_determined` (including `wake_word_enabled=True`), launch makes **no
OS dialog, no input-stream open, no event-tap creation and no capture call**; an upgrader with
every grant present sees zero toasts and zero requests. The service singleton is lazy; boot order:
construct (no I/O), then `attach_bus(bus, loop)` in `jarvis/ui/desktop_app.py` right after the
`WebServer` constructor (before the speech task can ask for the microphone) and again as the first
line of `WebServer.start()` in `jarvis/ui/web/server.py`; the call only stores two references. `scripts/ci/check_boot_budget.py` runs after touching startup. Tests that
pin it: `tests/unit/speech/test_voice_permission_jit.py` (boot with all `not_determined`, boot of
an upgrader, boot on a non-macOS host) and `tests/unit/ui/test_desktop_macos_permission_gate.py`.

### 4.8 Threading, episodes, watcher

- The service lock guards dictionary mutation only; a native call, a poll, a publish, a listener
  call and a log line never run under it.
- **Episode:** one coalesced "feature X is waiting on permissions Y", keyed by (pane family,
  feature). A feature has at most one open record per permission regardless of `wait_s`; a retry
  loop that re-enters gets PENDING with `asked=False`; at most one native request per permission
  per episode, except that an explicit "Ask now" click (`force_ask`) may repeat a PROMPT-ONCE request
  (Accessibility, Input Monitoring, Screen Recording); a DIALOG-class dialog is never asked twice. An episode ends when every permission it needs is granted
  (`PermissionResolved(granted=True)`) or after ten minutes untouched (`granted=False`). A slot a
  failed-use report marked (a `restart_hint`, or a refused Automation send, 4.4) stays open while
  the state reads granted: its probe is what proved unreliable. A refused Automation send ends
  through `report_use_ok`, `note_reset` or the same ten minutes.
- **Watcher:** an asyncio task (or one daemon thread without a loop) polls open permissions every
  2 s, publishes the edges, calls listeners (cheap, never raising, run on the loop thread when one
  is attached) and stops when nothing is open.
- **Phases and origins:** `phase="os_dialog"` (macOS is asking: the app shows nothing) turns
  `blocked` when the user has to act. For PROMPT-ONCE permissions that is after an app refocus or
  about 15 s still ungranted; for DIALOG permissions when the dialog was open 130 s or the state
  turned denied (our own figures). Only `origin="user"` and `phase="blocked"` may produce the
  toast; `origin="background"` fills the status snapshot only, with ONE exception: a denied
  microphone for the wake word (4.10). Events are published on a state change, never from
  repeated silent checks.
- **Automation reads** can hang [D thread 666528], so every Automation read goes through a guard
  with a hard timeout, one call in flight per player and a quarantine after a timeout. On an
  event-loop thread an Automation read never waits.
- **Screen Recording oracle:** the window-title check enumerates on-screen windows; it runs on a
  gesture entry and, for a gesture-opened episode, at most every 10 s. Whether the enumeration
  itself can trigger the macOS 15 alert is **unverified**, hence the low cadence. A proven grant is
  remembered for SHALLOW reads (the preflight is frozen per process and can only go stale
  negative); a deep read (the computer-use pre-dispatch gate, the episode watcher) never answers
  from that memory and drops it when it finds no grant, so a revoke is seen before the next
  action. The oracle only proves positively, so "no readable window title" also reads as "not
  granted" there; the next deep read that finds a titled window proves the grant again. The GET
  status probes (`/api/screen-context/status`) are shallow: they run no oracle.

### 4.9 Events and snapshot v2

Events (frozen dataclasses in `jarvis/core/events.py`, AP-4 parity: Python tuples regex-read
against the TypeScript twin `jarvis/ui/web/frontend/src/lib/permissionEvents.ts`):

- `PERMISSION_FEATURES`: `voice`, `dictation`, `wake_word`, `computer_use`, `screen_context`,
  `appshot`, `window_control`, `dictation_insert`, `global_shortcuts`, `audio_ducking`,
  `browser_voice`.
- `PERMISSION_NEEDED_REASONS`: `not_determined`, `denied`, `restricted`, `needs_settings`,
  `restart_hint`, `unavailable`. `PERMISSION_NEEDED_PHASES`: `os_dialog`, `blocked`.
  `PERMISSION_NEEDED_ORIGINS`: `user`, `background`.
- `PermissionNeeded(permissions, feature, reason, phase, origin, target, can_prompt,
  can_open_settings, outside_app, detail)`; `PermissionResolved(permissions, feature, granted)`.
  `detail` is a fixed-template English sentence for logs and support, never rendered by the UI
  (the toast has its own localized sentences) and never carrying exception text, paths or
  window titles (AP-34 spirit).
- `DictationRefused` is unchanged; the microphone case publishes both it and `PermissionNeeded`.
- The routine-trigger catalog excludes both events (`2ee24af3c`): the scheduler never dispatches a
  permission episode to a routine.

`GET /api/permissions/status` (sync, never prompts, never blocks):
`{platform, supported, headless, app_identity{app_name, bundle_id, bundle_path,
launched_as_bundle, stable}, outside_installed_app, permissions:[{id, label, status, used_for,
can_request, can_open_settings, can_reset, restart_hint, detail, settings_path}], needed:[open
episodes]}`. The Automation row is computed only while `[ducking].enabled` or `?include=automation`
(reading it asks a running player). Screen Recording is the shallow preflight here (it can only go
stale negative; a grant shows once the service has proven it, 4.8). EVENT_POSTING has no row of its
own. `GET /api/permissions/{id}` returns one cheap row. Both GET routes accept `?activated=1`: the
route calls `note_app_activated()` so a PROMPT-ONCE dialog the person left to flip a switch is
promoted to `blocked` at once. It is a hint about what the user just did, never a prompt (only
the UI's `activated=1` counts). **Nothing sends it since the permission UI was removed** (the toast
does not poll or refetch), so a PROMPT-ONCE episode now turns `blocked` on the roughly 15 s timer of
4.8 only. The parameter stays this round because the routes do (candidate for a later cleanup, or for
a window-focus caller of the toast). The snapshot is read by the CI probe, the CLI and agents; no
frontend seeds from `needed[]` any more.

`restart_hint` on a row is true while a `restart_hint` episode is open for that permission, **even
when the state reads granted** (that is the case it is reported for: granted, yet a real use
failed); the row's `detail` is then the fixed restart sentence. The Keychain row has no request
call: its `can_request` means "Try again", which replays the Keychain read.

A refused Automation send (`report_failed_use`, 4.4) shows as a `needs_settings` / `blocked` /
`background` entry in `needed[]` that carries the player in `target` and a fixed sentence in
`detail` naming it (`feature` `audio_ducking`, `can_prompt` false, `can_open_settings` true on a
macOS desktop). The Automation row keeps the probe's `status` (granted; no key was added to the row,
AP-4), `restart_hint` stays false, `can_request` stays false (nothing to ask), and its `detail` is
that sentence for each such player followed by the "checked only while a player is running" note. A
row names a player only when an open `needs_settings` episode targets it while its own probe reads
granted, so a plain `needs_settings` (the ask withheld outside the installed app, a dialog nobody
answered) is not called a refusal. Both GET routes only read: they never ask, and a probe that
reads granted never closes the episode (only a later landed send does, 4.4). Nothing offers a
reset for it (the state reads granted, so there is nothing to reset). Its `origin` is `background`,
so no toast ever shows it: it is for `jarvis permissions status` and agents.

`POST /{id}/request` hands the permission to `ensure(interactive=True, wait_s=0)` and answers at
once. The body may carry `allow_outside_app` (refused with 403 for an agent: confirming a grantee is for
a person at the UI), `feature` and `target`. The person at the Jarvis window is identified
positively: its session cookie, or (open local access has no cookie) the browser-set
`Sec-Fetch-Site: same-origin` header on a request with no `Authorization` header. Every other
caller is an agent: a script with the control key and equally a local process that presents no
credential at all. A request from the UI passes `force_ask=True` to the service (an explicit click
on the toast's "Ask macOS now" skips the PROMPT-ONCE cooldown); an agent's does not, so a script cannot loop it. For an
agent `/reset` is refused with 403 (a reset forgets the cooldown, so "reset, then request" would
be a prompt loop) and `?activated=1` is ignored. The route has no UI caller now; the person's way to
reset is the local command in 4.16, which does not go through the route. The UI and every other caller have separate
cooldowns and global caps, so a loop from a shell cannot starve the person's click. Residual limit:
a local process that deliberately forges `Sec-Fetch-Site` is indistinguishable from the WebView;
this guards against an agent that follows its instructions, not against malware (the dialog itself
stays macOS's), and whether the macOS WebView sends the header on every supported release is
unverified (a WebView without it only loses `allow_outside_app` and the cooldown skip).
Opening a pane quits a running System Settings first (it ignores the anchor while running,
BUG-083, observed on macOS 15.7, not documented by Apple), except when the same pane URL was the
last one this process opened (`_last_opened_url` in `SystemPermissionPort._open_settings`); the user
may have navigated since, so this is an approximation. `/reset` and `/open-settings`
answer 409 when the operation did not happen (a reset: while the live state is GRANTED, when the
state cannot be reset, or off macOS). Each of the three routes has its own 5 s per-(action, permission
family) cooldown (429 with Retry-After); all three share a global cap of 20 calls per 60 s. Handlers are sync `def`. No agent or router tool exposes ensure, request,
open-settings or reset (guarded by `tests/unit/platform/test_permission_agent_boundary.py`).

### 4.10 The UI: macOS's own dialog, plus one toast

**What the app draws:** nothing before macOS asks, nothing around its dialog. There is no wall,
banner, wizard, floating card, inline note, status note, tip, dictation popover, "Enable global
shortcuts" button or Settings page for permissions. The only thing the app adds is for the one
moment macOS stays silent: a user-started use FAILED because a permission is off (macOS never asks
twice, so after a denial nothing tells the person why the feature does nothing), or a granted
permission still does not work. Then ONE short toast with at most ONE action appears.

**The toast reuses the app's existing toast** (`pushToast` and `ToastLayer` in `store/events.ts`
and `components/ToastLayer.tsx`), extended by an optional `action` (`{label, onAction, keepOpen}`)
and an optional `ttlMs`. No new component, so it looks exactly like every other toast in light and
dark mode and over a terminal pane (theme tokens only). The permission toast stays 20 s (the
default is 3.5 s, 12 s for a toast with a button) and the layer sits at `z-[115]`: above the first-run
spotlight, whose scrim would otherwise swallow the click, and below the caption bar. The source is
the two bus events `PermissionNeeded` and `PermissionResolved` (twin `lib/permissionEvents.ts`,
parity-tested, AP-4), handled in `lib/permissionToast.ts` from the WebSocket hook.

**When it shows (all must hold):**

1. `origin === "user"` and `phase === "blocked"` (macOS is not asking right now), and the reason is
   not `unavailable` (nothing to tell, nothing to do).
2. This window owns host-level prompts: the main window of the embedded desktop app on macOS
   (`lib/embeddedDesktop.ts`). Every window has a socket and the server broadcasts, so without an
   owner N windows would toast N times; a detached solo window, a remote browser (it must never
   offer "Open System Settings" for another computer) and other operating systems never toast.
3. The episode is not already on screen in this window (dedupe per episode and reason). The memory
   is dropped when `PermissionResolved` ends the episode (a grant also takes a still-open toast
   down) and as soon as the toast is gone (dismissed or expired) after a short cooldown, so the
   person's next press of a feature that ends blocked again is told again. The service cooperates:
   a USER gesture that lands on a still-open episode makes it publish the current state once more
   (at most once per 10 s per episode, `_REANNOUNCE_MIN_S`; a background check never does, and
   re-announcing never asks macOS again). A re-save of an identical shortcut is no gesture (4.5).

A **background** origin never toasts, with ONE exception: a denied microphone for the **wake word**
(the always-listening feature the person switched on) is told once per app session.

**The sentences and the one action** (`permissions.toast.*`, en, de and es, short and calm, no pane
names, no jargon):

| Situation | Sentence (en) | Action |
|---|---|---|
| Denied or needs the switch, per permission | "Personal Jarvis needs microphone access to hear you." and one such sentence each for screen recording, accessibility, input monitoring, automation, plus a generic one | **Open System Settings**: `POST /api/permissions/{id}/open-settings` |
| Not asked yet but macOS can still ask | The same sentence | **Ask macOS now**: `POST /api/permissions/{id}/request` with the feature |
| Development run, not the installed app | "... It is not running as an installed app, so macOS will give the access to the app that started it." | **Ask macOS now** with `allow_outside_app: true` (only the person's own click may confirm the grantee, 4.9) |
| Allowed but a real use failed (a `restart_hint` episode) | "Access to {what} is on, but it only works after Personal Jarvis restarts." | **Quit and reopen** (`useRestartApp`; a failure shows the existing `permissions.restart_failed` toast; the toast stays open after the press so a "missions are running" answer can be retried) |
| Device policy (restricted) | "Your Mac does not allow Personal Jarvis to use {what}. Only an administrator can change that." | none |
| Wake word, microphone denied (once per session) | "The wake word cannot listen: microphone access is off for Personal Jarvis." | **Open System Settings** |
| An action request failed | "That did not work. Please open System Settings yourself." | none |

**Browser voice in the embedded window** has no pre-ask (R1): when the web view's `getUserMedia`
is refused by macOS, the realtime control shows a desktop-worded sentence (not the browser "site
settings" one) and reports the refusal once, from the person's own press, with
`POST /api/permissions/microphone/request?dry_run=false` and `{"feature": "browser_voice"}`; the
service opens a user-origin episode, so the microphone toast above takes over. A wake-started call
never reports. Outside the embedded Mac window the browser's own sentence stays.

**Kept unchanged** (they are the other feedback layers, not permission chrome): the native bar and orb
text for a refused dictation (`ui/orb/bus_bridge.py`, en/de/es), the spoken computer-use readbacks
(`jarvis/voice/action_phrases.py`, regex-only, AP-11), the `DictationRefused` and
`ErrorOccurred(ui.web.dictation)` handling that resets the recording pill, and the
`permissions.restart_failed`, `topbar.restart_missions_running` and `sidebar.realtime_microphone_*`
strings. The sidebar "Mic blocked" line and the voice-warming banner's blocked line are gone with the rest.

**Deleted on 2026-10-02:** the floating card and its host and action table, all inline notes (dictation,
wake word, appshot, browser voice, mute music), the Shortcuts status note and tip, the Settings >
Privacy page with its nav and search entries, and the stores, hooks and libraries behind them:
about 4,200 source lines and 5,500 test lines, and roughly 170 of the 184 permission-related
strings per language. The Python parity tests that read the deleted TypeScript twins were removed or
retargeted in the same change.

**Honest limits.** The toast is web-styled, not an Apple alert (that was the trade for deleting the
custom surfaces). It is invisible while the Jarvis window is hidden or not frontmost: a refused
dictation then still shows on the native bar and a computer-use mission is read out, but a deaf
shortcut tap has no other surface. A toast raised while the window was hidden is not queued: it is
told again only when the person tries the feature again with the window visible (the re-announce
above), so the way out of "no message appeared" is to try again, or to open System Settings >
Privacy & Security yourself, or `jarvis permissions reset <permission>` (4.16). Its look in the real WKWebView, whether "Open System Settings" lands
on the right pane on the current macOS, and the whole flow on a Mac are unverified (7.3, M-TOAST-1).
Definition of done for any change here: production build, light and dark appearance and the
terminal-pane appearance inspected (AGENTS.md), vitest and locale parity green, no restart ever
asked of the user. Build and appearance results are not recorded on this page (7.4).

### 4.11 What stays

Stable signed identity (BUG-060, 217, 223); live uncached state reads; the own-bundle `tccutil
reset` (the route from the installed app, the local `jarvis permissions reset` for the person, 4.16;
and the one reset the managed installer runs when a rebuild changed
the bundle's signing identity, `_reset_after_identity_change` in `jarvis/setup/macos_app_bundle.py`;
the explanatory marker it used to leave is gone); the restart *hint* (never a forced restart); the honest
wallpaper-only and zero-audio checks; the CI probe of the built app (`scripts/ci/check_frozen_macos_app.py`);
acceptance of both bundle ids (`ACCEPTED_BUNDLE_IDS` in `jarvis/platform/permissions.py`) for the
reset gate and the outside-app logic.

### 4.12 Packaging

- **One usage-string table:** `jarvis/core/macos_privacy_strings.py`, stdlib-only, loaded by path
  from `jarvis.spec` and from the managed bundle's `Info.plist` writer, so the two apps cannot
  drift. Keys: microphone, screen capture (parity, effect unverified), Apple events, Desktop,
  Documents, Downloads, removable volumes, network volumes, local network. A parity test and the
  frozen-app probe assert it on the built bundle. The wording is short and calm: the Apple Events
  string is **target-neutral** (it names no app: the system dialog itself names the app under
  control, and the same string serves Music, Spotify and Terminal), and the folder strings say
  "works with files in your ... folder when you ask it to" (the dialog already names the folder).
- **German and Spanish dialog text.** The same module holds the de and es strings. Both bundles are
  localised: `Info.plist` declares `CFBundleDevelopmentRegion` (`en`) and `CFBundleLocalizations`
  (`en`, `de`, `es`), and `Contents/Resources/{de,es}.lproj/InfoPlist.strings` carries the
  translated usage strings (UTF-8; English stays the base text in `Info.plist` itself, so there is no
  `en.lproj`). The `.dmg` app gets the files from `packaging/macos/add_localizations.py`, which
  `packaging/macos/build.sh` runs on the finished `.app` BEFORE it signs it (the files are part of
  the code seal, so one added later would break the signature and the notarization); the
  languages are declared by `jarvis.spec`. The managed bundle writes the same files when it lays
  out the bundle (`jarvis/setup/macos_app_bundle.py`). `scripts/ci/check_frozen_macos_app.py` asserts
  the two plist keys and that each `.strings` file equals the table. The bundle format version is
  **not** bumped (a bump would rebuild every installed bundle, and an ad-hoc rebuild is a new
  identity that re-asks every permission): an installed managed bundle carries the new strings after
  the next rebuild that happens for another reason. German and Spanish text is allowed here because
  this is the closed product surface (`scripts/ci/german-allowlist.txt`). That macOS shows the
  localised string in its permission dialog on a German or Spanish system, and that it accepts a
  UTF-8 `.strings` file there, is **unverified** (no Mac was available; Apple's own tooling writes
  UTF-16).
- **Removed:** `NSCameraUsageDescription` plus the camera entitlement,
  `NSSpeechRecognitionUsageDescription`, `NSSystemAdministrationUsageDescription` (the wrong key
  for Accessibility and Input Monitoring, which have no key). A build that brings one back fails CI.
- **Entitlements** (`packaging/macos/entitlements.plist`): `device.audio-input`,
  `automation.apple-events`, `cs.allow-unsigned-executable-memory`,
  `cs.disable-library-validation`. Apple says an entitlements property list must have no comments
  [D thread 701514]; the file has none and the build normalises it with `plutil`. On a signed build
  the probe asserts `codesign -d --entitlements :-`. The shipped images so far were ad-hoc signed
  without the hardened runtime, so **no entitlement has run on a notarized build** (unverified).
- The `.dmg` and managed bundles also had packaging defects fixed in BUG-222 (AVFoundation not
  frozen, `LSBackgroundOnly`), proven on macOS runners (7.4).

### 4.13 What was deleted

Consumer-visible: the banner and its dismissal store, the wizard and its polling, the onboarding
permissions step, the readiness aggregate in the snapshot (`features`), `wanted`, `active`,
`identity_reset`, the global `restart_required` and `foreground`; then, in the UI reset of
2026-10-02, the whole custom permission UI (4.10) and, on the backend, `jarvis/trigger/
shortcuts_status.py` with the `shortcuts_status` field of `GET /api/settings/keybinds` and
`HotkeyTrigger.deaf_tap_suspected()` (the shortcut status note was their only reader; the
backend-level `deaf_tap_suspected` that feeds `report_if_deaf` stays). The routes and snapshot v2
stay (the CI probe, the CLI and agents read them). Backend: the Automation consent
file and the hidden launch of Music and Spotify, the identity-reset marker, the readiness
aggregation, and (BUG-159 fix f) `_reset_or_explain`.

**State of the port (checked by grep, 2026-10-02):** the old members `FEATURE_REQUIREMENTS`,
`active_features`, `runtime_access_granted` and `runtime_feature_ready`, the legacy `snapshot()` /
`request()`, `identity_reset_*` and `automation_consent_path` are deleted from
`jarvis/platform/permissions.py` and from `jarvis/platform/probes.py` (`screen_recording_granted`).
`tests/unit/platform/test_no_legacy_permission_api.py` fails if any of those names returns to
`jarvis/`, `scripts/` or `.github/`. The macOS CI step that asserted the old behaviour was replaced
by "Verify headless permission asks fail closed" (the name is historical): it replaces the three
native request seams with tripwires, then asserts that outside an installed bundle `ensure`
requests nothing (`asked` is false) and that nothing but a granted state reads as granted. Two more
steps were added to `.github/workflows/macos-desktop.yml`: "Probe the native permission symbols" and
"Smoke the permission service without asking". `tests/unit/ci/test_macos_desktop_permission_step.py`
runs those scripts against the FakeTCC simulator on any host.

**Leftovers on upgrade.** `macos-tcc-reset.json` and `macos-automation-consent.json` are never read
any more. The managed source installer deletes them once, best-effort, on its next run
(`remove_leftover_state_files`, called from `ensure_macos_app_bundle`). The frozen `.dmg` app never
gets them deleted: `ensure_desktop_integration` returns early for frozen builds, so that cleanup is
not reached. That is harmless, since nothing reads either file. A stored banner-dismissal key and a
stored onboarding step `permissions` are ignored or mapped to `voice`, and never prompt.

### 4.14 Non-darwin ledger

| Surface | Windows / Linux behaviour | Pinned by |
|---|---|---|
| `ensure` / `check` | NOT_REQUIRED, no events, no port access, no framework import | `tests/contract/test_permission_service_contract.py` (off-macOS scenarios) |
| Snapshot v2 | Rows `not_required`, `needed: []` | route tests |
| Onboarding step list | `permissions` is gone from the platform-agnostic list `welcome, keys, subscriptions, voice, ready` (the UI showed it on macOS only); both sides tested | `tests/unit/setup/test_onboarding_meta.py`, `components/onboarding/setup/setupSteps.test.ts` |
| `jarvis permissions reset` | Prints one line ("only works on macOS") and exits 1; never runs `tccutil` | `tests/unit/cli_ctl/test_commands_permissions.py` |
| `PUT /api/settings/keybinds` | Never touches the permission layer (no ask off macOS) | `tests/unit/ui/web/test_keybinds_input_monitoring_ask.py` |
| Settings nav | There is no Privacy or Permissions section on any OS | `tests/unit/ui/web/test_no_permissions_settings_page.py` |
| Consumers | No gating, no request (FakeTCC call log empty) | consumer tests |
| Hotkey factory | Unchanged | `tests/contract/test_hotkey_backend_protocol.py` |
| Windows microphone-privacy analogue, all-zero audio detection off macOS | Out of scope (A9) | n/a |

### 4.15 Global hotkeys: stage S0 now, a Carbon backend later

**Facts [code]:** on macOS global shortcuts use `QuartzHotkeyBackend`, a listen-only event tap
(`jarvis/trigger/backends/quartz.py`); pynput is the Linux X11 backend. Before this change the
tap required both Accessibility and Input Monitoring, which is stricter than Apple's rule
(listen-only needs Input Monitoring only [A, WWDC19 session 701]).

**Stage S0 (this change):** Input Monitoring only; the tap is not created at boot unless already
granted; re-arm on grant through the service listener (no second path); the asking moment of 4.5 (the
save of a shortcut; the first status note was deleted again, 4.10); stale texts fixed; a raw-callback counter replaces the dead
`received_any_event()` liveness signal. A restart hint appears only when the preflight is true and
no raw event arrived after the user typed (an explicit "still not working" report would need its
own route and UI and does not exist); never an automatic restart. The Appshot both-Option gesture is **not** an Input Monitoring row: its
permission need is unverified (`jarvis/appshot/gesture.py` records that it is unverified whether the
`CGEventSourceKeyState` Option-key read needs Input Monitoring; `jarvis/trigger/hotkey.py` notes that
the sibling `CGEventSourceFlagsState` read needs no event tap and no Accessibility grant, and says
nothing about Input Monitoring; neither read was measured on a Mac); nothing in the app asks or says anything about it.

**Documented follow-up, not shipped here (AP-31: no flag before code reads it):** a Carbon
`RegisterEventHotKey` backend for chords of "modifiers plus exactly one key". Carbon delivers
press and release [A header `CarbonEvents.h`: `kEventHotKeyReleased`] and is widely reported to
need no Accessibility or Input Monitoring [C]; the Apple header and docs do not say so. It cannot
express modifier-only, Fn, side-specific or two-key chords (the shipped Call `f3+f4` and Hangup
`f1+f2` stay on the tap); macOS 15.0 and 15.1 refused hotkeys whose only modifier was Option or
Option+Shift (fixed in 15.2 [D thread 763878]); whether release is delivered when a modifier is
lifted first is unverified. Default Call/Hangup chords for macOS are a maintainer decision.

The red-team **crash-surface list** a Carbon shim must satisfy before any default flips:

1. `restype=c_void_p` on `GetApplicationEventTarget` / `GetEventDispatcherTarget` (otherwise
   64-bit pointer truncation and SIGSEGV); `EventHotKeyID` passed by value.
2. The callback, the `EventTypeSpec` array and the user data are module-level singletons.
3. Main-thread hop in an event-tracking run-loop mode: use the common modes, and treat a hop
   timeout as "deferred", never "failed" (a false failure causes a double registration).
4. At process shutdown do **not** unregister (the OS releases hot keys); a live re-arm waits at
   most 1 s and is non-fatal (the red team cites a 90 s quit stall seen on 2026-09-30).
5. Liveness check is "`NSApp` already instantiated and running", otherwise report "unavailable in
   this mode" and never create an `NSApplication`.
6. The callback only schedules work onto the loop and returns `eventNotHandledErr` on an internal
   error (returning 0 would consume the event).
7. A process-wide `CarbonRegistry`: one handler installed once and never removed, a monotonic
   hot-key id, refcounted OS registrations with fan-out to owners (four `HotkeyTrigger` instances
   exist), diff-based re-registration, per-row results.
8. A sentinel with strike quarantine scoped by app version and macOS build, written after the
   first successful registration and removed in `shutdown()` before `os._exit`; two consecutive
   strikes with the same version and build resolve to the tap and say why.
9. A frozen-app explicit-path load probe.

**Flip rule** (default `auto` may become Carbon-first only if all hold): spike variants green on
macOS 15 arm64, 15 Intel and the current release with every preflight false; a built-app probe on
both architectures (load, register, synthetic chord delivered, unregister, clean exit); one
physical-Mac sign-off from a clean `tccutil reset` (hold-to-dictate including modifier-first
release, no dialog from the hotkey, Secure Input, window drag during re-arm, ten Settings-save
re-arms, quit in under 5 s); one opt-in release cycle with no quarantine reports; and the
`docs/os-parity.md` wording updated.

**The runner spike (shipped):** `scripts/ci/macos_carbon_hotkey_spike.py` and
`.github/workflows/macos-hotkey-spike.yml` (`workflow_dispatch` only; the temporary branch `push`
trigger of commit `59749f859` was reverted in `945fe7949`). Runner matrix: `macos-15-intel` and `macos-15`, plus `macos-26`
only when the dispatch input asks for it (that the label exists for this repository is unverified).
The script records the TCC context first (`CGPreflightListenEventAccess`, `CGPreflightPostEventAccess`,
`AXIsProcessTrusted`, `IOHIDCheckAccess`, macOS build, architecture) with non-prompting reads in a
child process, then runs each risky variant in its own child process group under a hard timeout with
the exit signal captured: register and unregister under a real `NSApplication` loop (plus 200
register/unregister cycles and 50 synthetic presses), register with no `NSApplication`, unregister
off the main thread, duplicate registration, secure event input, installing the handler twice, the
Appshot both-Option read (key actually held, three injection routes), and two controls that tell a
harness bug from a Carbon crash. The report is written after every experiment, so a killed parent
still leaves a table; the verdict "Carbon needs no TCC grant" needs the preflights false in the same
process that received a hot key event, otherwise it reads `inconclusive`. Limits it states itself:
runners pre-grant TCC to bash, `osascript` and Terminal; no dialog; no physical keyboard; no hold
semantics (modifier lifted first, auto-repeat); the hop-mode variant and the off-main register half
are not covered, so flip criterion 1 stays open. **Result:** it ran once from the branch as run `36954304202` on `macos-15` (arm64, macOS 15.7.9)
and `macos-15-intel`; both jobs were green. On arm64 all 11 experiments finished and only the
deliberate control crashed (SIGABRT, as designed); every other variant, including register and
unregister under `NSApplication`, 200 register/unregister cycles with 50 synthetic presses
delivered, duplicate registration, secure event input and the Appshot Option read, ended `ok`. The
report itself marks the run unusable as TCC evidence (`usable=False`: a preflight read granted in
the test process, because runners pre-grant TCC), so it does not settle whether Carbon needs a
grant. Variant G and the off-main register half are still not covered, so flip criterion 1 stays
open. Runner evidence only; nothing on a physical Mac. The Intel per-variant table has not been
transcribed here. The Linux self-test is `tests/unit/ci/test_macos_carbon_hotkey_spike.py`.

### 4.16 Starting over: `jarvis permissions reset`

macOS never asks twice. After a "Don't Allow", or after a re-sign orphaned a record (BUG-083), the
only ways back are the switch in System Settings and `tccutil reset`. The Settings > Privacy page and
its "Ask again" button are gone, so the person's way to a clean slate is a local command:

```
jarvis permissions reset <permission> [--bundle-id <id>] [--yes] [--dry-run]
```

- **macOS only.** Elsewhere it prints one line ("permissions reset only works on macOS ...") and
  exits 1 without running anything, even with `--dry-run`.
- **Runs locally, not through the app.** It runs `/usr/bin/tccutil reset <Service> <bundle id>` with
  the service name from the one table the port uses (`tcc_reset_service`: Microphone, ScreenCapture,
  Accessibility, ListenEvent, PostEvent, AppleEvents). It does NOT call `POST /{id}/reset`, which
  correctly refuses scripts and Bearer callers with 403 (a reset forgets the re-ask cooldown, so
  "reset, then request" from an agent would be a prompt loop), and it works with Jarvis closed.
  `credential_store` has no TCC record and is refused.
- **On the PATH.** A downloaded-`.dmg` user has no `jarvis` on the PATH until the app's console
  entry is linked (`packaging/macos/README.md`, "Reaching the CLI"): run
  `"/Applications/Personal Jarvis.app/Contents/MacOS/jarvis" permissions reset ...` through the
  bundle (never a copy: a launch from outside it is another identity), or link it once. The user
  guides say so and give the raw `tccutil reset <Service> <bundle id>` one-liners as the way that
  needs no command.
- **Whose record.** The bundle id comes from the installed app's own `Info.plist` (the managed
  `com.personal-jarvis.desktop` bundle, else the downloaded `ai.personaljarvis.desktop` app), never
  from the process that runs the command (a terminal). `--bundle-id` accepts only those two ids,
  for a Mac that has both; any other id is refused (exit 2), so it can never reset another app.
- **Guard.** It changes the machine directly, so it fails closed without `--yes` (or
  `JARVIS_CLI_ASSUME_YES=1`), like every destructive CLI action; `--dry-run` prints the exact
  `tccutil` argv and runs nothing. The subprocess passes `NO_WINDOW_CREATIONFLAGS` and decodes UTF-8
  (AP-1); a failing `tccutil` exits 1 with its status.
- **Afterwards.** Quit and reopen Personal Jarvis, then use the feature: macOS asks again. The
  command cannot tell the running app (a separate process), whose live reads see the new state
  anyway. `ScreenCapture`, `Accessibility`, `ListenEvent` and `PostEvent` are system-wide services
  for which `tccutil` may need elevated rights or may toggle instead of deleting (A15).
- **Raw commands** (the same thing without Jarvis, and for the services the repo never resets) are in
  the user troubleshooting guide and in 7.1.

Evidence: the command is exercised on Linux with a recording runner, with `FakeTCC.run_tccutil`
(only the named bundle's row returns to "not asked yet") and with a fake executable standing in for
`/usr/bin/tccutil`; every permission and both bundle ids are checked against the exact argv. That the
real `tccutil` accepts each service name and really removes the entry is unverified (A15, M-RESET-1).

## 5. Assumptions

| # | Assumption |
|---|---|
| A1 | Autostart stays default-on. |
| A2 | The Carbon backend and macOS-specific Call/Hangup defaults are deferred; only the spike ships (one runner run, unusable as TCC evidence; see 4.15). |
| A3 | The camera and speech strings, and the camera entitlement, are removed (no caller). |
| A4 | No strings for services only a child process could reach (Contacts, Calendars, Photos, Location, Bluetooth); unsupported until an agent feature needs one. |
| A5 | The WKWebView media-capture delegate (the WebKit decision that sits on top of the TCC dialog) is an open item; the app does not pre-ask before `getUserMedia` (R1): the web view's own request raises the dialog, and a refusal in the embedded Mac window is reported to the service (`POST /api/permissions/microphone/request`, feature `browser_voice`) so the toast can say what to do. |
| A6 | The macOS 27 Accessibility label is static text; no OS sniffing. |
| A7 | The Appshot both-Option permission need is unverified. |
| A8 | No telemetry. The field signal is the support-visible `PermissionNeeded` detail in logs. |
| A9 | A Windows microphone-privacy analogue and cross-OS all-zero audio detection are out of scope; the digital-silence guard is macOS-only. |
| A10 | There is no persisted "asked" memory; the once-per-process and cooldown rules are in memory. |
| A11 | The PROMPT-ONCE shape (the dialog only offers "Open System Settings") is community-observed, not Apple-documented. |
| A12 | The timing figures (15 s "blocked" after a PROMPT-ONCE request, 130 s DIALOG ceiling, 10 s oracle cadence, 120 s and 600 s cooldowns, the 5 s zero-audio window) are our own choices, not Apple's. |
| A13 | The list of system consent window owner names in `jarvis/cu/system_dialogs.py` is unverified; a miss leaves the other layers (prohibitive agent text, the grant itself) in force. |
| A14 | An `osascript` child is attributed to the app for Automation (the ducking sender); unverified. Apple documents `-1743` only as an error code; its behaviour after a grant on a real Mac is unverified. A `-1743` after a GRANTED read is reported as one background `needs_settings` episode naming the player (`report_failed_use`, 4.4) and ends only when a later send lands (`report_use_ok`). |
| A15 | `tccutil reset` for the system-wide services (`ScreenCapture`, `Accessibility`, `ListenEvent`, `PostEvent`) may need elevated rights or may toggle rather than delete [D thread 788454]; `jarvis permissions reset` for them is unverified (the route additionally verifies the state after a reset; the local command only reports the exit status of `tccutil`). |
| A16 | Keeping `NSScreenCaptureUsageDescription` is harmless; whether macOS uses it is unverified in both directions. |
| A17 | Screen Recording consent re-confirmation on macOS 15 and later is a recurring normal state, not an error (3.7). |
| A18 | The toast (4.10) is the whole permission UI. It is web-styled, shown only in the owner window while it is visible, and its look in the real web view is unverified. The ~20 s lifetime, the 10 s re-announce window and the dedupe rule are our own choices. |
| A19 | `CGRequestListenEventAccess` called from the save of a shortcut shows macOS's Input Monitoring alert (community-observed for the PROMPT-ONCE class, unverified); the save is chosen because a background listener has no "on use" moment. |
| A20 | macOS shows the German or Spanish `InfoPlist.strings` text in its permission dialogs on a system in that language, and accepts a UTF-8 `.strings` file there; unverified. English is the fallback for every other language (`CFBundleDevelopmentRegion`). |

## 6. Open points and risks

Everything below is **unverified on a physical Mac**. "Closes with" refers to rows in section 7.

| # | Risk or open point | Why it matters | What the code does today | Closes with |
|---|---|---|---|---|
| R1 | **WKWebView `getUserMedia` double dialog.** In the embedded window, `getUserMedia` reaches the same microphone permission; WebKit's own media-capture decision (`WKUIDelegate`) is additional. No handler exists in `jarvis/`. | A user may see two prompts, or WebKit may deny after TCC allowed. | Since the UI reset the app no longer asks before `getUserMedia`: macOS asks when the web view first opens the microphone, and a refusal in the embedded Mac window shows a desktop-worded sentence under the realtime control and is reported once, from the person's own press, to the service (feature `browser_voice`), which opens a user-origin episode so the one toast ("Open System Settings") takes over. The delegate grant is a tracked open item (A5). | M-MIC-3 |
| R2 | **macOS 27 pane naming.** The Accessibility pane is reported renamed; whether the `Privacy_Accessibility` anchor still resolves is unknown. `NSWorkspace.openURL` returns success even when the wrong pane opens. | The "Open System Settings" button could land on the wrong page. | The toast sentence names no pane; the app quits a running System Settings before opening a pane, unless this process last opened the same pane (an approximation: the user may have navigated since). | M-AX-4 |
| R3 | **Screen Recording restart conflict.** Reports conflict on whether a running process sees the grant. | Wrong either way: a forced restart annoys, no restart leaves a dead feature. | Re-probe (shallow preflight, then the window-title oracle), try a real capture, offer "Quit and reopen" only after a real failure. | M-SR-4 |
| R4 | **Accessibility re-prompt and attribution.** The prompt call may show again while untrusted [C]; a Ventura toggle bug returned wrong values [C]. | Nagging or a false "granted". | Rate-limited to once per 10 minutes per process; the paste after an in-process grant reports `paste_sent`. | M-AX-2, M-AX-3 |
| R5 | **Attribution of child processes.** DTS says the algorithm is undocumented; helper binaries have been separate TCC clients in other products. | The ducker's `osascript`, `screencapture`, ffmpeg or agent CLIs may need their own grant, or may be attributed to Terminal in a dev run. | Expected, not guaranteed. A `-1743` from the ducker's `osascript` after a granted read is reported as a background `needs_settings` episode that names the player (4.4, 4.9). No in-app text about attribution exists (there is no permission page); saying that a grant applies to Jarvis and the tools it starts only as far as macOS attributes them stays a documentation point (the user guide). | M-DEV-1, M-AUTO-3 |
| R6 | **Appshot both-Option need.** Unverified whether the `CGEventSourceKeyState` Option-key read needs Input Monitoring (`jarvis/appshot/gesture.py`); `jarvis/trigger/hotkey.py` notes that the sibling `CGEventSourceFlagsState` read needs no event tap and no Accessibility grant, and says nothing about Input Monitoring; neither read was measured on a Mac. | It is the default Appshot shortcut and has no gesture that "enables" it. | Not an Input Monitoring row; nothing asks or says anything about it. The runner spike can read it, but runners cannot prove the permission outcome for a real user. | M-HK-5 |
| R7 | **Autostart notice.** The LaunchAgent runs `/usr/bin/open` without `AssociatedBundleIdentifiers`, so Login Items shows the program or organisation name, and macOS posts its own "Background Items Added" notice [A for attribution, C for the notice]. | Looks like an unexplained permission prompt. | Autostart stays default-on (A1); documented as a product decision. | M-LOGIN-1 |
| R8 | **Hardened-runtime entitlements never ran on a notarized build.** | A missing `audio-input` entitlement silently suppresses the microphone prompt [C, D]. | The probe asserts `codesign -d --entitlements :-` on a signed build; every runner build so far was ad-hoc signed. | M-SIGN-1 |
| R9 | **Keyboard-layout and input-source collision.** The default dictation toggle chord equals a macOS input-source shortcut when several input sources exist [I]. | A dead shortcut that is not a permission problem. | Not changed. | M-HK-4 |
| R10 | **Intel versus Apple Silicon.** Runners cover both; grants and prompts were never compared on hardware. | Architecture-specific frozen-app defects. | Runner smoke on both. | M-ARCH-1 |
| R11 | **Screen Recording first-capture double dialog and the monthly alert** on macOS 15 and later; an app cannot suppress it. | A first capture may show two dialogs; a capture timeout while an alert is up is not a denial. | A timeout while the state reads granted is PENDING; no periodic background capture without opt-in (P7). | M-SR-2, M-SR-5 |
| R12 | **Stranded grant after a re-sign** (checkmark on, kernel refuses). | Settings shows the switch on and the feature does not work. | The person runs `jarvis permissions reset screen_recording` (4.16) and reopens the app, or removes the entry with minus and re-adds it (reboot hint). The app offers no button for it. | M-SR-6 |
| R13 | **Files and Folders in a headless session.** The prompt appears only in a GUI login session [D]. | A launchd-started backend is silently denied. | Normal `EPERM` handling; nothing pre-checks or enumerates those folders. | M-FF-1 |
| R14 | **Digital-silence guard threshold.** 5 s of exact zeros while granted is our own figure. | Could mis-report a deliberately muted source, or miss a quiet one. | One report, stays open, the stream is not torn down. | M-MIC-5 |

Follow-ups, not part of this change: the Carbon backend (4.15), macOS Call/Hangup defaults, the
`WKUIDelegate` media-capture grant, `AssociatedBundleIdentifiers` for the LaunchAgent, a
`Capabilities.ax_permission_granted` cleanup (the probe still exists in `jarvis/platform/probes.py`
and feeds the capability record), a `report_failed_use` path for Accessibility input (4.4; the
Automation path for ducking exists), and any per-turn computer-use permission message (cut from v1: the one toast plus the mission's `blocked_permission` ending; a deck journal
line by trace id was never built). Also open: a window-focus caller for the `?activated=1` hint (4.9), and a surface for a deaf
shortcut tap while the Jarvis window is hidden (the toast is invisible then, 4.10).

## 7. Manual test checklist for a real Mac

Nothing in this section has been run: no physical Mac existed while the design and the UI reset were
built. Each row is a check that fakes and Linux gates cannot make.

### 7.0 Prerequisites

- **Macs and versions:** at least one Apple Silicon Mac and one Intel Mac, on macOS 15 (the version
  with the live findings in 2.2) and on the current release; a macOS VM snapshot is preferred (7.1).
  Record the macOS version and build, the architecture and the app version in every row.
- **Builds:** the `.dmg` app from a release or from the desktop installers workflow
  (`docs/desktop-installers.md`, "macOS"); drag it to Applications. The managed source-install app
  comes from the one-line installer in `README.md` (`install/install.sh`). Both are listed with
  their bundle ids in 7.1. State which build and signature (ad-hoc, self-signed, Developer ID)
  each row ran on.
- **Voice turns:** use a subscription or a local model; send one turn, never a loop against a paid
  provider.
- **Where things are (UI strings from `en.json`):** there is **no** permissions page and no
  **Privacy** entry in Settings. The dictation keys are on **Voice > Shortcuts** ("Push to talk",
  "Hands-free"); read them there and write them down: the defaults in `jarvis/core/config.py` are
  `ctrl+right_alt+j` (hold to talk) and `ctrl+right_alt+space` (press to start, press again to
  stop); on a Mac keyboard record what the page shows for the right-hand Alt key. Saving a
  shortcut is possible on **Voice > Shortcuts**, on the **Keyboard shortcuts** page and under
  **Settings > Keyboard shortcuts**. "Mute music while dictating" is under **Settings > Overlay &
  taskbar**. The wake word is under **Settings > Wake word** ("Activate wake word", "Test wake
  word"). The composer's dictation button is labelled "Dictate"; the voice-conversation control is
  "Speak in this conversation". The one toast in the top-right corner is the whole permission UI of
  the app (4.10).
- **Result column:** write PASS or FAIL. A row whose Expected cell says "record" passes when the
  observation is written down in the Result cell and shows no crash, no hang and no dialog the row
  does not predict; anything else is FAIL with a note.

### 7.1 Setup, identities and reset commands

- Bundle ids used by the repo (`jarvis/core/branding.py`, `ACCEPTED_BUNDLE_IDS`):
  - `.dmg` app: **`ai.personaljarvis.desktop`**;
  - managed source-install app: **`com.personal-jarvis.desktop`**.
- Each is a separate TCC client with separate grants. Test each one; always reset the id of the
  app under test.
- **Quit Jarvis first, then reset, then start it.** A reset while the app runs leaves stale state in
  the running process. After any reset, open System Settings > Privacy & Security and check the
  list: an unchanged list means the reset did nothing (see the caveats below).
- **The supported reset is the CLI (4.16):** `jarvis permissions reset <permission> --yes` for the
  installed app, or with `--bundle-id <id>` for the other one. The raw `tccutil` commands below are
  what it runs, and they also cover services the repo never resets (the `SystemPolicy*` names are [C] /
  unverified). That each name is accepted by `tccutil`, and that each reset really removes the entry,
  is unverified for every macOS version: check each command on the Mac under test.

Reset everything for one app (replace the bundle id with the one under test):

```bash
for s in Microphone ScreenCapture Accessibility PostEvent ListenEvent AppleEvents \
         SystemPolicyDesktopFolder SystemPolicyDocumentsFolder SystemPolicyDownloadsFolder \
         SystemPolicyRemovableVolumes SystemPolicyNetworkVolumes; do
  tccutil reset "$s" ai.personaljarvis.desktop   # or com.personal-jarvis.desktop
done
```

Single services, used by the rows in 7.3 (`<id>` is the bundle id under test):

| Permission | CLI | Raw command |
|---|---|---|
| Microphone | `jarvis permissions reset microphone --yes` | `tccutil reset Microphone <id>` |
| Screen Recording | `jarvis permissions reset screen_recording --yes` | `tccutil reset ScreenCapture <id>` (system-wide service, may need `sudo`, may toggle instead of delete) |
| Accessibility | `jarvis permissions reset accessibility --yes` | `tccutil reset Accessibility <id>` |
| Post synthetic events | `jarvis permissions reset event_posting --yes` | `tccutil reset PostEvent <id>` (reset it together with Accessibility) |
| Input Monitoring | `jarvis permissions reset input_monitoring --yes` | `tccutil reset ListenEvent <id>` |
| Automation | `jarvis permissions reset automation --yes` | `tccutil reset AppleEvents <id>` |
| Files and Folders | none | `tccutil reset SystemPolicyDesktopFolder <id>`, `tccutil reset SystemPolicyDocumentsFolder <id>`, `tccutil reset SystemPolicyDownloadsFolder <id>`, `tccutil reset SystemPolicyRemovableVolumes <id>`, `tccutil reset SystemPolicyNetworkVolumes <id>` |

- Prefer a fresh macOS VM snapshot between runs (Apple DTS: "the most reliable way to test TCC is
  in a VM, restoring to a fresh snapshot between each test" [D]; the thread id was not recorded
  when this page was written, so the wording is unconfirmed).
- "Dialog" below always means Apple's own system dialog. Jarvis shows its own toast only where a row
  says so, and never while a dialog is open.

### 7.2 Environment-level rows

| ID | Scenario | Steps | Expected | Result | Signed off (name, date) |
|---|---|---|---|---|---|
| M-FRESH-1 | Fresh install (`.dmg`) | Quit the app; run the "reset everything" loop of 7.1 for `ai.personaljarvis.desktop`; install the `.dmg` (drag to Applications); start the app from Applications; walk the first-run setup (welcome, keys, subscriptions, voice, ready) | App starts. No Apple dialog, no toast, no banner, no wizard at launch or on the welcome, keys and subscriptions steps, and no permissions step in setup. On the voice step, switching the wake word on is a user gesture and raises Apple's microphone dialog (expected; record its text and language). If the wake word stays off, no dialog appears during setup. Settings has no Privacy entry | | |
| M-FRESH-2 | Fresh install (managed app) | Same loop for `com.personal-jarvis.desktop`; install with the one-line installer (7.0) | Same as M-FRESH-1, including the voice-step microphone dialog | | |
| M-FRESH-3 | Boot with the wake word on and nothing granted | Enable the wake word in a previous run, quit the app, run the microphone reset for the app under test, start the app | No dialog at launch; no input stream opened; no toast (the wake loop only parks); record whether anything is visible. After pressing the dictation button the dialog appears. Separately deny the microphone and restart: exactly one toast "The wake word cannot listen: microphone access is off for Personal Jarvis." with **Open System Settings**, once per app session | | |
| M-UPG-1 | Upgrader with all grants present | Grant everything, then update or restart the app | Zero toasts, zero requests, features work at once | | |
| M-DEV-1 | Terminal-launched dev run (outside the installed app) | `python -m jarvis` (or the dev launcher) from a terminal | The outside-app path: no native request without the confirmation. After a user-started use, the toast says Jarvis is not running as an installed app and offers **Ask macOS now**; pressing it asks macOS, whose dialog names the terminal app as the grantee. A "granted" state here proves nothing about the installed app | | |
| M-APP-1 | `.dmg` app versus managed app | Grant the microphone to one, start the other | The other app is not granted and asks on its own first use | | |
| M-ARCH-1 | Intel and Apple Silicon | Repeat M-FRESH-1 and the microphone and Screen Recording rows on one Mac of each architecture | Same behaviour; record any difference | | |
| M-UI-1 | Light and dark appearance of the toast | Trigger a blocked episode (deny the microphone, press **Dictate** again) in light and in dark mode, and over a terminal pane | One toast in the top-right column: the app's normal toast look (nothing different from, say, a "saved" toast except its button), one sentence, the button **Open System Settings**; legible and on theme tokens in both modes, does not cover the terminal controls, does not take focus from the dictation target, stays about 20 s, closes on the X and on the button. Record how it looks in the real WebView | | |
| M-UI-2 | Toast during first-run setup | Provoke a blocked episode while the setup spotlight is up (switch the wake word on, deny the dialog) | The toast sits above the spotlight scrim and its button is clickable | | |
| M-TOAST-1 | Owner window and duplicates | With the main window and one detached window open, provoke a blocked episode; then open the app in a remote browser | The main window shows one toast; the detached window and the remote browser show none | | |
| M-TOAST-2 | Dedupe | Provoke the same blocked episode twice in a row | One toast for the episode; after the permission is granted and revoked again, a new failed use may show a new one. A grant while the toast is open takes it down | | |
| M-TOAST-3 | Hidden window | Hide or minimise the Jarvis window, then dictate into another app with the microphone denied | No toast is visible (expected); record that the native bar shows its own sentence and that nothing else is missing. Press **Open System Settings** later from the visible toast, if one was kept | | |
| M-TOAST-4 | Button wiring per variant | For one denied permission press **Open System Settings**; for a not-yet-asked one press **Ask macOS now**; for a restart hint press **Quit and reopen** | Settings opens on the right pane (R2); the native dialog appears; the app restarts (and refuses while a mission runs, with the existing message) | | |
| M-LOC-1 | Localised dialog text | Check `ls "<app>/Contents/Resources"` for `de.lproj` and `es.lproj` in both builds and `plutil -lint` on each `InfoPlist.strings`; `codesign --verify --deep --strict "<app>"`; set the macOS language to German, reset the microphone, press **Dictate** | The files exist and lint; the signature verifies (the strings are inside the seal); the microphone dialog shows the German sentence (record), Spanish the same, any other language the English text; the Automation and folder dialogs carry the short target-neutral wording | | |
| M-RESET-1 | `jarvis permissions reset` | With the app closed, run `jarvis permissions reset microphone --dry-run`, then with `--yes`, for each bundle id (`--bundle-id` for the other one); also try it from a non-macOS host and with a foreign `--bundle-id` | The dry run prints `/usr/bin/tccutil reset Microphone <id>` and runs nothing; the real run exits 0 and the entry disappears from System Settings; the next use asks again; another app's entry is untouched; the foreign id is refused (exit 2); off macOS one line and exit 1. Record whether `screen_recording`, `accessibility` and `input_monitoring` need `sudo` or only toggle (A15) | | |
| M-SIGN-1 | Signed build entitlements | On a Developer ID signed build: `codesign -d --entitlements :- "<app>"` | Lists `audio-input`, `apple-events`, the two `cs.*` keys; no camera key; the microphone dialog appears on first use | | |

### 7.3 Per permission

For every row: record the exact dialog text, whether the app was frontmost, and whether anything
unexpected appeared (a second dialog, a hang). "The toast" is the one toast of 4.10; the sentence per
permission is in 4.5a.

| ID | Permission | Trigger action | Expected dialog or behaviour | Denial path | Route back | After-grant behaviour |
|---|---|---|---|---|---|---|
| M-MIC-1 | Microphone | Quit the app, reset the microphone (7.1), start it, then press **Dictate** in the chat composer (UI-started) | Apple microphone dialog naming the app with the usage string; no toast while the dialog is up | Press Don't Allow: the native bar shows its refusal sentence and the app shows the toast "Personal Jarvis needs microphone access to hear you." with **Open System Settings**; typed chat still works | The toast's **Open System Settings** opens System Settings > Privacy & Security > Microphone | Allow it there: dictation works without a restart (a second press of **Dictate**); the toast, if still open, goes away |
| M-MIC-2 | Microphone, hold key | Same reset as M-MIC-1, then press and hold the hold-to-talk shortcut (7.0) | First press raises the dialog; releasing does not start a late recording | After Allow, nothing starts on its own; the next hold works | as above | as above |
| M-MIC-3 | Microphone, voice conversation | Same reset as M-MIC-1, then press **Speak in this conversation** (embedded window) | The web view's own microphone request (R1: note whether WebKit adds a second dialog); the app adds no pre-ask | Deny: the realtime control shows its desktop-worded "microphone access was denied" sentence AND the microphone toast with **Open System Settings** appears (the press reports the refusal to the service, R1) | System Settings > Microphone | Press again after allowing |
| M-MIC-4 | Microphone, wake word | Same reset as M-MIC-1, then switch **Settings > Wake word > Activate wake word** on; separately try **Test wake word** | The switch is the gesture: the dialog appears; the test button waits up to a minute for the answer | Deny: the wake loop stays parked; the switch's own ask (a user gesture) shows the microphone toast; on later starts with the wake word on and the microphone denied, exactly one wake-word toast per app session says it cannot listen | as above | The parked wake loop starts on its own after the grant |
| M-MIC-5 | Microphone, zeros | With the app granted, set the input to a muted or disconnected source (or revoke while running) | After about 5 s of exact zeros: one "denied or muted" notice, no crash; revoking while running ends the stream within a few seconds and shows the toast | n/a | as above | Restore and retry |
| M-SR-1 | Screen Recording | Quit, reset `screen_recording`, start; ask Jarvis to look at the screen (the snapshot tool or computer use) | `CGRequestScreenCaptureAccess` dialog offering Open System Settings; the capture refuses honestly (**no wallpaper frame**) | Don't allow or ignore: the toast "Personal Jarvis needs screen recording access to see your screen." appears about 15 s after the unanswered or ignored dialog (4.9: nothing sends the `activated` hint any more, so a refocus alone does not raise it), no repeated prompting | Pane "Screen & System Audio Recording" (or "Screen Recording" before macOS 15); the toast's **Open System Settings** | After flipping the switch, return to Jarvis: note whether capture works in the running process (R3), and whether the **Quit and reopen** variant appears (only after a real failed capture) |
| M-SR-2 | Screen Recording, macOS 15 and later | Capture twice, as a user, after the grant | Note any "bypass the system picker" or "Allow for one month" alert, its timing, and that a capture timeout during it is shown as pending, not denied | n/a | n/a | n/a |
| M-SR-3 | Screen Recording, computer use | Start a computer-use task | One coalesced episode for Screen Recording and Accessibility if both are missing (one toast, one sentence for the first); the agent text tells the agent not to touch the dialog; with a consent window frontmost nothing is dispatched | Mission ends as `blocked_permission` with the spoken readback and a Retry | panes | Retry continues |
| M-SR-4 | Restart conflict | Grant while Jarvis runs, return to Jarvis, capture again | Record: works without restart / works only after restart / still blocked; the restart toast appears only after a real failed attempt | n/a | n/a | n/a |
| M-SR-5 | Lapse | Revoke the grant while running, run a computer-use task | The next action fails closed with an honest message | n/a | pane | n/a |
| M-SR-6 | Stranded entry | After a re-sign or a manual remove and re-add, the switch shows on but capture fails | The restart toast, or no toast at all (nothing reads "denied"); run `jarvis permissions reset screen_recording --yes` with the app closed, reopen, retry | n/a | the CLI reset, or remove with minus and re-add; reboot if the entry is stuck | macOS asks again on the next capture; record |
| M-AX-1 | Accessibility | Quit, reset `accessibility` and `event_posting`, start; use dictation into another app (auto-paste), or a computer-use click | First time Jarvis must type or click: Apple Accessibility dialog (offers Open System Settings); until granted the dictation falls back to the clipboard and says so | Deny: text stays on the clipboard (`clipboard_only`); the toast "Personal Jarvis needs accessibility access to control other apps for you." | Pane "Accessibility" (macOS 27: note the actual label, R2) | Flip the switch: the first paste after the grant is reported as sent, not as inserted, until observed |
| M-AX-2 | Accessibility, no re-prompt | Click the same action repeatedly while untrusted | At most one dialog per 10 minutes per process, and one toast per episode | n/a | n/a | n/a |
| M-AX-3 | Accessibility reads | Without the grant, use anything that reads the UI tree | No dialog, no toast; the feature degrades quietly | n/a | n/a | n/a |
| M-AX-4 | Deep link | Press the toast's **Open System Settings** for Accessibility, Input Monitoring and Screen Recording with System Settings already open on another pane | The right pane opens (the app quits a running System Settings unless this process last opened the same pane, then opens the anchor); on macOS 27 note whether the anchor resolves. Extra step: open pane A, navigate manually to another pane in System Settings, press the button for A again, and record whether the pane shown is A | n/a | n/a | n/a |
| M-IM-1 | Input Monitoring, saving a shortcut | Quit, reset `input_monitoring`, start; open **Voice > Shortcuts**, record a new Call chord and save it | Apple Input Monitoring dialog right after the save; **nothing at boot**; the shortcut is saved either way; saving again in the same episode asks nothing new; clearing a shortcut asks nothing; with the permission granted and the tap listening a save asks nothing | Deny: the toast "Personal Jarvis needs input monitoring access so your shortcuts work in other apps." with **Open System Settings**; buttons and voice keep working; Esc-to-cancel never claims a dead key | Pane "Input Monitoring" | The shortcut works in the running process or after a restart: record which; the restart sentence appears as the **Quit and reopen** toast only after real typing produced no events |
| M-IM-2 | Input Monitoring, onboarding | Fresh install, voice step: choose "Skip, I'll use the Call shortcut" | The ONE click asks for Input Monitoring (Apple dialog); the sentence "No wake word? The Call shortcut works anytime." is true afterwards; no dialog if the wake word was chosen | Deny: no toast during setup is expected (the click is the gesture; record whether one appears) | as above | as above |
| M-HK-1 | Hold-to-dictate | Hold the dictation shortcut in another app | Dictation starts on press and ends on release | n/a | n/a | n/a |
| M-HK-2 | Hold-to-dictate, modifier released first | Hold the dictation shortcut, release the modifier key before the letter key | Record whether dictation ends, or keeps recording until the letter key is released (settles part of the Carbon flip rule in 4.15) | n/a | n/a | n/a |
| M-HK-3 | Re-arm and quit | After a clean reset and one grant: save a shortcut in Settings ten times in a row; drag a window during a re-arm; then quit the app and time it | Each save re-arms the shortcut without a dialog; dragging stays smooth; the app quits in under 5 s. Record any miss | n/a | n/a | n/a |
| M-HK-4 | Chord collision | Check the default toggle chord against System Settings > Keyboard > Keyboard Shortcuts > Input Sources | Record any collision (R9) | n/a | n/a | n/a |
| M-HK-5 | Appshot both-Option | With Input Monitoring **not** granted, press both Option keys | Record whether it fires (settles R6) | n/a | n/a | n/a |
| M-AUTO-1 | Automation | With Music running (a window open), switch **Mute music while dictating** on (Settings > Overlay & taskbar) | Apple Automation dialog naming the app and Music, with the target-neutral usage string; Music is not launched by Jarvis; with no player running, no dialog | Deny: the toast "Personal Jarvis needs permission to control another app for this."; dictation continues without ducking | Pane "Automation" | Ducking works on the next dictation |
| M-AUTO-2 | Automation off | Keep the switch off | No Automation dialog ever, no player launched | n/a | n/a | n/a |
| M-AUTO-3 | Attribution | After Allow, dictate while music plays | The volume drops (a `-1743` after Allow means the grant is not attributed to the app that sends; record it, R5). If it does not drop, `jarvis permissions status --include-automation` names the player (one background `needs_settings` episode, never a toast); after the switch is fixed, the next dictation ducks | n/a | n/a | n/a |
| M-AUTO-4 | Terminal | Start a subscription sign-in that opens a Terminal window | Record whether macOS shows a separate "control Terminal" Automation dialog (outside the permission service) and what a denial looks like | n/a | n/a | n/a |
| M-FF-1 | Files and Folders | Ask Jarvis to list or save a file in Desktop, Documents or Downloads | Apple's own prompt on first access, with the short usage string; nothing pre-checks the folders | Deny: normal error handling, no toast | System Settings > Files & Folders | Immediate |
| M-KC-1 | Keychain | First read of a stored key after install or update | The Keychain prompt; after a declined dialog Jarvis keeps working from the local file (`jarvis permissions status` reports the storage kind) | n/a | `jarvis permissions request credential_store` replays the read | n/a |
| M-LOGIN-1 | Autostart | Reboot or log in again with autostart on | Record the "Background Items Added" notice and the name shown in Login Items (R7) | n/a | Login Items | n/a |
| M-AGENT-1 | An agent never answers a dialog | With a dialog on screen, start a computer-use task | Nothing is dispatched; the mission stops with `blocked_permission` | n/a | n/a | n/a |

### 7.4 Evidence ledger: what was verified, and how

**NOT verified on a physical Mac.** Nothing in this table is a substitute for section 7.3.

| Layer | What | How | Result recorded | Strength |
|---|---|---|---|---|
| Unit and contract tests on a framework-level simulator | The service's outcomes, episodes, cooldowns, events, the scenario table on macOS and off it; boot with every permission undecided; an upgrader with all grants; a non-macOS host never touches TCC; the consumers (audio capture, ducking, dictation insert, hotkey tap, screen capture and context, computer use, window control); the routes and snapshot v2; **since the UI reset:** the keybinds ask on save, the `jarvis permissions reset` command, the Info.plist and `.lproj` content, the frozen-app probe and the build script | `tests/fakes/fake_tcc.py` (a stateful TCC simulator: per-service states, scripted dialog policy, no re-ask after a decision, a frozen Screen Recording preflight until relaunch, an ordered call log of probes, requests and implicit prompts, a `tccutil` stand-in), `tests/fakes/fake_permission_service.py`, and the test files named in 4.5, 4.12 and 4.16 | **2026-10-02, Linux sandbox, Python 3.11, no GPU, no audio hardware, real bash 3.2 first on `PATH`:** `python -m pytest -p no:cacheprovider tests/unit/cli_ctl tests/unit/ci tests/unit/packaging tests/unit/setup tests/unit/trigger tests/contract/test_hotkey_backend_protocol.py tests/contract/test_permission_service_contract.py tests/unit/platform tests/unit/core/test_permission_events.py tests/unit/ui/web/test_keybinds_input_monitoring_ask.py tests/unit/ui/test_keybinds_route.py tests/unit/ui/web/test_wake_activation_permission.py tests/unit/ui/web/test_permissions_routes.py tests/unit/ui/web/test_permissions_snapshot.py tests/unit/ui/web/test_dictation_start_failures_parity.py tests/unit/scripts` gave **2846 passed, 25 skipped, 1 failed** (237 s). The one failure, `tests/unit/setup/test_macos_app_bundle.py::test_a_changed_interpreter_still_earns_a_fresh_rebuild`, asserts that the running interpreter is not Python 3.11 and fails the same way on the untouched branch (a sandbox-only failure). A second run of `tests/unit/ui/web` together with the keybinds, mic-level, voice-permission, ducking and dictation-insert tests (1012 tests) gave 1009 passed and 3 failed (a skills-route name, a society-figure asset path, and a timing-sensitive local-models overlap test that passes alone); the first two fail identically on a clean checkout of the base commit, none touches permissions. The 25 skips are platform or optional-dependency skips | The fake models what the code believes about macOS, not macOS itself. It proves the logic, not the OS behaviour |
| Frontend tests | The toast planner and its event parity, the toast layer's action, the i18n parity of `permissions.toast.*`, the production build, the light, dark and terminal-pane inspection | Vitest, `npm run build`, a browser harness | **Not recorded on this page.** They belong to the frontend package of the UI reset and are reported there; a Linux browser is not the macOS web view, so the real look stays a checklist row (M-UI-1) | Logic and a browser look only; no real web view |
| Linux gates | Docs privacy scan, public-docs (manifest, links, front matter, word budget), generated CLI references, language gate, silent-exception and dead-config gates, bash 3.2 parse, `actionlint` on the touched workflow, the frozen-app probe against a synthesized bundle, the build script's `DRY_RUN` | `scripts/ci/run_gates.py --base origin/main --pr` with the real bash 3.2 first on `PATH`, `actionlint .github/workflows/macos-desktop.yml`, `scripts/ci/check_frozen_macos_app.py`'s `check_app` on a bundle laid out with `packaging/macos/add_localizations.py`, `DRY_RUN=1 packaging/macos/build.sh` | **2026-10-02:** every gate passed (private keys, dist consistency, brand logos, mirrors, CLI coverage, danger metadata, install methods, dead config switches, silent exceptions, async routes, WebGL, plugin auth contract, reference docs, docs privacy, public docs, bash 3.2 parse of all 9 shell scripts, privacy, no new German, history); `actionlint` printed nothing; the probe reported both missing `.lproj` files on the bare bundle, passed after the build step ran, and flagged a tampered Spanish file; the dry run prints `add_localizations.py` before the first `codesign`. The privacy and language gates compare committed history, so the working-tree additions were also checked with the gate's own functions (no violations; without the allowlist entry for the string table it reports 10) | Static and portable-code checks |
| macOS CI lane | Imports of the native frameworks; a tripwire proof that nothing asks outside an installed bundle; symbol binding; a service smoke that never asks; the bundle self-probe; a curated test list | `.github/workflows/macos-desktop.yml` (steps in 4.13); its scripts also run against FakeTCC in `tests/unit/ci/test_macos_desktop_permission_step.py` | **No runner result is recorded for this branch.** The lane steps exist and their scripts pass against the simulator on Linux; the lane itself has not been seen to run green on a macOS runner with the new steps | Runner only; informational shards once it runs |
| Installer workflow dispatch | The `.dmg` app builds with the new spec on Apple Silicon and Intel, `scripts/ci/check_frozen_macos_app.py` passes, the frozen app boots twice and reads its permission status: microphone "granted" under `ai.personaljarvis.desktop`, so AVFoundation loads inside the app | Desktop installers workflow, run 36923225371 at commit `43eeb7aed`, 2026-10-01, no release published (as recorded in commit `ed489bece`) | Passed on both architectures, **ad-hoc signed**, no hardened runtime, no notarization. It predates the just-in-time rebuild, so it says nothing about the new service | Runner evidence. It proves packaging, not prompts, not Gatekeeper |
| Runner spike | Carbon hot-key crash surface | `.github/workflows/macos-hotkey-spike.yml` (dispatch only, 4.15) | Run `36954304202` at commit `59749f859`: harness green on `macos-15` (arm64) and `macos-15-intel`; the arm64 report's verdict is `usable=False` (a preflight read granted in the test process), so it is not TCC evidence; variant G and the off-main register are not covered | Runner only |

What none of the above shows: any real dialog, wording of any dialog, the behaviour of a grant in
a running process, hold-key semantics, the Developer ID or notarized build, macOS 26 and 27
behaviour, a user's Intel Mac, light and dark appearance of the toast in the real web view, that its
button opens the right pane, that macOS shows the German and Spanish usage strings or accepts the
UTF-8 `.strings` files, the real `tccutil` behaviour of each service name, or the double dialog in the
embedded WebView. Each is a row in 7.2 and 7.3 awaiting a sign-off.
