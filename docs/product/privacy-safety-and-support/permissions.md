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
setup, such as the wake word, asks at that moment. No master permission unlocks your
computer, accounts, and tool actions at once.

On a Mac, the feature you start shows Apple's own dialog at that moment, and
Jarvis draws nothing of its own around it. If you say no, only that feature
stops. macOS does not ask a second time and says nothing afterwards, so Jarvis
shows one short message with one button, and the button opens the right System
Settings pane. There is no permissions page to maintain. Browser permissions,
connected-account access, and Jarvis safety approvals remain separate.

## Before You Start

- On macOS, use the installed **Personal Jarvis** app. The app from the installer
  and the app from the downloaded disk image both count; each is its own app to
  macOS, with its own permissions. Do not grant access to Terminal, Python, or a
  copied app bundle. If Jarvis was started from a terminal, it does not ask on
  its own: after something you started needs a permission, the message offers
  **Ask macOS now**, and macOS records the answer for the app that started Jarvis
  (the terminal), not for the installed app. A copy running outside Applications
  does the same, for that copy only.
- Decide which features you need. Text chat requires none of these permissions.
- Finish or stop active Jarvis-Agent missions first: **Quit and reopen** refuses to
  stop a running mission.
- Enter credentials only in **API Keys & Providers** or the connection screen. A
  system permission prompt never needs an API key, password, token, or recovery code.

## Check Your Platform

| Platform | What you see | What you still need to check |
|---|---|---|
| macOS desktop | Apple dialogs at first use (German and Spanish text in new builds, English otherwise), and one short Jarvis message only when something you started is blocked | The installed app identity, the native dialogs, and any restart notice |
| Windows desktop | **No extra desktop privacy permissions are asked by Jarvis on this operating system.** Nothing is shown | Windows microphone privacy, User Account Control, file access, and the feature itself |
| Linux desktop | The same: nothing is asked and nothing is shown | Audio device access, file ownership, desktop session, and required desktop tools |
| Headless or remote browser | No local desktop grant can create a microphone or live display | Browser site access and whether the host has the required device or desktop session |

Not asking means only that Jarvis has no macOS permission to manage; it is not a
device-health check. Current global shortcuts and Computer Use cannot inject
input in a Linux Wayland session, and Computer Use needs a live desktop, so it is
unavailable on a headless server.

## When macOS Asks

| Permission | What it allows | When Jarvis asks | If you say no |
|---|---|---|---|
| **Microphone** | Capture local microphone audio | The first time you press the dictation key or button, start a voice conversation, use push-to-talk, switch the wake word on, or run the microphone check | Voice and dictation stop with a note; typed chat is unaffected |
| **Screen Recording** | Capture visible screen content | The first time you ask Jarvis to look at your screen: Computer Use, a screen snapshot, screen context, or an appshot | The capture is refused with a message, never a blank or wallpaper-only picture |
| **Accessibility** | Click, type, focus, and move windows for you | The first time Jarvis must type or click for you: Computer Use input, typing dictated text into another app, window control | Dictated text stays on the clipboard so you can paste it; Computer Use input is refused |
| **Input Monitoring** | Listen for system-wide keyboard shortcuts | When you save a global shortcut (on **Voice > Shortcuts** or the Keyboard shortcuts page), or choose the Call shortcut in setup. A shortcut works while another app is in front, so no single key press could carry the question | Shortcuts that work while another app is in front stay off; the app's buttons and voice keep working |
| **Automation (Music & Spotify)** | Send Apple Events to Music and Spotify | When you switch **Mute music while dictating** on while a player is running | Jarvis skips volume control for that player and dictation continues |
| **Keychain (API keys)** | Store API keys in macOS Keychain | When Jarvis first stores or reads a key; macOS shows its own Keychain prompt | Keys are kept in a permission-restricted local file instead |

Jarvis does not ask for a permission that a feature you have not turned on needs.
Features that only read the interface structure of another app never open a dialog;
they quietly do less. macOS owns the wording and the look of its dialogs. On
some macOS versions the system may add its own extra Screen Recording
confirmation; that comes from macOS, not from Jarvis.

The Appshots both-Option shortcut is not listed under Input Monitoring: it is not
established whether macOS requires that permission for it.

## If You Say No

macOS normally does not ask again after you decide, and it shows nothing when a
feature then fails. So when a feature you started is blocked by a permission,
Jarvis shows one short message in its usual notification corner, with one button.
It is never shown while an Apple dialog is open and never for something that started
in the background. It is not repeated while on screen, but comes back if you dismiss
it, or it fades, and you try the feature again. It appears in the main desktop
window only (not a detached window or a browser), and only while that window is
visible: a message due while it was hidden is not saved for later. The wording is calm
and names no settings pane, for example "Personal Jarvis needs microphone access
to hear you." The button is one of:

- **Open System Settings**, which opens the matching pane. Turn on Personal
  Jarvis, then use the feature again.
- **Ask macOS now**, when macOS can still ask, or when Jarvis was started from a
  terminal or a copy (the message then says the app that started Jarvis gets the
  access).
- **Quit and reopen**, only when a permission is on but macOS applies it to a
  fresh process, and only after a real attempt failed.

A Mac restricted by device policy gets a sentence and no button. One background feature may speak up: if the wake word you switched
on cannot listen because the microphone is off, Jarvis says so once per session.
Dictation also shows its own short line on the bar, and Computer Use reads its
reason aloud.

A permission you turn on in System Settings is expected to work at once. If a switch
shows on but the feature still fails, as can happen after an update changed the
app's signature, start over (next section).

