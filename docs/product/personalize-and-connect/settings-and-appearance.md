---
title: "Settings and Appearance"
slug: settings-and-appearance
summary: "Adjust startup, sound, keyboard shortcuts, the Jarvis Bar, overlays, and other everyday behavior."
section: "Personalize and connect"
section_order: 3
order: 4
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [settings, appearance, startup, keyboard-shortcuts, jarvis-bar, sound]
related: [desktop-app-tour, audio-and-wake-word, permissions]
---

Use **Settings** to choose how Personal Jarvis starts, sounds, responds to
keyboard shortcuts, and appears during voice activity. Each control saves its
own change, so there is no page-wide Save button.

Most changes take effect immediately when the matching desktop feature is
running. If it is not available, Jarvis keeps the choice for the next start and
shows a restart or next-start message where the control supports one.

## Before You Start

- Use the desktop app for login startup, on-screen overlays, and global voice
  shortcuts. A remote browser or headless server cannot control those parts of
  your local desktop.
- Finish or pause important work before selecting **Restart now**. Jarvis
  protects running Jarvis-Agent missions from a normal restart; forcing a
  restart can stop them.
- Enter provider credentials only under **API Keys**. No setting on this page
  should ask for a credential in chat, voice input, or a keyboard shortcut.

## Find the Right Setting

Open **Settings** from the app sidebar, then scroll to the labeled group you
need. This map separates everyday behavior from the voice and language choices
that have their own guides.

| Group | What you can change | When it applies |
|---|---|---|
| **Languages** | Interface, speech-recognition, and reply language | Interface and reply choices apply live; a running voice session may reconnect or need a new start |
| **App settings** | Launch the app when you sign in | The operating-system startup entry is added or removed immediately |
| **Privacy permissions** | Microphone, screen, accessibility, and input access on macOS | Permission changes may require **Restart now** |
| **Wake Word** | Activation, phrase, spoken language, and detection engine | Usually live when desktop voice is ready; otherwise on the next voice start |
| **Thinking pause**, **Volume**, and **Audio devices** | End-of-speech timing, spoken volume, microphone, and speaker | Live with the Pipeline running; otherwise on the next start |
| **Voice Keybinds** | Call, Hangup, and Talk or push-to-talk shortcuts | Live with the voice Pipeline running; otherwise after a restart |
| **System Prompt** | The assistant's general style and behavior | On the next message; no restart |
| **Bar & Overlay** | Display style, idle visibility, music muting, and short sound effects | A mix of live changes and restart-required display swaps |

Read [Languages and Voices](languages-and-voices) before changing several
language controls at once. Read [Audio Devices and Wake Word](audio-and-wake-word)
for the complete microphone and wake setup.

## Start Jarvis When You Sign In

1. Under **App settings**, turn on **Launch app at login**.
2. Wait for the success message. Jarvis creates the appropriate login entry for
   the current operating system without requiring an app restart.
3. On Windows, select **Enable instant start** when it appears. Windows asks for
   permission once so Jarvis can use a scheduled login task.
4. If you decline that Windows prompt, Jarvis keeps a startup-shortcut fallback.
   It still starts automatically, but Windows may delay it on a busy machine.

macOS and Linux use their native desktop-login mechanisms. On a headless host,
the switch is disabled because there is no graphical login session to start
into. Use the host's service setup instead of expecting the desktop toggle to
work there.

## Choose the Bar or Overlay

Under **Bar & Overlay > Appearance**, choose one of these display styles:

| Style | What you see | Important limit |
|---|---|---|
| **Bar (default)** | A slim status bar for idle, listening, thinking, and speaking states | Available on a supported graphical desktop |
| **Mascot orb** | The classic on-screen mascot during voice activity | Not available on macOS; a change from another real style normally needs a restart |
| **None (hidden)** | No on-screen voice surface | Hiding the current surface can usually apply live |

1. Select the preview card for the style you want.
2. If the app confirms the switch, the change is already active.
3. If **Restart now** appears, select it after active work finishes. Creating a
   different real overlay safely requires a clean app start.
4. If the restart is refused because missions are running, wait for them to
   finish. Use a forced restart only when you accept that those runs will stop.

On macOS, the Jarvis Bar runs in a separate desktop companion process, while
the Mascot option falls back to no visible surface. On a machine without a
display, all styles are saved for a later graphical start but cannot appear
immediately.

### Adjust Bar and Sound Behavior

- **Show bar at all times** keeps the Bar visible while Jarvis is idle. Turn it
  off to show the Bar only after voice activation. This applies live when the
  Bar is running.
- **Mute music while dictating** mutes other audio sessions while you speak and
  restores them afterward. The current audio-session implementation is Windows
  only; on other systems the choice is saved but does not mute other apps.
