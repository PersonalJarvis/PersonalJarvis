---
title: "App Permissions"
slug: permissions
summary: "Grant only the microphone, screen, accessibility, and input permissions needed for voice, global shortcuts, and Computer Use."
section: "Privacy, safety, and support"
section_order: 6
order: 3
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [permissions, macos, privacy, computer-use, safety, approvals]
related: [first-run-setup, audio-and-wake-word, computer-use, privacy-and-local-data]
---

Use App Permissions to grant only the local access required by the features
you use. Personal Jarvis has no master permission that unlocks your computer,
accounts, and tool actions at once.

The current card manages six items for the installed macOS desktop app. Five
are macOS privacy permissions. The sixth reports whether API keys use macOS
Keychain. Browser permissions, connected-account access, and Jarvis safety
approvals remain separate.

## Before You Start

- On macOS, open the installed **Personal Jarvis** app and bring it to the
  foreground. Do not grant access to Terminal, Python, or a copied app bundle.
  macOS records access against the exact app identity.
- Decide which features you need. Text chat does not require microphone,
  screen, accessibility, or input access.
- Finish or stop active Jarvis-Agent missions before a permission change that
  needs a restart. **Restart now** refuses to stop a running mission.
- Enter credentials only in **API Keys & Providers** or the relevant connection
  screen. A system permission prompt never needs an API key, password, token,
  or recovery code.

During first-run setup, an interactive Mac shows all six rows. **Continue** is
enabled when every row is ready or is waiting for the final setup restart.
Choose **Continue with text only** to skip the remaining grants. Completing
setup restarts the installed desktop app when that restart is available.

## Check Your Platform

| Platform | What App Permissions shows | What you still need to check |
|---|---|---|
| macOS desktop | Six permission rows and available actions | The installed app identity, native prompts, and any restart notice |
| Windows desktop | **No extra desktop privacy permissions are required on this operating system.** | Windows microphone privacy, User Account Control, file access, and the feature itself |
| Linux desktop | The same **Not required** message | Audio device access, file ownership, desktop session, and required desktop tools |
| Headless or remote browser | No local desktop grant can create a microphone or live display | Browser site access and whether the host has the required device or desktop session |

The **Not required** message means only that this card has no macOS grant to
manage. It is not a device-health check. Current global shortcuts and Computer
Use cannot inject input in a Linux Wayland session. Computer Use also needs a
live desktop, so it is unavailable on a headless server.

## Grant and Review macOS Access

Open **Settings > Privacy permissions > macOS permissions**. The card uses
these visible row names:

| Permission | What it allows | Needed for | Restart after request or settings visit |
|---|---|---|---|
| **Microphone** | Capture local microphone audio | Wake word and local voice input | No |
| **Screen Recording** | Capture visible screen content | Computer Use and screen-based vision | Yes |
| **Accessibility** | Read supported interface structure, focus or move windows, and support reliable input | Computer Use, window control, and global shortcuts | Yes |
| **Input Monitoring** | Listen for configured system-wide keyboard shortcuts | Global shortcuts | Yes |
| **Input control** | Post mouse and keyboard events | Computer Use | No |
| **Keychain (API keys)** | Store API keys in macOS Keychain | Encrypted operating-system credential storage | No |

Current macOS versions may name the Screen Recording pane **Screen & System
Audio Recording**. **Input control** is Jarvis's label for posting input
events. Its state can follow **Accessibility**, and **Open Settings** may open
the Accessibility pane rather than a separate pane named Input control.

Grant one item at a time:

1. Move focus to **Allow** for the row you need and activate it. Read the
   native macOS prompt before you approve or deny it.
2. If **Open Settings** is available, activate it to open the matching privacy
   pane. Turn on Personal Jarvis, then return to the app.
3. Wait for the card to check again. It refreshes when the app regains focus
   and, while access is missing, about every 2.5 seconds while visible.
4. If a row says **Denied**, use **Ask again** to remove only Personal Jarvis's
   recorded decision. This does not grant access. Activate **Allow** afterward
   and answer the new system prompt. You can also use **Open Settings**.
5. If **Restart now** appears, finish or stop active missions and activate the
   button. Screen Recording, Accessibility, and Input Monitoring changes are
   treated as unavailable until a fresh app process applies them.
6. Test the dependent feature. **Allowed** confirms the native grant, not the
   health of a microphone, model, provider, connection, or target app.

**Keychain (API keys)** has no **Open Settings** or **Ask again** action. If
Keychain access was declined, Jarvis keeps working with a permission-restricted
local file and the row stays **Not allowed**. Use **Allow** to retry Keychain
access. The local file is a compatibility fallback, not encrypted Keychain
storage.

### Read Each Status

| Status | Meaning | What to do |
|---|---|---|
| **Allowed** | The native check reports access | Test the feature; restart first if the card asks |
| **Not requested** | macOS reports that no choice has been recorded | Use **Allow** and answer the native prompt |
| **Not allowed** | The check cannot confirm access; some native checks cannot distinguish a previous denial from no choice | Use **Allow** or **Open Settings** when available |
| **Denied** | macOS reports a denied decision | Use **Ask again**, then **Allow**, or change the switch in System Settings |
| **Restricted** | Device policy or a system rule prevents the grant | Ask the device administrator or review the Mac's policy |
| **Unavailable** | Jarvis cannot use the native permission check on this installation | Reopen the installed app, use **Check again**, and review the installation if it persists |
| **Restart pending** | A requested Screen Recording change is waiting for a fresh process | Use **Restart now** after active missions finish |
| **Not required** | The macOS permission flow does not apply to this host | Check the operating system, browser, device, or desktop session directly |