## Start Over for One Permission

There is no permissions page and no reset button in the app. To make macOS forget
its answer for Personal Jarvis, so that it asks again the next time you use the
feature, run this in a terminal on the Mac (it works with Jarvis closed):

```bash
jarvis permissions reset microphone --yes
```

If the terminal says `jarvis: command not found` (the downloaded app does not put it on
your path), run `"/Applications/Personal Jarvis.app/Contents/MacOS/jarvis"` instead, or
use `tccutil reset Microphone ai.personaljarvis.desktop` (installer's app:
`com.personal-jarvis.desktop`); [Troubleshooting](troubleshooting) has the rest.

Replace `microphone` with `screen_recording`, `accessibility`, `input_monitoring`,
or `automation`. The command resets only Personal Jarvis's own record for that one
permission, for the installed app (`--bundle-id` names the other app when both the
installer's and the downloaded app are installed), never another app's. `--dry-run`
shows the exact `tccutil` command without running it. Quit and reopen Jarvis
afterwards, then use the feature. Other systems print one line and stop. The raw
`tccutil` commands are in [Troubleshooting](troubleshooting). `jarvis permissions
status` shows where each permission stands and never asks.

**Keychain (API keys)** has no System Settings pane and no reset. If Keychain access
was declined, Jarvis keeps working with a permission-restricted local file;
`jarvis permissions request credential_store` retries Keychain access. The local
file is a compatibility fallback, not encrypted Keychain storage.

To revoke access, turn off Personal Jarvis in the matching macOS **Privacy &
Security** pane. The app has no **Revoke** or **Reset all permissions** button.
Revoking stops later use; it does not delete audio, screenshots, action records, or
provider data that already exists.

Grants for the app the installer builds are recorded against a local signing
certificate it creates once (macOS asks for your login password that one time), so
they survive updates. The downloaded disk-image app is signed differently: unless it
carries a stable Apple signature, macOS may treat a new version as a new app.

## Permissions This Page Does Not Cover

### Files and Notifications

There is no general **Files** row, and Jarvis does not require **Full Disk
Access**. File access depends on the operating-system account running Jarvis, the
folder you choose, a browser file picker, a connected service, or a Jarvis-Agent
mission workspace. macOS may separately ask for **Files & Folders** access to
protected locations the first time something touches them. Grant the narrow access
the task needs instead of Full Disk Access.

Notification delivery is outside Jarvis's permission handling: manage it in the
operating system or browser that displays it. A browser can separately ask for
microphone, camera, download, or notification access per site and profile.

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
a live desktop, and an image-capable model. A safety approval cannot create a
missing screen grant.

## Check That It Works

Use the microphone as a small, harmless check:

1. On a Mac where you have not answered the microphone question, press the
   dictation button in the composer. macOS shows its microphone dialog; allow it.
2. Open **System Settings > Privacy & Security > Microphone** and confirm that
   Personal Jarvis is on.
3. Open the wake-word **Microphone check**, activate **Test your microphone**,
   and speak a short phrase. A usable level confirms that both the macOS grant and
   the selected microphone work.

On Windows, Linux, or a browser, run the same microphone check and review that
platform's device or site settings if it cannot capture audio. To verify screen
and input access, use the non-private calculator check in [Computer
Use](computer-use).

The macOS behaviour on this page is not verified on a real Mac yet; see
[Platform Support](platform-support) for the verification record.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A feature says access is off, but no dialog appeared | macOS already recorded your earlier answer and does not ask twice | Use **Open System Settings** on the message and turn Personal Jarvis on, or start over with `jarvis permissions reset` |
| No message appeared, but the feature does nothing | The Jarvis window was hidden when the message was due, or the use was started in the background | Open the main Jarvis window and press the feature again, or switch Personal Jarvis on yourself in **System Settings > Privacy & Security** |
| System Settings shows the switch on, but the feature still fails | The record may belong to an older version of the app | Run `jarvis permissions reset <permission> --yes` with Jarvis closed, reopen it, and use the feature; if that fails, turn the switch off and on, then restart the Mac |
| The message says Jarvis is not running as an installed app | Jarvis was started from a terminal or a copy, so macOS would record access for that program | Open the installed Personal Jarvis app, or choose **Ask macOS now** for the program that started Jarvis |
| The permission dialog is in the wrong language | German and Spanish text needs a system in that language and a new build (an installer's app already on your Mac gets it when next rebuilt); otherwise it is English | Change the system language, or reply to the dialog as it is shown |
| Permissions are asked for again after an update | The new version was signed differently, so macOS treats it as a new app | Answer once more. A build signed with a stable certificate keeps its grants |
| **Quit and reopen** refuses to restart | A Jarvis-Agent mission is still running | Finish or stop the mission, then try again |
| `jarvis permissions status` says `not_required` but the feature is blocked | The host is not macOS, or a browser, device, display session, Wayland, or file boundary owns the failure | Test the feature directly and review the relevant host or browser settings |
| Every permission reads `granted`, but the feature fails | A restart, device, model, connection, account scope, or target application is still unavailable | Follow the dependent feature's troubleshooting steps |

## Next Steps

- Use [First-Run Setup](first-run-setup) to review onboarding and the text-only
  path.
- Read [Audio and Wake Word](audio-and-wake-word) to test microphone access,
  device selection, and wake readiness together.
- Read [Computer Use](computer-use) before granting screen and input control or
  letting Jarvis operate a live desktop.
- Review [Privacy and Local Data](privacy-and-local-data) to understand what a
  granted feature can store locally or send to a connected service.