- **Sound effects** controls short cues such as the wake chime, hang-up tone,
  and ready cue. It does not mute Jarvis's spoken replies.

## Set Voice Keyboard Shortcuts

Voice Keybinds let you start a call, end it, or hold a key combination while
speaking. The on-screen keyboard uses PC or Mac modifier labels to match your
device.

1. Find **Voice Keybinds**, then choose the row for **Call**, **Hangup**, or
   **Talk / Push-to-talk**.
2. Select **Record** and hold the complete combination. Release every key to
   finish, or select keys on the on-screen keyboard and then select **Stop**.
3. Check the markers: a filled key is physically pressed, an outlined key is in
   the new combination, and an amber dot means another action already uses it.
4. Select **Save**. If Jarvis shows a restart message, restart after important
   work has finished; otherwise the running voice listener is already updated.

Press **Escape** while recording to restore the previously saved combination.
**Reset to default** places the default in the field; select **Save** to apply
it. **Clear** immediately removes the current shortcut for that action.

Jarvis blocks combinations that would be unsafe or ambiguous. A modifier by
itself is incomplete, ordinary typing keys need a modifier or second key, and
two actions cannot use overlapping combinations. The system or Command key,
Alt+F4, and Ctrl+C are reserved. Function keys can be used alone; navigation
keys are allowed but warn you that they also fire while editing text.

## How It Fits Together

1. You change a control in **Settings**. The desktop interface sends that
   single choice to the local Jarvis app and shows the saved result.
2. Jarvis stores the choice for future starts and asks the running feature to
   adopt it. A voice shortcut updates the voice trigger, while Bar choices
   update the surface that displays voice state.
3. [Audio Devices and Wake Word](audio-and-wake-word) supplies the microphone,
   speaker, and activation phrase. Voice keybinds provide an alternative way to
   start the same voice path.
4. [Languages and Voices](languages-and-voices) decides how speech is
   recognized and how replies sound. Appearance settings only change what you
   see and hear around that conversation; they do not change its language.
5. [Permissions](permissions) decides whether the operating system allows the
   microphone, overlay-related capture, or computer control. A saved preference
   cannot override an operating-system denial.
6. If a live capability is unavailable, Jarvis keeps the preference and uses it
   on the next compatible start. Platform-specific features degrade without
   stopping Chats or other text-based work.

## Check That It Works

On a graphical desktop with **Bar (default)** selected, wait until Jarvis is
idle. Turn **Show bar at all times** off and confirm that the idle Bar hides;
turn it on and confirm that the Bar returns. If the app reports that the change
needs a restart, restart when no mission is running and repeat the check.

For a shortcut check, save an unused **Talk / Push-to-talk** combination, hold
it, and confirm that the Bar changes to a listening state. Release it and
confirm that Jarvis sends the spoken turn.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A display choice says a restart is required | The new Bar or Mascot surface cannot be created safely during the current run | Finish active missions, select **Restart now**, and check the chosen style after the app returns |
| Restart is refused | One or more Jarvis-Agent missions are still running | Wait for them to finish; force only if stopping them is acceptable |
| **Launch app at login** is unavailable | The current host has no supported graphical login session | Use the desktop app on a supported host; a headless system needs its own service setup |
| Music keeps playing after you enable muting | The current muting feature is unavailable, or the platform is not Windows | Use the operating system or media app controls; the setting cannot provide cross-platform muting yet |
| **Save** stays disabled for a shortcut | The combination is incomplete, reserved, or overlaps another action | Follow the inline message and the on-screen keyboard markers, then choose a distinct combination |
| A macOS feature remains blocked | The operating system has not granted the required access, or it requires an app restart | Use **Settings > Privacy permissions > Request** or **Open Settings**, then select **Restart now** when shown |
| A change works now but returns after restart | The live update succeeded, but the saved preference could not be written | Try the control again, then use [Troubleshooting](troubleshooting) if it still does not survive a restart |
| **Autopilot Toasts** has no effect | The visible switch is not currently connected to a saved preference | Do not rely on this control until a future app update wires it to behavior |

## Next Steps

- Read [Tour the Desktop App](desktop-app-tour) to see how Settings relates to
  Chats, Jarvis-Agents, history, and the rest of the sidebar.
- Use [Audio Devices and Wake Word](audio-and-wake-word) to set up the
  microphone, speaker, activation phrase, and wake self-test.
- Read [Permissions](permissions) before enabling microphone or computer-control
  features that depend on operating-system approval.
- Open [Languages and Voices](languages-and-voices) to choose interface,
  recognition, reply, and speaking-voice behavior separately.
