---
title: "App Permissions"
slug: permissions
summary: "See when macOS asks for microphone, screen, accessibility, and input access, and how to change an answer later."
section: "Privacy, safety, and support"
section_order: 6
order: 3
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-10-02
phase: "-"
audience: end-user
tags: [permissions, macos, privacy, computer-use, safety, approvals]
related: [first-run-setup, audio-and-wake-word, computer-use, privacy-and-local-data]
---

Personal Jarvis asks macOS for a permission only when a feature that needs it is
first used or switched on. Nothing is asked when the app starts or from a warning
banner, and setup never needs a permission to continue; a switch you turn on during
setup, such as the wake word, asks for its microphone permission at that moment.
There is no master permission that unlocks your computer, accounts, and tool actions
at once.

On a Mac, the feature you start shows Apple's own dialog at that moment. If you
say no, only that feature stops, Jarvis says why where you are, and one click
opens the right System Settings pane. **Settings > Privacy** lists the same
permissions as a passive status page. Browser permissions, connected-account
access, and Jarvis safety approvals remain separate.

## Before You Start

- On macOS, use the installed **Personal Jarvis** app. The app from the installer
  and the app from the downloaded disk image both count; each is its own app to
  macOS, with its own permissions. Do not grant access to Terminal, Python, or a
  copied app bundle. If Jarvis was started from a terminal, it does not ask on
  its own: the card offers an explicit **Allow for the app that started
  Personal Jarvis** button, and macOS records the answer for that terminal app.
- Decide which features you need. Text chat does not require microphone,
  screen, accessibility, or input access, and setup never requires a permission
  to continue.
- Finish or stop active Jarvis-Agent missions before a restart that a permission
  asks for. **Quit and reopen** refuses to stop a running mission.
- Enter credentials only in **API Keys & Providers** or the relevant connection
  screen. A system permission prompt never needs an API key, password, token,
  or recovery code.

## Check Your Platform

| Platform | What you see | What you still need to check |
|---|---|---|
| macOS desktop | Apple dialogs at first use, a Jarvis card only when something is blocked, and the **Privacy** page in Settings | The installed app identity, the native dialogs, and any restart notice |
| Windows desktop | **No extra desktop privacy permissions are asked by Jarvis on this operating system.** The **Privacy** page is hidden | Windows microphone privacy, User Account Control, file access, and the feature itself |
| Linux desktop | The same: nothing is asked and the page is hidden | Audio device access, file ownership, desktop session, and required desktop tools |
| Headless or remote browser | No local desktop grant can create a microphone or live display | Browser site access and whether the host has the required device or desktop session |

Not asking means only that Jarvis has no macOS permission to manage. It is not a
device-health check. Current global shortcuts and Computer Use cannot inject
input in a Linux Wayland session. Computer Use also needs a live desktop, so it
is unavailable on a headless server.

## When macOS Asks

| Permission | What it allows | When Jarvis asks | If you say no |
|---|---|---|---|
| **Microphone** | Capture local microphone audio | The first time you press the dictation key or button, start a voice conversation, use push-to-talk, switch the wake word on, or run the microphone check | Voice and dictation stop with a note; typed chat is unaffected |
| **Screen Recording** | Capture visible screen content | The first time you ask Jarvis to look at your screen: Computer Use, a screen snapshot, screen context, or an appshot | The capture is refused with a message, never a blank or wallpaper-only picture |
| **Accessibility** | Click, type, focus, and move windows for you | The first time Jarvis must type or click for you: Computer Use input, typing dictated text into another app, window control | Dictated text stays on the clipboard so you can paste it; Computer Use input is refused |
| **Input Monitoring** | Listen for system-wide keyboard shortcuts | When you use **Enable global shortcuts** (Keyboard shortcuts page) | Shortcuts that work while another app is in front stay off; the app's buttons and voice keep working |
| **Automation (Music & Spotify)** | Send Apple Events to Music and Spotify | When you switch **Mute music while dictating** on while a player is running | Jarvis skips volume control for that player and dictation continues |
| **Keychain (API keys)** | Store API keys in macOS Keychain | When Jarvis first stores or reads a key | Keys are kept in a permission-restricted local file instead |

