# macOS permissions: ask when a feature needs them

Status: implemented, **not yet exercised on a physical Mac** (section 7 is the checklist that
closes that gap). Last reviewed: 2026-10-02, reconciled with the finished code on that date
(AP-35 is in `AGENTS.md`, ADR-0037 and BUG-225; the legacy permission wall is deleted). Scope:
Personal Jarvis on macOS (the downloadable `.dmg` app and the managed source-install app).

## 1. Summary

1. **Wrong:** features refused to run until our own permission check passed, so macOS was never asked from a feature path.
2. **Why:** the plan said degrade, never hard-block (AD-13); the code inverted it on 2026-07-15 with no recorded decision (2).
3. **Real, kept:** stable signed identity, live state reads, usage strings, entitlements, silent-failure checks (wallpaper, all-zero audio).
4. **Now:** a feature asks at first use, macOS shows its own dialog, a denial degrades that feature with one click to the pane (4).
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

## 3. How other apps do it, and what Apple says

### 3.1 Evidence quality (read first)

- Vendor domains (OpenAI help centre and developer pages, Raycast manual, Zoom, Slack,
  1Password, Loom, CleanShot, Alfred, Wispr Flow, Superwhisper) were **not reachable** from the
  research environment. Most vendor statements below come from **search-result summaries of the
  official page**, university or IT how-to pages that mirror the vendor knowledge base, and
  third-party reviews. They are weaker than a direct read.
- Pages read directly: the Claude desktop and computer-use documentation, one Claude support
  article, and GitHub issues of public repositories (these quote real dialog text and bundle ids
  but are user reports, not vendor statements).
- Nothing was observed on a Mac. A "no wall found" statement means "none documented", never
  "none exists".
- Source types: **direct** (page read), **summary** (search summary of the official page),
  **how-to** (third-party or institutional write-up), **issue** (GitHub issue).

### 3.2 Comparison table