To revoke access, turn off Personal Jarvis in the matching macOS **Privacy &
Security** pane. The app has no **Revoke** or **Reset all permissions** button.
**Ask again** is recovery for one denied row, not a revocation control. Return
to the app after a change and restart when asked.

Revoking access stops later use of that capability. It does not delete audio,
screenshots, action records, or provider data that already exists.

## Permissions This Card Does Not Manage

### Automation, Files, and Notifications

The optional **Mute music while dictating** setting can ask for macOS
**Automation** access to Music or Spotify. Automation is not one of the six
rows. If you deny it, Jarvis skips player-specific volume control and voice can
continue. Review that access in macOS **System Settings > Privacy & Security >
Automation**.

There is no general **Files** row, and this flow does not require **Full Disk
Access**. File access depends on the operating-system account running Jarvis,
the folder you choose, a browser file picker, a connected service, or a
Jarvis-Agent mission workspace. macOS may separately ask for **Files &
Folders** access to protected locations. Grant the narrow access the task needs
instead of Full Disk Access.

Notification delivery is also outside this card. Manage it in the operating
system or browser that displays the notification. A browser can separately ask
for microphone, camera, download, or notification access for one site and one
browser profile.

### App Permissions and Tool Approvals

An operating-system grant lets the app reach a local capability. It does not
authorize a particular Jarvis tool action.

| Boundary | What it decides | Typical lifetime |
|---|---|---|
| Operating-system permission | Whether this app identity can use a microphone, screen, accessibility interface, or input device | Until you revoke it or the app identity changes |
| Browser site permission | Whether one site in one browser profile can use a device or browser capability | Until changed in that browser |
| Service authorization | Which account and service scopes a connection can use | Until expiry, disconnect, or service-side revocation |
| Jarvis safety decision | Whether one proposed tool call runs, needs confirmation, or is blocked | Usually one exact call; narrow standing rules are separate |

Jarvis classifies a proposed tool call as **safe**, **monitor**, **ask**, or
**block**. Safe and monitored calls can run without a prompt. Ask-level calls
need a supported confirmation, and no answer is treated as a denial. Blocked
calls do not run. Granting screen or input access never bypasses this decision.
Read [Safety and Approvals](safety-and-approvals) before allowing
consequential or unattended work.

## How It Fits Together

A feature works only when every required boundary is ready:

1. You start the feature from the app, voice, a shortcut, or a scheduled task.
2. On macOS, Jarvis checks the live native grant, the installed app identity,
   and any pending restart. Revoking a grant can stop the next capture or input
   action.
3. The device, desktop session, model, plugin, or connected account must also
   be available.
4. Jarvis applies the safety decision to the exact proposed tool call.
5. The operating system or external service can still refuse the operation.

For example, macOS Computer Use needs **Screen Recording**,
**Accessibility**, and **Input control**, plus a live desktop and a working
image-capable model. The screen grant does not approve a click, and a safety
approval cannot create a missing screen grant.

## Check That It Works

Use the microphone as a small, harmless check:

1. On macOS, open **Settings > Privacy permissions > macOS permissions** and
   confirm that **Microphone** says **Allowed**.
2. Open the wake-word **Microphone check** and activate **Test your
   microphone**.
3. Speak a short phrase. A usable level confirms that both the macOS grant and
   the selected microphone work.

On Windows, Linux, or a browser, run the same microphone check and review that
platform's device or site settings if it cannot capture audio. To verify screen
and input access, use the non-private calculator check in [Computer
Use](computer-use).

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| App identity warning or no permission actions | Jarvis is not the foreground installed app, or the session is headless | Quit it and open the installed Personal Jarvis app in an interactive macOS session |
| **Denied** or **Not allowed**, but no prompt appears | macOS already recorded a choice or the native check cannot distinguish it | Use **Ask again** for a denied privacy row, then **Allow**; otherwise use **Open Settings**. For Keychain, use **Allow** |
| **Restart now** refuses to restart | A Jarvis-Agent mission is still running | Finish or stop the mission, then use **Restart now** again |
| The card says **Not required**, but the feature is blocked | The host is not macOS, or a browser, device, display session, Wayland, or file boundary owns the failure | Test the feature directly and review the relevant host or browser settings |
| Every required row says **Allowed**, but the feature fails | A restart, device, model, connection, account scope, or target application is still unavailable | Clear any restart notice, then follow the dependent feature's troubleshooting steps |

## Next Steps

- Use [First-Run Setup](first-run-setup) to review onboarding and the text-only
  path.
- Read [Audio and Wake Word](audio-and-wake-word) to test microphone access,
  device selection, and wake readiness together.
- Read [Computer Use](computer-use) before granting screen and input control or
  letting Jarvis operate a live desktop.
- Review [Privacy and Local Data](privacy-and-local-data) to understand what a
  granted feature can store locally or send to a connected service.