Jarvis does not ask for a permission that a feature you have not turned on needs.
Features that only read the interface structure of another app never open a dialog;
they quietly do less. macOS owns the wording and the look of its dialogs. On
some macOS versions the system may add its own extra Screen Recording
confirmation; that comes from macOS, not from Jarvis.

The Appshots both-Option shortcut is not listed as an Input Monitoring item
because it is not established whether macOS requires that permission for it.

## If You Say No

A card appears in the app only when a feature is blocked and you started it, never
while an Apple dialog is open. Its buttons:

- **Continue** before a dialog, when Jarvis wants to tell you what is about to be
  asked.
- **Open System Settings** opens the matching pane. Turn on Personal Jarvis, then
  return to the app.
- **Check again** re-reads the permission after you changed it.
- **Not now** hides the card for this one request. Nothing is remembered.
- **Quit and reopen** appears only when a permission is on but macOS applies it
  to a fresh process, and only after a real attempt failed.
- **Already on? Reset and ask again** appears when System Settings shows a switch
  on but the feature still fails, which can happen after an app update changed
  its signature. If the reset does not help, turn the switch off and on in System
  Settings, and restart the Mac if it still fails.

macOS normally does not ask again after you decide, so Jarvis does not keep
asking either. When you return from System Settings the card checks again by
itself; on a Mac the microphone is expected to work without a restart. A change
to Screen Recording or Input Monitoring may need **Quit and reopen**, and Jarvis
tells you only after a real attempt failed.

For a missing input shortcut, the Shortcuts page shows one status note: ready,
needs Input Monitoring, or unavailable in this mode.

## Settings > Privacy

Open **Settings > Privacy > macOS privacy permissions** (macOS only). The page
never raises a dialog by itself and does not poll. It refreshes when you open it,
when the app regains focus, and after an action. Each row shows:

| Status | Meaning | What to do |
|---|---|---|
| **Granted** | The native check reports access | Test the feature |
| **Off or not asked** | No decision is recorded, or the check cannot confirm access | Use **Allow** if offered, or just use the feature and macOS will ask |
| **Denied** | macOS reports a denied decision | Use **Open System Settings**, or **Ask again** where offered, then use the feature |
| **Restricted** | Device policy or a system rule prevents the grant | Ask the device administrator or review the Mac's policy |
| **Unavailable** | Jarvis cannot use the native permission check here | Reopen the installed app, use **Check again**, and review the installation if it persists |
| **Restart needed** | macOS applies a grant only to a fresh process | Use **Quit and reopen** after active missions finish |
| **Not required** | The macOS permission flow does not apply to this host | Check the operating system, browser, device, or desktop session directly |

Each row names its pane in words (for example System Settings > Privacy &
Security > Microphone), because macOS renames panes between versions. The
Screen Recording pane may be called **Screen & System Audio Recording**.
**Ask again** removes only Personal Jarvis's recorded decision for that row; it
does not grant anything.

**Keychain (API keys)** has no **Open System Settings** or **Ask again** action. If
Keychain access was declined, Jarvis keeps working with a permission-restricted
local file and the row says so. Use **Try again** to retry Keychain access. The
local file is a compatibility fallback, not encrypted Keychain storage.

To revoke access, turn off Personal Jarvis in the matching macOS **Privacy &
Security** pane. The app has no **Revoke** or **Reset all permissions** button.
Revoking stops later use of that capability. It does not delete audio,
screenshots, action records, or provider data that already exists.

Grants for the app the installer builds on your Mac are recorded against a local
signing certificate the installer creates once (macOS asks for your login password
that one time), so they survive later updates. The app from the downloaded disk
image is signed differently: unless it carries a stable Apple signature, macOS may
treat a new version as a new app and ask again at first use.