| App | When it asks | Explanation before / after | On denial | Route to System Settings | Confidence, source type |
|---|---|---|---|---|---|
| **ChatGPT desktop** | At first use of a feature: Work with Apps (Accessibility), Record and voice (Microphone, Screen & System Audio Recording), Computer Use after an explicit opt-in. No launch wall found. | Help-centre prose plus the OS dialog; an own pre-prompt card was not verified. Computer Use adds app-level approvals independent of TCC ("allow once / Always allow"). | Feature unavailable; Computer Use reports "permissions are still pending" when the grant sits on the wrong client (a helper is a separate TCC client, openai/codex#46776). | Text path to the Privacy & Security pane; a deep link is unverified. | Partly documented. Summary of vendor help pages plus issues (read). |
| **Claude desktop** | Computer use is off by default; the user flips a Settings toggle and must grant two permissions "before the toggle takes effect". Quick Entry shows an in-app opt-in card at first launch of the updated app (an app-level consent card, not a TCC prompt). | Docs table with one line per permission and a status badge per permission in Settings. | Toggle stays inert until both are granted; if off, Claude says in chat it could do the task if enabled. | "Click the badge to open the relevant System Settings pane" (documented). Restart after a grant: not stated for Desktop. | Documented for computer use (direct). Partly documented for the timing of the OS dialogs and for Quick Entry denial. |
| **Claude Code** (terminal) | Nothing up front. Computer use is a built-in server, off by default; "the first time Claude tries to use your computer" a prompt appears. Protected folders prompt on first touch (plain OS template). | Inline card in the session with links to the panes and a **Try again** button. | Try again, no nagging. Folder denial surfaces as an unexplained error (issues). | Links in the prompt. "macOS may require you to restart Claude Code after granting Screen Recording." | Documented for the flow (direct). Attribution is mixed: the docs name the terminal app; issues show the Claude binary as its own client (a version path in Automation dialogs, per-update re-prompts). |
| **Raycast** | Per feature at first use (summary of the manual); feature setup views for Screen Awareness and Dictation. Whether Accessibility is also asked in first-run onboarding is **conflicting** across third-party sources. | Setup view per feature and an inline "Grant Permission" card. | Feature degrades; Screen Recording is optional for Screen Awareness. | Button opens the pane; a changelog entry fixes "would not open System Settings when requesting access for a denied permission" (summary). Auto-restart and resume is asserted by one summary only. | Partly documented. Summary of the manual and changelog; third-party how-tos. |
| **Zoom** | Camera and microphone at first use; Screen Recording at the first share; Accessibility only when someone requests remote control. No permission onboarding documented. | OS dialog with the usage string; for Accessibility "Open System Preferences" on the dialog, which as far as can be told is the standard macOS dialog, not an own one. | Feature dead; all how-tos tell the user to fix it manually in Privacy & Security. | Button for Accessibility; OS asks to restart Zoom after Screen Recording. | Partly documented. How-tos mirroring the vendor KB. |
| **Slack** | First huddle (microphone), first video or share (camera, Screen Recording). | OS dialog (inferred). | **Silent degrade**: sharing shows the wallpaper and menu bar. A cautionary example. | Help text, quit and reopen; `tccutil reset ScreenCapture <bundle id>` as a last resort (third party). | Partly documented / inferred. Third-party sources. |
| **1Password** | Screen Recording at the first QR or setup-code scan; Accessibility only for the opt-in Universal Autofill and shortcuts. The vault works with no permission at all. | OS dialog; the help page is named after the dialog text ("1Password would like to record this screen"). | QR scan unavailable until granted and restarted; core product unaffected. | "Open System Settings" on the dialog, then "Quit & Reopen". | Partly documented. Summary of vendor pages. |
| **Loom** | The install article says users "will be prompted" when installing the desktop app; microphone and camera when selected (inferred). Exact order not documented. | Unknown. | Manual toggles; `tccutil reset` commands documented as a fix for stuck state (a known pain point). | Help-article steps; no in-app deep link confirmed. | Partly documented. Summary of help articles. |
| **CleanShot X** | Screen Recording and Accessibility during setup (a screenshot tool whose whole purpose needs Screen Recording, so up front equals first use); microphone when a recording uses it. | Short onboarding; wording unknown. | Capture does not work; the app appears in the Screen Recording list only after a capture attempt. | Quit and reopen needed (third party). | Inferred / partly documented. Third-party reviews. |

### 3.3 Closest comparators (voice, hotkey, insert text)

| App | Finding | Confidence |
|---|---|---|
| **Wispr Flow** | First-launch setup with two cards (Accessibility, Microphone), each with an own priming sentence and an Allow button, then Continue and a microphone test; resumable. Input Monitoring only if the user binds Caps Lock alone; screen capture not needed for dictation; re-checks permissions when brought to the foreground (community guide); warns that a missing Accessibility grant is a silent failure. | Documented (summary of vendor docs); runtime recovery behaviour from a community guide. |
| **Superwhisper** | First-launch wizard for Microphone and Accessibility; a changelog note says a re-prompt after completed onboarding was fixed. | Partly documented. |
| **Alfred** | Hybrid: a first-launch checklist plus a permanent "Request Permissions..." button in Preferences; Contacts and Automation are asked by macOS only when needed. | Partly documented. |
| **Codex desktop** | Opt-in toggles (Computer Use, Chronicle) then the OS prompts, then per-app approvals; helper identities are separate TCC clients (issues #46776, #18507); a prompt for a feature the user never enabled is perceived as a bug (#37378, "access data from other apps" about daily with Computer Use off). | Partly documented; issues read. |

### 3.4 What the comparison supports, and what it does not

- Documented only for the Claude products (direct read): the OS dialog hangs off a feature
  toggle or first use, with a feature-local explanation and a link to the right pane. For the
  other apps the evidence is summary, how-to or inference. Wispr Flow, Superwhisper, Alfred,
  CleanShot X and (inferred) Loom run short first-launch permission flows, which is a setup
  wizard of the kind Jarvis removes. No source found documents a persistent app-wide banner or a
  "set up everything" wizard, but absence of documentation is not absence of the feature.
  This comparison is therefore not evidence for the "no wizard" decision; that decision rests on
  the Apple HIG quotes (3.5).
- Not supported: "no app asks at launch". Claude desktop shows a first-launch opt-in card;
  Wispr Flow, Superwhisper, CleanShot X and Alfred have short first-launch flows scoped to what
  the product cannot work without; Raycast onboarding is uncertain. State it as "none documented
  as a persistent wall".
- Two-layer consent for agent features (an app-level toggle, then per-app approvals, then the
  OS dialog) appears in the Claude and Codex products. Jarvis already has its own risk tiers
  for tool use; this change adds no new app-level approval layer.
- Helper-identity pitfall (issues only): when a helper does the capture, the grant must be on the
  helper. Ask from, and check, the process that will call the API (4.7, attribution).

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

Remove the wall. The feature asks at the moment it first needs the permission, from a user
gesture; macOS shows its own dialog; denial degrades that one feature honestly with one click to
the right System Settings pane; nothing is asked at launch; nothing nags; the Settings page is
passive.

### 4.2 Principles (rule AP-35)

AP-35 is the rule name used in code comments and commit messages. It is recorded in the
`AGENTS.md` register (section 3), in `docs/adr/0037-macos-permissions-ask-when-needed.md` (the
same nine principles) and as the prevention rule of BUG-225 in `docs/BUGS.md`; this table is the
detailed statement.

| # | Principle |
|---|---|
| P1 | **Just in time.** Ask when a feature that needs the permission is first used or switched on by the user, never at launch, never in a banner, never as a wizard (HIG, 3.5). |
| P2 | **The feature may ask; it must never act on anything but a live GRANTED.** No path refuses because our own preflight says "not granted" before the OS was asked, and none acts on a permission the OS has not granted. `EnsureResult.granted` is true only for GRANTED and NOT_REQUIRED; PENDING, NEEDS_SETTINGS, DENIED and UNAVAILABLE never proceed; a native request's return value is never evidence of success. Silent-failure traps are checked at the source. |
| P3 | **Let the OS ask where it asks by itself; call the explicit prompt API where it does not.** Never ask by "touching" an API that fails silently. Never create an event tap to provoke a prompt (BUG-058 class): the ask is `CGRequestListenEventAccess` / `IOHIDRequestAccess`, and the tap is created only after the preflight is true. |
| P4 | **Denied is a stable state, not an error to retry.** Degrade the one feature, say why where the user is, offer one click to the right pane, stop. No repeated prompting, no polling banner. `tccutil reset` stays an explicit "Ask again". |
| P5 | **Grants are detected by silent probing owned by the backend** (an episode watcher), applied in process; a restart is only a hint, because the evidence conflicts (3.6). |
| P6 | **Identity decides who may reset and whether we may auto-ask, not whether we may act.** We act on whatever grant exists. Native requests are made only when running as an installed app bundle, or after an explicit confirmation that names the grantee (`outside_installed_app`). Reset only from the installed app's own bundle id. |
| P7 | **Opt-in features own their permission.** A feature the user has not switched on never touches its permission (wake word, mute music, computer use, global shortcuts). |
| P8 | **Honest degradation, always.** Every refusal carries a stable reason and a full English sentence; the UI renders one card with an action. |
| P9 | **An AI agent never answers a system dialog.** Agent-facing text is prohibitive; the computer-use engine pauses while a macOS consent window is frontmost. |

### 4.3 Layers

| Layer | Module | Role |
|---|---|---|
| Port (OS adapter) | `jarvis/platform/permissions.py`, `SystemPermissionPort` | Live, uncached, non-prompting `state` reads (shallow by default); `request_native` (returns `dialog_shown`, `no_dialog`, `timed_out` or `unavailable`, never evidence of a grant); `usage_string_present`; `outside_installed_app`; `open_pane`; `reset_row` (own bundle id only). It decides nothing about whether a feature may run. Keeps `ACCEPTED_BUNDLE_IDS`, `AUTOMATION_TARGETS`, `PANE_FAMILY`, `REQUEST_CLASS` and the module-loader seams for fakes, plus `remove_leftover_state_files` (4.13). |
| Service | `jarvis/platform/permission_service.py`, `PermissionService` | The just-in-time layer: `check`, `ensure`, `ensure_all`, `ensure_async`, `ensure_all_async`, `outstanding`, `add_listener`, `open_settings`, `report_failed_use`, `note_reset`, `note_app_activated`, `refresh_episodes`, `invalidate`, `check_deep`, `app_info`, `can_request`, `attach_bus`. No framework import at module scope, no I/O at import (AP-26); off macOS `ensure` returns NOT_REQUIRED before the port is asked anything. Resolves the port per call, so test stubs apply. |
| Protocol | `jarvis/core/protocols.py`, `PermissionGate` | `check`, `ensure`, `ensure_async`, `ensure_all`, `open_settings`. Consumers receive the gate through an injectable hook with a default resolver to the service singleton; `tests/fakes/fake_permission_service.py` injects there. `force_ask`, `report_failed_use`, `note_reset` and `add_listener` are service-only; consumers reach them by capability lookup. |
| Consumers | audio, speech pipeline, desktop app gates, computer use, screen capture (`jarvis/platform/screen_access.py`) and context, dictation insert, window control, ducking, hotkeys | Low-level helpers only `check()` and degrade silently; interactive `ensure` exists only at entry points that carry a user gesture (4.6). |
| Routes | `jarvis/ui/web/permissions_routes.py` | Passive snapshot v2 and single-row GETs; `POST /{id}/request`, `/open-settings`, `/reset` (4.9). CLI (`jarvis/cli_ctl/commands/permissions.py`): `jarvis permissions status`, `request` and `open-settings`; there is no CLI reset. |
| Events | `jarvis/core/events.py` | `PermissionNeeded`, `PermissionResolved` over the existing `/ws` wildcard forwarder (4.9). |
| Frontend | `jarvis/ui/web/frontend/src` (the frontend paths on this page are relative to it) | Pure reducer `lib/permissionPrompts.ts`, store `store/permissions.ts`, REST client `lib/permissionsApi.ts`, event twin `lib/permissionEvents.ts`, floating card `components/permissions/PermissionPromptLayer.tsx` (mounted by `PermissionPromptHost.tsx`), inline notes (`InlinePermissionNote.tsx`, `agentchat/DictationNote.tsx`, `ShortcutsStatusNote.tsx`, `ShortcutsTip.tsx`, `views/settings/MuteMusicPermissionNote.tsx`), passive `views/settings/PermissionsPanel.tsx` (4.10). |

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
   `background`-origin episode (inline status only, never the floating card).
5. `interactive=True` outside an installed app and without `allow_outside_app`: no native
   request; the result says `outside_installed_app` so the UI can offer "Allow for the app that
   started Jarvis" with an explicit confirmation.
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

`report_failed_use(permission, *, feature, target=None, trace_id=None)` is the one producer of
the `restart_hint` reason: a consumer calls it after a real capture or tap failure while the state
reads granted (or after the user returned from Settings and the preflight is still negative). It is
never an automatic restart. It only produces a `restart_hint` for Screen Recording and Input
Monitoring (`_RESTART_HINT_FAMILIES` in `jarvis/platform/permission_service.py`); any other
permission falls back to a plain non-interactive `ensure`. It has two callers, both exercised only
against fakes:

- Input Monitoring: `jarvis/trigger/backends/quartz.py` (`report_if_deaf`), once per tap when the
  tap runs, the grant is visible, no raw event arrived, Secure Event Input is off and key activity
  was seen. A raw event arriving later ends the episode again (`note_reset`).
- Screen Recording: `jarvis/platform/screen_access.py` (`_unusable_grant_refusal`, reached from
  `verify_frame_is_real`), when the state reads granted yet other apps' windows show no readable
  title. Only a user-started capture reports; a background capture stays quiet. A later frame whose
  window list shows a readable title ends the episode (`_clear_failed_use`, through `note_reset`).
  `jarvis/cu/capture.py` deliberately does not report for a native-capture timeout while the grant
  reads live, because nothing failed for good there.

Accessibility input and the ducking module have no such path. `jarvis/audio/ducking/macos.py` notes
that the service has no call for "a real attempt failed although the probe said granted" and maps a
`-1743` after a granted read to `needs_settings` instead.

`note_reset(permission)` drops a slot's restart hint and forgets the per-process ask cooldown; the
reset route calls it after a successful "Ask again".

### 4.5 The permission table: trigger, request, denial, route back, after a grant

Class: DIALOG, PROMPT-ONCE, NATIVE (3.6). EVENT_POSTING is an alias of ACCESSIBILITY for asking.

| Permission | Request kind | Trigger (a user gesture) | How we ask | If not granted | Route back | After a grant |
|---|---|---|---|---|---|---|
| **Microphone** | DIALOG | Dictation key or button; "speak in this conversation" (the UI calls `POST /api/permissions/microphone/request` with feature `browser_voice` before `getUserMedia`); push-to-talk; switching the **wake word** on (the route asks with `wait_s=0`); the wake self-test button (`wait_s=60`, the test is the JIT moment) | `request_native` (`AVCaptureDevice requestAccess...`) when `not_determined`, then the stream opens | Voice and dictation refuse (`DictationRefused("microphone_unavailable")` plus `PermissionNeeded`); typed chat unaffected; zeros are never recorded silently | Card and Privacy row: System Settings > Privacy & Security > Microphone | Usable at once [I; Apple does not document a restart requirement]. The wake loop that was parked wakes on `PermissionResolved`. A HOLD key pressed during the dialog is **not** started retroactively ("allowed, press again"). |
| **Screen Recording** | PROMPT-ONCE | First capture started by a user goal: computer-use perception, the `screen_snapshot` tool body, a user-requested screen-context or Appshot capture | `CGRequestScreenCaptureAccess()` (once per process; an explicit Allow always) | Capture refuses with an honest error, never a wallpaper frame; `PermissionNeeded(needs_settings)` | Pane "Screen & System Audio Recording" (static label; the pane naming per macOS version is [C], unverified; "Screen Recording" before macOS 15). On macOS 15 and later the first capture may also show the OS "bypass the system picker" alert. A stuck entry may need a reboot [D 818415]. | The window-title oracle (`_screen_capture_live_check`) decides. A capture timeout while the state reads granted is PENDING ("macOS may be asking you to confirm"), not DENIED. A restart is offered only after a real failed attempt. |
| **Accessibility** and post-event | PROMPT-ONCE | First time Jarvis must type, click or focus for the user: computer-use input, dictation auto-paste, window focus and maximize. AX **reads** (tree, element at point) are background: never prompt, degrade | `AXIsProcessTrustedWithOptions(prompt)`, at most once per 10 minutes per process; an explicit Allow always | Dictation falls back to `clipboard_only`; computer-use tools return `[permission_needed:accessibility] ...`; window tools return the same prefix | Pane "Accessibility" (reported renamed on macOS 27, 3.7; the label is static text, no OS sniffing) | A silent watcher; taps and observers are rebuilt on a false-to-true edge. The first paste after an in-process grant reports `paste_sent` until delivery is observed. |
| **Input Monitoring** | PROMPT-ONCE | The user enables or saves global shortcuts: the "Enable global shortcuts" action in the Shortcuts surfaces, saving a shortcut, and one dismissible tip after the first UI-started dictation. Never at boot | `CGRequestListenEventAccess()` / `IOHIDRequestAccess` only; the event tap is created only after the preflight is true | The shortcut status reads `needs_input_monitoring`; the app keeps working from button and voice; the Esc-to-cancel pill never claims a dead key | Pane "Input Monitoring"; copy never says "you denied" (the entry is reportedly auto-registered after a request: BUG-083 live finding on one Mac, [C], unverified) | The watcher re-arms the hotkey in process (a listener sets the existing reload path). "No raw events arrived" is a restart hint, never an automatic restart. |
| **Automation** (Music and Spotify only) | DIALOG | Switching "Mute music while dictating" on **while a player runs**; the Privacy-row Allow. Never mid-dictation, never by launching a player | The killable `osascript` consent runner (120 s, own daemon thread) | Ducking skips that player; the inline status names the player | Pane "Automation" | Checked per send. `-1743` after GRANTED maps to NEEDS_SETTINGS. |
| **Files and Folders** | NATIVE | Whenever the user or an agent touches the folder | The OS prompts by itself; never pre-checked or enumerated "to check"; needs a GUI login session [D] | Normal `EPERM` handling | System Settings > Files & Folders | Immediate |
| **Keychain** | not TCC | Reading a stored key | The `security` vault, then the 0600 file fallback (existing) | File fallback | The Privacy row shows the storage kind; "Try again" replays the read | n/a |
| **Login item** | not TCC | Autostart (existing, default on) | A LaunchAgent; the OS shows its own notice [C], see R7 | n/a | Login Items | n/a |
| Camera, Contacts, Calendars, Notifications, Full Disk Access, Location | n/a | Not used | n/a | n/a | n/a | n/a |

Saving a shortcut from any keybind surface (`useKeybinds().saveKeybind`) asks for Input Monitoring
once, from that click, when the last status read says `needs_input_monitoring` and the window is
the embedded desktop one (a failed or rate-limited ask never fails the save). Not implemented
(verified in the tree): the onboarding wake/call-shortcut step does not show the status note or
ask, so a fresh Mac user who only picks the Call shortcut there meets the ask on their first save in
Settings, on the status note's action, or on the one-time tip after the first UI-started dictation.

### 4.6 Per-consumer behaviour (execution context is part of the contract)

Rule: `ensure(wait_s>0)` only from a worker thread or via `ensure_async`; a loop, Tk or tap
caller uses `check()` or `wait_s=0`.

| Site | Context | Behaviour |
|---|---|---|
| `jarvis/audio/capture.py`, `MicrophoneCapture` | async | One `ensure_async` at open (`permission_feature`, `interactive`). No per-frame or 0.25 s probes. The watchdog's existing 1 s tick calls `check(MICROPHONE)`; a revoke raises `MicrophoneAccessError` (carries the `EnsureResult`). **Digital-silence guard:** 5 s of exact zeros while the OS says granted triggers one re-check and one `PermissionNeeded` ("access is probably denied or muted"); unverified on a Mac. Restarts are non-interactive. |
| `jarvis/ui/desktop_app.py` gates, `jarvis/speech/pipeline.py` | loop | Two silent predicates: background capture (wake word, barge-in) needs a live GRANTED; a user gesture (push-to-talk, a voice session, dictation) needs "not denied", because the gesture itself asks. The wake loop parks on a reload event set by `PermissionResolved(microphone)`, never a 2 s churn. First press: `ensure("microphone", wait_s=0)`. |
| `jarvis/ui/web/settings_routes.py` | route | Wake switch on: blocking `ensure(wait_s=0)` from the sync handler (a worker thread); the response carries `permission:{outcome, reason, can_open_settings}`. Wake self-test: `ensure_async(wait_s=60)`. GET mic-level uses `check` only. Mute-music PUT asks only when switching on with a player running. |
| `jarvis/cu/actuate/base.py` | tool body, worker | `ensure_all([ACCESSIBILITY], wait_s=0)` after the executor authorised the call; non-granted raises `PermissionNeededError`, whose text starts `[permission_needed:accessibility] `. `ToolResult` is unchanged. |
| `jarvis/cu/engine.py` `_dispatch_tool` | worker | Keeps a **silent** Screen Recording gate before every engine action (the AP-3 choke point and the blind-input guard), never interactive there; maps the `[permission_needed:` prefix to a terminal `blocked_permission` mission state with a user Retry, not a model retry loop. **System-consent guard:** while a macOS consent window is frontmost (`jarvis/cu/system_dialogs.py`, an owner-name list that is itself unverified) nothing is dispatched. |
| `jarvis/cu/capture.py`, `jarvis/platform/screen_access.py`, `screen_snapshot`, screen context (`jarvis/screen_context/service.py`) | mixed | Helpers `check()` and degrade; gesture entries (`require_screen_recording[_async]`, `wait_s=0`) ask. A screen-context capture is always a user gesture, so its silent probe is followed by one `require_screen_recording_async`; the status route only runs the silent probe. `verify_frame_is_real` runs a pixel sanity check after the grab while the state claims GRANTED (other apps' windows on screen with no readable title refuses with honest text, flat frame or not) and reports the failed use (4.4). GET status routes never prompt. |
| `jarvis/vision/ax_tree.py`, `element_at_point.py` | worker | AX reads: `check()` and degrade, never a card. |
| `jarvis/platform/window_state.py` | worker | `ensure(ACCESSIBILITY, feature="window_control", wait_s=0)`; refusal text starts with the `[permission_needed:accessibility]` prefix. |
| `jarvis/dictation/insert.py` | worker | First paste: `ensure(ACCESSIBILITY, feature="dictation_insert", wait_s=0)`; `clipboard_only` is a success state (the text is one paste away); `paste_sent` is the honest middle for the first paste after an in-process grant; never sends Return while a dialog could be frontmost. |
| `jarvis/audio/ducking/macos.py` | worker | Switching on is the only asking path (`prewarm`, off every lock); `mute_others` is non-interactive: it skips and opens a `background` episode. The controller lock is never held across an ask. |
| `jarvis/trigger/hotkey.py`, `trigger/backends/quartz.py` | loop, tap thread | Requirement is Input Monitoring only. Boot start is silent. `HotkeyTrigger` registers a grant listener that re-arms the backend off the loop (`asyncio.to_thread`). The tap callback never calls `ensure` and takes no service lock; it reads a cached verdict. A raw-callback counter feeds `deaf_tap_suspected`, and `report_if_deaf` (worker thread) turns that into one `report_failed_use` per tap (4.4). |
| `jarvis/trigger/shortcuts_status.py` | route | One status for the global shortcut tap: `ready`, `needs_input_monitoring`, `unavailable_in_this_mode`, served in `GET /api/settings/keybinds` as `shortcuts_status`. |

### 4.7 Boot rule

With every permission `not_determined` (including `wake_word_enabled=True`), launch makes **no
OS dialog, no input-stream open, no event-tap creation and no capture call**; an upgrader with
every grant present sees zero cards and zero requests. The service singleton is lazy; boot order:
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
  per episode, except that an explicit Allow click (`force_ask`) may repeat a PROMPT-ONCE request
  (Accessibility, Input Monitoring, Screen Recording); a DIALOG-class dialog is never asked twice. An episode ends when every permission it needs is granted
  (`PermissionResolved(granted=True)`) or after ten minutes untouched (`granted=False`).
- **Watcher:** an asyncio task (or one daemon thread without a loop) polls open permissions every
  2 s, publishes the edges, calls listeners (cheap, never raising, run on the loop thread when one
  is attached) and stops when nothing is open.
- **Phases and origins:** `phase="os_dialog"` (macOS is asking: no card) turns `blocked` when the
  user has to act. For PROMPT-ONCE permissions that is after an app refocus or about 15 s still
  ungranted; for DIALOG permissions when the dialog was open 130 s or the state turned denied
  (our own figures). Only `origin="user"` may open the floating card; `origin="background"` fills
  inline rows and status. Events are published on a state change, never from repeated silent
  checks.
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
  and never carrying exception text, paths or window titles (AP-34 spirit).
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
own. `GET /api/permissions/{id}` returns one cheap row. Both GET routes accept `?activated=1`, which
the frontend sends on the first refetch after the window regained focus: the route calls
`note_app_activated()` so a PROMPT-ONCE dialog the person left to flip a switch is promoted to
`blocked` at once. It is a hint about what the user just did, never a prompt (only the UI's `activated=1` counts). The frontend seeds
from `needed[]` because events are not persistent.

`restart_hint` on a row is true while a `restart_hint` episode is open for that permission, **even
when the state reads granted** (that is the case it is reported for: granted, yet a real use
failed); the row's `detail` is then the fixed restart sentence. The Keychain row has no request
call: its `can_request` means "Try again", which replays the Keychain read.

`POST /{id}/request` hands the permission to `ensure(interactive=True, wait_s=0)` and answers at
once. The body may carry `allow_outside_app` (refused with 403 for an agent: confirming a grantee is for
a person at the UI), `feature` and `target`. The person at the Jarvis window is identified
positively: its session cookie, or (open local access has no cookie) the browser-set
`Sec-Fetch-Site: same-origin` header on a request with no `Authorization` header. Every other
caller is an agent: a script with the control key and equally a local process that presents no
credential at all. A request from the UI passes `force_ask=True` to the service (an explicit click
on "Allow" skips the PROMPT-ONCE cooldown); an agent's does not, so a script cannot loop it. For an
agent `/reset` is refused with 403 (a reset forgets the cooldown, so "reset, then request" would
be a prompt loop) and `?activated=1` is ignored. The UI and every other caller have separate
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

### 4.10 Frontend

- **Removed:** the app-wide banner and its dismissal store, the wizard (`setupAll`, ordering,
  auto-restart, polling), the refresh-event plumbing, the onboarding `permissions` step (a stored
  legacy step id maps to `voice`).
- **Floating card** (`PermissionPromptLayer`, mounted lazily by `PermissionPromptHost`): owner
  window only, not on a headless or remote browser (host-only actions), a portal at `z-[115]`
  (above the setup spotlight, below the caption bar). Opens only for `origin="user" &&
  phase="blocked"`. Copy only from i18n (`permissions.prompt.<feature>.<reason>`) as full
  sentences. Buttons (en.json `permissions.prompt.action.*`), chosen per reason by
  `components/permissions/promptActions.ts`: "Continue" (before macOS asks), "Allow for the app that
  started Personal Jarvis" (replaces Continue when running outside the installed app), "Open System
  Settings", "Check again", "Not now" (episode-scoped, memory only), "Quit and reopen" (only for
  `restart_hint`), and "Already on? Reset and ask again" (only after the person returned from
  Settings, the permission still reads off and the backend says `can_reset`). Restricted and
  unavailable episodes get an explanation and "Not now" only. No autofocus, `role="group"`, a polite
  live region, no global Escape handler, theme tokens only. No interval polling: a single-flight,
  jittered refetch on window return (`lib/focusRefresh.ts` through `lib/connectBudget.ts`, AP-33);
  the backend watcher pushes `PermissionResolved`.
- **Inline surfaces** through a ref-counted `useInlinePermission(feature)` registry the card
  consults to avoid duplicates: dictation note, wake-word panel, mute-music row, Shortcuts status
  note and tip, browser voice (host microphone asked before `getUserMedia`), sidebar voice status.
  The native orb keeps handling only `DictationRefused`.
- **Settings > Privacy** (nav label "Privacy", section id `permissions` kept;
  `views/settings/PermissionsPanel.tsx`): passive rows with the textual pane path, a status pill
  ("Granted", "Off or not asked", "Denied", "Restricted", "Unavailable", "Not required", "Restart
  needed"), "Allow" when `can_request` ("Try again" on the Keychain row), "Open System Settings"
  when `can_open_settings`, "Ask again" when `can_reset`, a header sentence "{app} asks only when a
  feature needs it. This page shows which permissions macOS allows right now and where to change
  them."; refreshes on mount, on focus and after an action; hidden on non-macOS. A row that reads
  granted but carries `restart_hint` (a real failed use, 4.9) shows the "Restart needed" pill and
  "Quit and reopen" there too; the host-only buttons appear only in the embedded desktop window,
  a remote browser sees the rows read-only.
- Definition of done for the frontend: production build, light and dark appearance and the
  terminal-pane appearance inspected (AGENTS.md), vitest and locale parity green, no restart ever
  asked of the user. Build and appearance results are not recorded on this page (7.4).

### 4.11 What stays

Stable signed identity (BUG-060, 217, 223); live uncached state reads; the own-bundle `tccutil
reset` from the installed app (and the one reset the managed installer runs when a rebuild changed
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
  frozen-app probe assert it on the built bundle.
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
`identity_reset`, the global `restart_required` and `foreground`. Backend: the Automation consent
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
| Settings nav | Privacy section hidden | frontend tests |
| Consumers | No gating, no request (FakeTCC call log empty) | consumer tests |
| Hotkey factory | Unchanged | `tests/contract/test_hotkey_backend_protocol.py` |
| Windows microphone-privacy analogue, all-zero audio detection off macOS | Out of scope (A9) | n/a |

### 4.15 Global hotkeys: stage S0 now, a Carbon backend later

**Facts [code]:** on macOS global shortcuts use `QuartzHotkeyBackend`, a listen-only event tap
(`jarvis/trigger/backends/quartz.py`); pynput is the Linux X11 backend. Before this change the
tap required both Accessibility and Input Monitoring, which is stricter than Apple's rule
(listen-only needs Input Monitoring only [A, WWDC19 session 701]).

**Stage S0 (this change):** Input Monitoring only; the tap is not created at boot unless already
granted; re-arm on grant through the service listener (no second path); the one `shortcuts_status`;
the asking moments of 4.5; stale texts fixed; a raw-callback counter replaces the dead
`received_any_event()` liveness signal. A restart hint appears only when the preflight is true and
no raw event arrived after the user typed (an explicit "still not working" report would need its
own route and UI and does not exist); never an automatic restart. The Appshot both-Option gesture is **not** an Input Monitoring row: its
permission need is unverified (`jarvis/appshot/gesture.py` records that it is unverified whether the
`CGEventSourceKeyState` Option-key read needs Input Monitoring; `jarvis/trigger/hotkey.py` notes that
the sibling `CGEventSourceFlagsState` read needs no event tap and no Accessibility grant, and says
nothing about Input Monitoring; neither read was measured on a Mac); the Appshot page
carries an inline note and the shortcut status note only.

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

## 5. Assumptions

| # | Assumption |
|---|---|
| A1 | Autostart stays default-on. |
| A2 | The Carbon backend and macOS-specific Call/Hangup defaults are deferred; only the spike ships (one runner run, unusable as TCC evidence; see 4.15). |
| A3 | The camera and speech strings, and the camera entitlement, are removed (no caller). |
| A4 | No strings for services only a child process could reach (Contacts, Calendars, Photos, Location, Bluetooth); unsupported until an agent feature needs one. |
| A5 | The WKWebView media-capture delegate (the WebKit decision that sits on top of the TCC dialog) is an open item; the service is called from the gesture before `getUserMedia`. |
| A6 | The macOS 27 Accessibility label is static text; no OS sniffing. |
| A7 | The Appshot both-Option permission need is unverified. |
| A8 | No telemetry. The field signal is the support-visible `PermissionNeeded` detail in logs. |
| A9 | A Windows microphone-privacy analogue and cross-OS all-zero audio detection are out of scope; the digital-silence guard is macOS-only. |
| A10 | There is no persisted "asked" memory; the once-per-process and cooldown rules are in memory. |
| A11 | The PROMPT-ONCE shape (the dialog only offers "Open System Settings") is community-observed, not Apple-documented. |
| A12 | The timing figures (15 s "blocked" after a PROMPT-ONCE request, 130 s DIALOG ceiling, 10 s oracle cadence, 120 s and 600 s cooldowns, the 5 s zero-audio window) are our own choices, not Apple's. |
| A13 | The list of system consent window owner names in `jarvis/cu/system_dialogs.py` is unverified; a miss leaves the other layers (prohibitive agent text, the grant itself) in force. |
| A14 | An `osascript` child is attributed to the app for Automation (the ducking sender); unverified. The ducker maps `-1743` after a GRANTED read to NEEDS_SETTINGS. |
| A15 | `tccutil reset` for the system-wide services (`ScreenCapture`, `Accessibility`, `ListenEvent`, `PostEvent`) may need elevated rights or may toggle rather than delete [D thread 788454]; in-app "Ask again" for them is unverified. The code verifies the state after a reset. |
| A16 | Keeping `NSScreenCaptureUsageDescription` is harmless; whether macOS uses it is unverified in both directions. |
| A17 | Screen Recording consent re-confirmation on macOS 15 and later is a recurring normal state, not an error (3.7). |

## 6. Open points and risks

Everything below is **unverified on a physical Mac**. "Closes with" refers to rows in section 7.

| # | Risk or open point | Why it matters | What the code does today | Closes with |
|---|---|---|---|---|
| R1 | **WKWebView `getUserMedia` double dialog.** In the embedded window, `getUserMedia` reaches the same microphone permission; WebKit's own media-capture decision (`WKUIDelegate`) is additional. No handler exists in `jarvis/`. | A user may see two prompts, or WebKit may deny after TCC allowed. | The UI calls the permission request from the gesture before `getUserMedia` and maps `NotAllowedError` to the same episode. The delegate grant is a tracked open item (A5). | M-MIC-3 |
| R2 | **macOS 27 pane naming.** The Accessibility pane is reported renamed; whether the `Privacy_Accessibility` anchor still resolves is unknown. `NSWorkspace.openURL` returns success even when the wrong pane opens. | The "Open System Settings" button could land on the wrong page. | Static label plus the textual path next to the button; the app quits a running System Settings before opening a pane, unless this process last opened the same pane (an approximation: the user may have navigated since). | M-AX-4 |
| R3 | **Screen Recording restart conflict.** Reports conflict on whether a running process sees the grant. | Wrong either way: a forced restart annoys, no restart leaves a dead feature. | Re-probe (shallow preflight, then the window-title oracle), try a real capture, offer "Quit and reopen" only after a real failure. | M-SR-4 |
| R4 | **Accessibility re-prompt and attribution.** The prompt call may show again while untrusted [C]; a Ventura toggle bug returned wrong values [C]. | Nagging or a false "granted". | Rate-limited to once per 10 minutes per process; the paste after an in-process grant reports `paste_sent`. | M-AX-2, M-AX-3 |
| R5 | **Attribution of child processes.** DTS says the algorithm is undocumented; helper binaries have been separate TCC clients in other products. | The ducker's `osascript`, `screencapture`, ffmpeg or agent CLIs may need their own grant, or may be attributed to Terminal in a dev run. | Expected, not guaranteed. No in-app text about attribution exists yet; a line on the Privacy page saying a grant applies to Jarvis and the tools it starts only as far as macOS attributes them is a follow-up. | M-DEV-1, M-AUTO-3 |
| R6 | **Appshot both-Option need.** Unverified whether the `CGEventSourceKeyState` Option-key read needs Input Monitoring (`jarvis/appshot/gesture.py`); `jarvis/trigger/hotkey.py` notes that the sibling `CGEventSourceFlagsState` read needs no event tap and no Accessibility grant, and says nothing about Input Monitoring; neither read was measured on a Mac. | It is the default Appshot shortcut and has no gesture that "enables" it. | Not an Input Monitoring row; an inline note and the shortcut status note only. The runner spike can read it, but runners cannot prove the permission outcome for a real user. | M-HK-5 |
| R7 | **Autostart notice.** The LaunchAgent runs `/usr/bin/open` without `AssociatedBundleIdentifiers`, so Login Items shows the program or organisation name, and macOS posts its own "Background Items Added" notice [A for attribution, C for the notice]. | Looks like an unexplained permission prompt. | Autostart stays default-on (A1); documented as a product decision. | M-LOGIN-1 |
| R8 | **Hardened-runtime entitlements never ran on a notarized build.** | A missing `audio-input` entitlement silently suppresses the microphone prompt [C, D]. | The probe asserts `codesign -d --entitlements :-` on a signed build; every runner build so far was ad-hoc signed. | M-SIGN-1 |
| R9 | **Keyboard-layout and input-source collision.** The default dictation toggle chord equals a macOS input-source shortcut when several input sources exist [I]. | A dead shortcut that is not a permission problem. | Not changed. | M-HK-4 |
| R10 | **Intel versus Apple Silicon.** Runners cover both; grants and prompts were never compared on hardware. | Architecture-specific frozen-app defects. | Runner smoke on both. | M-ARCH-1 |
| R11 | **Screen Recording first-capture double dialog and the monthly alert** on macOS 15 and later; an app cannot suppress it. | A first capture may show two dialogs; a capture timeout while an alert is up is not a denial. | A timeout while the state reads granted is PENDING; no periodic background capture without opt-in (P7). | M-SR-2, M-SR-5 |
| R12 | **Stranded grant after a re-sign** (checkmark on, kernel refuses). | Settings shows the switch on and the feature does not work. | "Already on? Reset and ask again" in the card; the state is verified after the reset, otherwise the manual path (remove with minus, re-add, reboot hint) is shown. | M-SR-6 |
| R13 | **Files and Folders in a headless session.** The prompt appears only in a GUI login session [D]. | A launchd-started backend is silently denied. | Normal `EPERM` handling; nothing pre-checks or enumerates those folders. | M-FF-1 |
| R14 | **Digital-silence guard threshold.** 5 s of exact zeros while granted is our own figure. | Could mis-report a deliberately muted source, or miss a quiet one. | One report, stays open, the stream is not torn down. | M-MIC-5 |

Follow-ups, not part of this change: the Carbon backend (4.15), macOS Call/Hangup defaults, the
`WKUIDelegate` media-capture grant, `AssociatedBundleIdentifiers` for the LaunchAgent, a
`Capabilities.ax_permission_granted` cleanup (the probe still exists in `jarvis/platform/probes.py`
and feeds the capability record), a `report_failed_use` path for Accessibility input and ducking
(4.4), and any per-turn computer-use permission cards (cut from v1: one floating card plus the mission's `blocked_permission` ending; a deck journal
line by trace id was never built).

## 7. Manual test checklist for a real Mac

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
- **Where things are (UI strings from `en.json`):** **Settings > Privacy** is the passive
  permission page. The dictation keys are on **Voice > Shortcuts** ("Push to talk", "Hands-free");
  read them there and write them down: the defaults in `jarvis/core/config.py` are
  `ctrl+right_alt+j` (hold to talk) and `ctrl+right_alt+space` (press to start, press again to
  stop); on a Mac keyboard record what the page shows for the right-hand Alt key. The **Enable
  global shortcuts** button sits on the Input Monitoring status note, shown on the **Keyboard
  shortcuts** page (and under **Settings > Keyboard shortcuts**), **Voice > Shortcuts**, the
  **Dictation** page and the **Appshots** page. "Mute music while dictating" is under **Settings >
  Overlay & taskbar**. The wake word is under **Settings > Wake word** ("Activate wake word", "Test
  wake word"). The composer's dictation button is labelled "Dictate"; the voice-conversation
  control is "Speak in this conversation".
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
- The six service names Microphone, ScreenCapture, Accessibility, PostEvent, ListenEvent and
  AppleEvents are the ones the in-app reset uses [code: `jarvis/platform/permissions.py`];
  the `SystemPolicy*` names are [C] / unverified (the repo never resets those). That each name is
  accepted by `tccutil`, and that each reset really removes the entry, is unverified for every
  macOS version: check each command on the Mac under test.

Reset everything for one app (replace the bundle id with the one under test):

```bash
for s in Microphone ScreenCapture Accessibility PostEvent ListenEvent AppleEvents \
         SystemPolicyDesktopFolder SystemPolicyDocumentsFolder SystemPolicyDownloadsFolder \
         SystemPolicyRemovableVolumes SystemPolicyNetworkVolumes; do
  tccutil reset "$s" ai.personaljarvis.desktop   # or com.personal-jarvis.desktop
done
```

Single services, used by the rows in 7.3 (`<id>` is the bundle id under test):

| Permission | Command |
|---|---|
| Microphone | `tccutil reset Microphone <id>` |
| Screen Recording | `tccutil reset ScreenCapture <id>` (system-wide service, may need `sudo`, may toggle instead of delete) |
| Accessibility | `tccutil reset Accessibility <id>` |
| Post synthetic events | `tccutil reset PostEvent <id>` (reset it together with Accessibility) |
| Input Monitoring | `tccutil reset ListenEvent <id>` |
| Automation | `tccutil reset AppleEvents <id>` |
| Files and Folders | `tccutil reset SystemPolicyDesktopFolder <id>`, `tccutil reset SystemPolicyDocumentsFolder <id>`, `tccutil reset SystemPolicyDownloadsFolder <id>`, `tccutil reset SystemPolicyRemovableVolumes <id>`, `tccutil reset SystemPolicyNetworkVolumes <id>` |

- Prefer a fresh macOS VM snapshot between runs (Apple DTS: "the most reliable way to test TCC is
  in a VM, restoring to a fresh snapshot between each test" [D]; the thread id was not recorded
  when this page was written, so the wording is unconfirmed).
- "Dialog" below always means Apple's own system dialog. Jarvis shows its own floating card only
  where a row says so.

### 7.2 Environment-level rows

| ID | Scenario | Steps | Expected | Result | Signed off (name, date) |
|---|---|---|---|---|---|
| M-FRESH-1 | Fresh install (`.dmg`) | Quit the app; run the "reset everything" loop of 7.1 for `ai.personaljarvis.desktop`; install the `.dmg` (drag to Applications); start the app from Applications; walk the first-run setup (welcome, keys, subscriptions, voice, ready) | App starts. No Apple dialog, no card, no banner, no wizard at launch or on the welcome, keys and subscriptions steps, and no permissions step in setup. On the voice step, switching the wake word on is a user gesture and raises Apple's microphone dialog (expected; record its text). If the wake word stays off, no dialog appears during setup. Settings > Privacy lists every row you did not trigger as "Off or not asked" | | |
| M-FRESH-2 | Fresh install (managed app) | Same loop for `com.personal-jarvis.desktop`; install with the one-line installer (7.0) | Same as M-FRESH-1, including the voice-step microphone dialog | | |
| M-FRESH-3 | Boot with the wake word on and nothing granted | Enable the wake word in a previous run, quit the app, run `tccutil reset Microphone <id>`, start the app | No dialog at launch; no input stream opened; the wake word is quietly blocked (the sidebar voice status is meant to show a quiet blocked-by-permission look; record what it shows); Privacy shows the microphone as "Off or not asked" | | |
| M-UPG-1 | Upgrader with all grants present | Grant everything, then update or restart the app | Zero cards, zero requests, features work at once | | |
| M-DEV-1 | Terminal-launched dev run (outside the installed app) | `python -m jarvis` (or the dev launcher) from a terminal | The outside-app path: no native request without the confirmation; the confirmation names the terminal app as the grantee; Privacy says whose grant it is; "Ask again" is not offered; a "granted" state here proves nothing about the installed app | | |
| M-APP-1 | `.dmg` app versus managed app | Grant the microphone to one, start the other | The other app is not granted and asks on its own first use; both Privacy pages show their own state | | |
| M-ARCH-1 | Intel and Apple Silicon | Repeat M-FRESH-1 and the microphone and Screen Recording rows on one Mac of each architecture | Same behaviour; record any difference | | |
| M-UI-1 | Light and dark appearance of the card | Trigger a blocked episode (deny the microphone, press **Dictate** again) in light and in dark mode, and over a terminal pane | The card (heading "Access is turned off", buttons "Open System Settings", "Check again", "Not now") is legible, uses theme tokens, does not cover the terminal controls, does not take focus from the dictation target | | |
| M-SIGN-1 | Signed build entitlements | On a Developer ID signed build: `codesign -d --entitlements :- "<app>"` | Lists `audio-input`, `apple-events`, the two `cs.*` keys; no camera key; the microphone dialog appears on first use | | |

### 7.3 Per permission

For every row: record the exact dialog text, whether the app was frontmost, and whether anything
unexpected appeared (a second dialog, a hang).

| ID | Permission | Trigger action | Expected dialog or behaviour | Denial path | Route back | After-grant behaviour |
|---|---|---|---|---|---|---|
| M-MIC-1 | Microphone | Quit the app, run `tccutil reset Microphone <id>`, start it, then press **Dictate** in the chat composer (UI-started) | Apple microphone dialog naming the app with the usage string; the card does **not** open while the dialog is up | Press Don't Allow: a note floating above the dictation button (the floating card stays quiet while it is mounted) says dictation cannot work because Microphone is off and offers **Open System Settings**; typed chat still works | The note's (or the card's, or the Privacy row's) **Open System Settings** opens System Settings > Privacy & Security > Microphone | Allow it there: dictation works without a restart; the note reads "Microphone allowed. Press the microphone again to start dictating." |
| M-MIC-2 | Microphone, hold key | Same reset as M-MIC-1, then press and hold the hold-to-talk shortcut (7.0) | First press raises the dialog; releasing does not start a late recording | After Allow: the UI says "Microphone allowed. Press the microphone again to start dictating."; the next hold works | as above | as above |
| M-MIC-3 | Microphone, voice conversation | Same reset as M-MIC-1, then press **Speak in this conversation** (embedded window) | One dialog (R1: note whether WebKit adds a second); after Allow the note reads "Microphone allowed. Press the button again to start the conversation." and the next press starts it | Deny: the card with the "Voice in the browser" sentence | as above | works |
| M-MIC-4 | Microphone, wake word | Same reset as M-MIC-1, then switch **Settings > Wake word > Activate wake word** on; separately try **Test wake word** | The switch is the gesture: dialog appears; an inline note (no success toast) on a non-grant; the test button waits up to a minute for the answer | Deny: the wake word stays off, the note says why | as above | The note reads "Microphone allowed. The wake word can listen now." and the parked wake loop starts on its own |
| M-MIC-5 | Microphone, zeros | With the app granted, set the input to a muted or disconnected source (or revoke while running) | After about 5 s of exact zeros: one "denied or muted" notice, no crash; revoking while running ends the stream within a few seconds and shows the card | n/a | as above | Restore and retry |
| M-SR-1 | Screen Recording | Quit, `tccutil reset ScreenCapture <id>`, start; ask Jarvis to look at the screen (the snapshot tool or computer use) | `CGRequestScreenCaptureAccess` dialog offering Open System Settings; the capture refuses honestly (**no wallpaper frame**) | Don't allow or ignore: the card appears after refocus or about 15 s, no repeated prompting | Pane "Screen & System Audio Recording" (or "Screen Recording" before macOS 15); the card's **Open System Settings** | After flipping the switch, return to Jarvis: note whether capture works in the running process (R3), and whether "Quit and reopen" is offered (an appshot shows "Screen Recording allowed. Take the appshot again.") |
| M-SR-2 | Screen Recording, macOS 15 and later | Capture twice, as a user, after the grant | Note any "bypass the system picker" or "Allow for one month" alert, its timing, and that a capture timeout during it is shown as pending, not denied | n/a | n/a | n/a |
| M-SR-3 | Screen Recording, computer use | Start a computer-use task | One coalesced card for Screen Recording and Accessibility if both are missing; the agent text tells the agent not to touch the dialog; with a consent window frontmost nothing is dispatched | Mission ends as `blocked_permission` with a Retry button | panes | Retry continues |
| M-SR-4 | Restart conflict | Grant while Jarvis runs, return to Jarvis, capture again | Record: works without restart / works only after restart / still blocked; the hint appears only after a real failed attempt | n/a | n/a | n/a |
| M-SR-5 | Lapse | Revoke the grant while running, run a computer-use task | The next action fails closed with an honest message | n/a | pane | n/a |
| M-SR-6 | Stranded entry | After a re-sign or a manual remove and re-add, the switch shows on but capture fails; press **Open System Settings**, come back | The card shows "It still looks off..." and offers "Already on? Reset and ask again"; verify the state after the reset ("Reset. macOS will ask again now." or "That did not reset it...") | n/a | manual path incl. reboot hint if the entry is stuck | n/a |
| M-AX-1 | Accessibility | Quit, `tccutil reset Accessibility <id>` and `tccutil reset PostEvent <id>`, start; use dictation into another app (auto-paste), or a computer-use click | First time Jarvis must type or click: Apple Accessibility dialog (offers Open System Settings); until granted the dictation falls back to the clipboard and says so | Deny: text stays on the clipboard (`clipboard_only`) | Pane "Accessibility" (macOS 27: note the actual label, R2) | Flip the switch: the first paste after the grant is reported as sent, not as inserted, until observed |
| M-AX-2 | Accessibility, no re-prompt | Click the same action repeatedly while untrusted | At most one dialog per 10 minutes per process | n/a | n/a | n/a |
| M-AX-3 | Accessibility reads | Without the grant, use anything that reads the UI tree | No dialog, no card; the feature degrades quietly | n/a | n/a | n/a |
| M-AX-4 | Deep link | Press **Open System Settings** for Accessibility, Input Monitoring and Screen Recording with System Settings already open on another pane | The right pane opens (the app quits a running System Settings unless this process last opened the same pane, then opens the anchor); on macOS 27 note whether the anchor resolves. Extra step: open pane A, navigate manually to another pane in System Settings, press the button for A again, and record whether the pane shown is A | n/a | n/a | n/a |
| M-IM-1 | Input Monitoring | Quit, `tccutil reset ListenEvent <id>`, start; open the Keyboard shortcuts page (7.0) and press **Enable global shortcuts**; separately, do one UI-started dictation to see the one-time tip "Dictate from any app" | Apple Input Monitoring dialog; **nothing at boot**; the status note reads "Global shortcuts work while another app is in front. For that, macOS needs your OK to let Personal Jarvis watch the keyboard (Input Monitoring)..." until granted; buttons and voice keep working | Deny: the note asks to switch on Personal Jarvis under Input Monitoring (no "you denied" wording); Esc-to-cancel never claims a dead key | Pane "Input Monitoring" | The note reads "Input Monitoring allowed. Global shortcuts are on."; note whether the shortcut works in the running process or needs a restart (the restart sentence "Input Monitoring is allowed, but macOS passes key presses to Personal Jarvis only after it restarts" appears only after real typing produced no events) |
| M-HK-1 | Hold-to-dictate | Hold the dictation shortcut in another app | Dictation starts on press and ends on release | n/a | n/a | n/a |
| M-HK-2 | Hold-to-dictate, modifier released first | Hold the dictation shortcut, release the modifier key before the letter key | Record whether dictation ends, or keeps recording until the letter key is released (settles part of the Carbon flip rule in 4.15) | n/a | n/a | n/a |
| M-HK-3 | Re-arm and quit | After a clean reset and one grant: save a shortcut in Settings ten times in a row; drag a window during a re-arm; then quit the app and time it | Each save re-arms the shortcut without a dialog; dragging stays smooth; the app quits in under 5 s. Record any miss | n/a | n/a | n/a |
| M-HK-4 | Chord collision | Check the default toggle chord against System Settings > Keyboard > Keyboard Shortcuts > Input Sources | Record any collision (R9) | n/a | n/a | n/a |
| M-HK-5 | Appshot both-Option | With Input Monitoring **not** granted, press both Option keys | Record whether it fires (settles R6) | n/a | n/a | n/a |
| M-AUTO-1 | Automation | With Music running (a window open), switch **Mute music while dictating** on (Settings > Overlay & taskbar) | Apple Automation dialog naming the app and Music; Music is not launched by Jarvis; with no player running, no dialog and the inline note "No music player is running, so nothing was checked..." | Deny: the inline status names the player; dictation continues without ducking | Pane "Automation" | Ducking works on the next dictation |
| M-AUTO-2 | Automation off | Keep the switch off | No Automation dialog ever, no player launched | n/a | n/a | n/a |
| M-AUTO-3 | Attribution | After Allow, dictate while music plays | The volume drops (a `-1743` after Allow means the grant is not attributed to the app that sends; record it, R5) | n/a | n/a | n/a |
| M-FF-1 | Files and Folders | Ask Jarvis to list or save a file in Desktop, Documents or Downloads | Apple's own prompt on first access, with the usage string; nothing pre-checks the folders | Deny: normal error handling | System Settings > Files & Folders | Immediate |
| M-KC-1 | Keychain | First read of a stored key after install or update | The Privacy row "Keychain (API keys)" shows the state; after a declined dialog it says "Keychain access was declined, so API keys are kept in a local file for now..." and **Try again** replays the read | n/a | n/a | n/a |
| M-LOGIN-1 | Autostart | Reboot or log in again with autostart on | Record the "Background Items Added" notice and the name shown in Login Items (R7) | n/a | Login Items | n/a |
| M-PRIV-1 | Settings > Privacy page | Open it with a mix of granted and denied rows; provoke a deaf tap or an unusable Screen Recording grant | Passive: no dialog on open, no polling; the pill reads Granted / Off or not asked / Denied / Restricted / Unavailable / Not required; **Allow**, **Open System Settings** and **Ask again** only where offered; refreshes on window return. Record whether the "Restart needed" pill and **Quit and reopen** show on a row that reads Granted after the real failed use (expected; the panel shows the hint on a granted row, covered by a vitest case) | n/a | n/a | n/a |
| M-AGENT-1 | An agent never answers a dialog | With a dialog on screen, start a computer-use task | Nothing is dispatched; the mission stops with `blocked_permission` | n/a | n/a | n/a |

### 7.4 Evidence ledger: what was verified, and how

**NOT verified on a physical Mac.** Nothing in this table is a substitute for section 7.3.

| Layer | What | How | Result recorded | Strength |
|---|---|---|---|---|
| Unit and contract tests on a framework-level simulator | The service's outcomes, episodes, cooldowns, events, the scenario table on macOS and off it; boot with every permission undecided; an upgrader with all grants; a non-macOS host never touches TCC; the consumers (audio capture, ducking, dictation insert, hotkey tap, screen capture and context, computer use, window control); the routes and snapshot v2 | `tests/fakes/fake_tcc.py` (a stateful TCC simulator: per-service states, scripted dialog policy, no re-ask after a decision, a frozen Screen Recording preflight until relaunch, an ordered call log of probes, requests and implicit prompts), `tests/fakes/fake_permission_service.py`, and the test files in the command of the next column | **2026-10-02, Linux sandbox, Python 3.11, no GPU, no audio hardware:** `python -m pytest tests/unit/platform tests/unit/ui/web/test_permissions_routes.py tests/unit/ui/web/test_permissions_snapshot.py tests/contract/test_permission_service_contract.py tests/unit/core/test_permission_events.py tests/unit/trigger tests/unit/cu tests/unit/screen_context tests/unit/vision tests/unit/dictation/test_insert_permission.py tests/unit/audio/test_capture_permission_gate.py tests/unit/audio/test_ducking_permissions.py tests/unit/speech/test_voice_permission_jit.py` gave **2444 passed, 17 skipped, 0 failed** (133 s). The 17 skips are platform or optional-dependency skips (Windows-only ctypes and token probes, `pynput`, `pyatspi`, PySide6, macOS PyObjC and `Quartz`); no failure occurred, so none needs a cause | The fake models what the code believes about macOS, not macOS itself. It proves the logic, not the OS behaviour |
| Frontend tests | Reducer, store, card, inline notes, Privacy panel, event parity, focus refresh | Vitest | **2026-10-02:** `npx vitest run --maxWorkers=2 src/components/permissions src/lib src/store src/hooks/usePermissions.test.tsx src/views/settings/PermissionsPanel.test.tsx` gave **108 test files passed, 1198 tests passed, 0 failed** (46 s). The frontend was still being polished while this ran, so the counts describe the tree at that moment. The production build and the light, dark and terminal-pane inspections are **not** recorded here | Logic only; no real WebView |
| Linux gates | Docs privacy scan, public-docs, language, parity tests, import-safety on a headless host | `scripts/ci/run_gates.py` and narrower invocations | **2026-10-02:** `python scripts/ci/docs_privacy_scan.py`, `python scripts/ci/check_public_docs.py`, `python scripts/ci/run_gates.py --only docs-privacy,public-docs,mirrors`, `python scripts/ci/check_no_new_german.py` and `python scripts/ci/check_agents_md.py` all passed on the documentation changes of this reconciliation. The full `run_gates.py` was not run for this page | Static and portable-code checks |
| macOS CI lane | Imports of the native frameworks; a tripwire proof that nothing asks outside an installed bundle; symbol binding; a service smoke that never asks; the bundle self-probe; a curated test list | `.github/workflows/macos-desktop.yml` (steps in 4.13); its scripts also run against FakeTCC in `tests/unit/ci/test_macos_desktop_permission_step.py` | **No runner result is recorded for this branch.** The lane steps exist and their scripts pass against the simulator on Linux; the lane itself has not been seen to run green on a macOS runner with the new steps | Runner only; informational shards once it runs |
| Installer workflow dispatch | The `.dmg` app builds with the new spec on Apple Silicon and Intel, `scripts/ci/check_frozen_macos_app.py` passes, the frozen app boots twice and reads its permission status: microphone "granted" under `ai.personaljarvis.desktop`, so AVFoundation loads inside the app | Desktop installers workflow, run 36923225371 at commit `43eeb7aed`, 2026-10-01, no release published (as recorded in commit `ed489bece`) | Passed on both architectures, **ad-hoc signed**, no hardened runtime, no notarization. It predates the just-in-time rebuild, so it says nothing about the new service | Runner evidence. It proves packaging, not prompts, not Gatekeeper |
| Runner spike | Carbon hot-key crash surface | `.github/workflows/macos-hotkey-spike.yml` (dispatch only, 4.15) | Run `36954304202` at commit `59749f859`: harness green on `macos-15` (arm64) and `macos-15-intel`; the arm64 report's verdict is `usable=False` (a preflight read granted in the test process), so it is not TCC evidence; variant G and the off-main register are not covered | Runner only |

What none of the above shows: any real dialog, wording of any dialog, the behaviour of a grant in
a running process, hold-key semantics, the Developer ID or notarized build, macOS 26 and 27
behaviour, a user's Intel Mac, light and dark appearance of the card on a real window, or the
double dialog in the embedded WebView. Each is a row in 7.2 and 7.3 awaiting a sign-off.