## Permissions This Page Does Not Manage

### Files and Notifications

There is no general **Files** row, and Jarvis does not require **Full Disk
Access**. File access depends on the operating-system account running Jarvis, the
folder you choose, a browser file picker, a connected service, or a Jarvis-Agent
mission workspace. macOS may separately ask for **Files & Folders** access to
protected locations the first time something touches them. Grant the narrow access
the task needs instead of Full Disk Access.

Notification delivery is outside Jarvis's permission page. Manage it in the
operating system or browser that displays the notification. A browser can
separately ask for microphone, camera, download, or notification access for one
site and one browser profile.

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
An AI agent never answers a macOS permission dialog: while one is on screen,
Computer Use dispatches nothing and the task stops until you act. Read [Safety and
Approvals](safety-and-approvals) before allowing consequential or unattended work.

## How It Fits Together

A feature works only when every required boundary is ready:

1. You start the feature from the app, voice, a shortcut, or a scheduled task.
2. On macOS, Jarvis asks the operating system the first time the feature needs a
   permission, from the action you took, and then acts only on a live grant. A
   revoked grant stops the next capture or input action.
3. The device, desktop session, model, plugin, or connected account must also
   be available.
4. Jarvis applies the safety decision to the exact proposed tool call.
5. The operating system or external service can still refuse the operation.

For example, macOS Computer Use needs **Screen Recording** and **Accessibility**,
plus a live desktop and a working image-capable model. The screen grant does not
approve a click, and a safety approval cannot create a missing screen grant.

## Check That It Works

Use the microphone as a small, harmless check:

1. On a Mac where you have not answered the microphone question, press the
   dictation button in the composer. macOS shows its microphone dialog; allow it.
2. Open **Settings > Privacy** and confirm that **Microphone** says **Granted**.
3. Open the wake-word **Microphone check**, activate **Test your microphone**,
   and speak a short phrase. A usable level confirms that both the macOS grant and
   the selected microphone work.

On Windows, Linux, or a browser, run the same microphone check and review that
platform's device or site settings if it cannot capture audio. To verify screen
and input access, use the non-private calculator check in [Computer
Use](computer-use).

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A feature says access is off, but no dialog appeared | macOS already recorded your earlier answer and does not ask twice | Use **Open System Settings** from the card or the Privacy page and turn Personal Jarvis on |
| System Settings shows the switch on, but the feature still fails | The record may belong to an older version of the app | Use **Already on? Reset and ask again** or **Ask again**; if that fails, turn the switch off and on, then restart the Mac |
| The card says Jarvis is not running as an installed app | Jarvis was started from a terminal or a copy, so macOS would record access for that program | Open the installed Personal Jarvis app, or allow it explicitly for the program that started Jarvis |
| Permissions are asked for again after an update | The new version was signed differently, so macOS treats it as a new app | Answer once more. A build signed with a stable certificate keeps its grants |
| **Quit and reopen** refuses to restart | A Jarvis-Agent mission is still running | Finish or stop the mission, then try again |
| The page is hidden or says **Not required** but the feature is blocked | The host is not macOS, or a browser, device, display session, Wayland, or file boundary owns the failure | Test the feature directly and review the relevant host or browser settings |
| Every row says **Granted**, but the feature fails | A restart, device, model, connection, account scope, or target application is still unavailable | Follow the dependent feature's troubleshooting steps |

## Next Steps

- Use [First-Run Setup](first-run-setup) to review onboarding and the text-only
  path.
- Read [Audio and Wake Word](audio-and-wake-word) to test microphone access,
  device selection, and wake readiness together.
- Read [Computer Use](computer-use) before granting screen and input control or
  letting Jarvis operate a live desktop.
- Review [Privacy and Local Data](privacy-and-local-data) to understand what a
  granted feature can store locally or send to a connected service.
