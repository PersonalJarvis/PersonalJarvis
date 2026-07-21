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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [settings, appearance, startup, keyboard-shortcuts, jarvis-bar, sound]
related: [desktop-app-tour, audio-and-wake-word, permissions]
---

Use **Settings** to choose how Personal Jarvis starts, sounds, responds to
keyboard shortcuts, and appears during voice activity. Toggles, sliders, and
device choices save when you change them. Wake Word, System Prompt, and Voice
Keybinds each have their own Save button. There is no page-wide Save button.

Most connected controls update a running feature immediately. When live update
is unavailable, Jarvis saves the choice for the next compatible start. The
visible **Autopilot Toasts** switch is an exception: it is not currently
connected to saved behavior.

## Before You Start

- Use the desktop app for login startup, on-screen overlays, and global voice
  shortcuts. A remote browser or headless server cannot control those parts of
  your local desktop.
- Finish or pause important work before selecting **Restart now**. Jarvis
  protects running Jarvis-Agent missions from a normal restart. Forcing a
  restart will stop them.
- Enter provider credentials only under **API Keys**. No setting on this page
  should ask for a credential in chat, voice input, or a keyboard shortcut.

## Find the Right Setting

Open **Settings** from the app sidebar, then scroll to the labeled group you
need. This map separates everyday behavior from the voice and language choices
that have their own guides.

| Group | What you can change | When it applies |
|---|---|---|
| **Languages** | Interface, Voice Recognition Language, and Reply Language | Interface and reply choices apply live. After changing recognition, end any Realtime call and restart Jarvis for Pipeline recognition |
| **App settings** | Launch the app when you sign in | The operating-system startup entry is added or removed immediately |
| **Privacy permissions** | Microphone, Screen Recording, Accessibility, Input Monitoring, Input control, and Keychain access on macOS | Status updates live; Jarvis shows **Restart now** when a new grant needs it |
| **Realtime voice (browser)** | Pipeline or Realtime voice mode | Saves immediately. An active desktop call reconnects when its mode changes; otherwise the next call uses the choice |
| **System Prompt** | The assistant's general style and behavior | On the next message; no restart |
| **Wake Word** | Activation, phrase, spoken language, detection engine, and self-test | Usually live when desktop voice is ready; otherwise on the next voice start |
| **Thinking pause** | End-of-speech timing for Pipeline mode; in Realtime mode the voice model detects the end of your turn itself | Live with Pipeline running; otherwise on the next Pipeline start |
| **Volume** | Local spoken-output volume | Live in running desktop voice; browser-owned Realtime uses browser or system volume |
| **Audio devices** | Local microphone and voice output | Live for devices known to the running app; a newly connected device may need the next app start |
| **Voice Keybinds** | **Call (answer / start talking)** and **Hangup** shortcuts | Live with the desktop voice listener running; otherwise on the next voice start |
| **Autopilot Toasts** | A visible switch with no connected setting | Not connected to persistence or behavior; do not rely on it |
| **Bar & Overlay** | Display style, idle visibility, music muting, and short sound effects | A mix of live changes and restart-required display swaps |

Read [Languages and Voices](languages-and-voices) before changing several
language controls at once. Read [Audio Devices and Wake Word](audio-and-wake-word)
for the complete microphone and wake setup.

The **Which language do you speak?** choice inside Wake Word changes the same
recognition setting shown as **Voice Recognition Language** under Languages.
The Wake Word panel asks for a specific spoken language because its local
detector needs one; changing either control updates the other.

### Realtime Voice and Providers

The **Realtime voice (browser)** switch chooses the voice engine. It does not
choose the provider, model, or speaking voice. Configure those under
[Providers and API Keys](providers-and-api-keys). When the optional Realtime
engine is installed, the current adapters are OpenAI Realtime and Gemini Live.

Jarvis skips installed Realtime providers that have no usable credential and
tries another ready provider. If none is available, Realtime cannot be enabled
or a failed call returns to Pipeline. Turning Realtime off remains available.
The status below the switch shows whether voice is idle, switching, using
Pipeline fallback, or connected to a Realtime provider and model.

## Start Jarvis When You Sign In

1. Under **App settings**, turn on **Launch app at login**.
2. Wait for the success message. Jarvis creates the appropriate login entry for
   the current operating system without requiring an app restart.
3. On Windows, accept the one-time permission prompt if it appears. This lets
   Jarvis create a scheduled login task for fast startup.
4. If that attempt falls back to a startup shortcut, **Enable instant start**
   appears. Select it to retry the permission prompt. If you decline, Jarvis
   still starts through the shortcut, but Windows may delay it on a busy
   machine.

macOS and Linux use their native desktop-login mechanisms. On a headless host,
the switch is disabled because there is no graphical login session to start
into. Use the host's service setup instead of expecting the desktop toggle to
work there.

## Choose the Bar or Overlay

Under **Bar & Overlay > Appearance**, choose one of these display styles:

| Style | What you see | Important limit |
|---|---|---|
| **Bar (default)** | A slim status bar for idle, listening, thinking, and speaking states | Available on a supported graphical desktop |
| **Mascot orb** | The classic on-screen mascot during voice activity | Available on a supported graphical desktop; changing from another visible style normally needs a restart |
| **None (hidden)** | No on-screen voice surface | Hiding the current surface can usually apply live |

1. Select the preview card for the style you want.
2. If the app confirms the switch, the change is already active.
3. If **Restart now** appears, select it after active work finishes. Creating a
   different visible surface safely requires a clean app start.
4. If the restart is refused because missions are running, wait for them to
   finish. A forced restart will stop those runs.

The Bar and Mascot are available on supported Windows, macOS, and Linux
graphical desktops. On a machine without a graphical display, Jarvis saves the
style for a later desktop start but cannot show it immediately.

### Adjust Bar and Sound Behavior

- **Show bar at all times** keeps the Bar visible while Jarvis is idle. Turn it
  off to show the Bar only after voice activation. This applies live when the
  Bar is running.
- **Mute music while dictating** turns down or mutes supported media for the
  full voice session, then restores it when the session ends. Windows handles
  application audio sessions. macOS currently handles Apple Music and Spotify
  and may ask for Automation access. Linux and headless hosts save the choice
  but do not currently mute other applications.
- **Sound effects** controls short cues such as the wake chime, hang-up tone,
  and ready cue. It does not mute Jarvis's spoken replies.

## Set Voice Keyboard Shortcuts

Voice Keybinds let you start or end a desktop voice call. The Settings view
currently exposes exactly two actions: **Call (answer / start talking)** and
**Hangup**. The on-screen keyboard uses PC or Mac modifier labels to match your
device.

1. Find **Voice Keybinds**, then choose **Call (answer / start talking)** or
   **Hangup**.
2. Select **Record** and hold the complete combination. Release every key to
   finish, or select keys on the on-screen keyboard and then select **Stop**.
3. Read the inline validation message. The on-screen keyboard also marks a
   physically pressed key, a key in the new combination, and a key already used
   by the other action. These markers are optional visual aids.
4. Select **Save**. If Jarvis shows a restart message, restart after important
   work has finished; otherwise the running voice listener is already updated.

Press **Escape** while recording to restore the previously saved combination.
**Reset to default** places the default in the field; select **Save** to apply
it. **Clear** saves the removal immediately, without another Save step.

You can complete this flow without a mouse. Use Tab to reach **Record**,
**Save**, or **Clear**, press Enter or Space to activate a button, enter the
combination, and press Escape to cancel recording.

Jarvis blocks combinations that would be unsafe or ambiguous. A modifier by
itself is incomplete, ordinary typing keys need a modifier or second key, and
two actions cannot use overlapping combinations. The system or Command key,
Alt+F4, and Ctrl+C are reserved. Function keys can be used alone; navigation
keys are allowed but warn you that they also fire while editing text.

## How It Fits Together

1. You change a control in **Settings**. The desktop interface sends that
   single choice to this Jarvis installation and shows the result.
2. For a connected control, Jarvis tries to store the choice for future starts
   and asks the running feature to adopt it. **Autopilot Toasts** is the visible
   exception because its switch is not connected yet.
3. [Audio Devices and Wake Word](audio-and-wake-word) supplies the local
   microphone, speaker, activation phrase, and spoken-language setting. Voice
   keybinds provide another way to start the desktop voice path.
4. [Languages and Voices](languages-and-voices) controls recognition and reply
   language. Thinking pause affects Pipeline only. Local Volume and Audio
   device controls cover desktop voice, while browser-owned Realtime uses the
   browser's audio devices and volume.
5. [Permissions](permissions) decides whether macOS allows microphone input,
   global shortcuts, Keychain storage, screen capture, and computer control. A
   saved preference cannot override an operating-system denial.
6. [Providers and API Keys](providers-and-api-keys) supplies the credential,
   model, and voice for Realtime. Jarvis can try another installed, ready
   Realtime provider before returning a failed call to Pipeline.
7. If a live capability is unavailable, Jarvis keeps a saved preference for the
   next compatible start. Platform-specific features can remain unavailable
   without stopping Chats or other text-based work.

## Check That It Works

On a graphical desktop with **Bar (default)** selected, wait until Jarvis is
idle. Turn **Show bar at all times** off and confirm that the idle Bar hides;
turn it on and confirm that the Bar returns. If the app reports that the change
needs a restart, restart when no mission is running and repeat the check.

For a shortcut check, save an unused **Call (answer / start talking)**
combination. Press and release it, then confirm that the Bar changes to a
listening state. Use the saved **Hangup** shortcut and confirm that Jarvis
returns to idle.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A display choice says a restart is required | The new Bar or Mascot surface cannot be created safely during the current run | Finish active missions, select **Restart now**, and check the chosen style after the app returns |
| Restart is refused | One or more Jarvis-Agent missions are still running | Wait for them to finish; force only if stopping them is acceptable |
| **Launch app at login** is unavailable | The current host has no supported graphical login session | Use the desktop app on a supported host; a headless system needs its own service setup |
| **Realtime voice (browser)** is unavailable | No installed Realtime provider has a usable credential, or this build does not include the optional Realtime engine | Connect OpenAI Realtime or Gemini Live under **API Keys**. If the control stays unavailable, use Pipeline |
| Music keeps playing after you enable muting | The platform backend is unavailable, macOS Automation access was denied, or the media app is unsupported | Accept the macOS Automation prompt when offered. On Linux or for unsupported players, use the operating system or media app controls |
| **Save** stays disabled for a shortcut | The combination is incomplete, reserved, or overlaps another action | Follow the inline message and the on-screen keyboard markers, then choose a distinct combination |
| A macOS feature remains blocked | The operating system has not granted the required access, or the new grant needs an app restart | Use **Settings > Privacy permissions > Allow**, **Open Settings**, or **Ask again**, then select **Restart now** when shown |
| A change works now but returns after restart | The live update succeeded, but the saved preference could not be written | Try the control again, then use [Troubleshooting](troubleshooting) if it still does not survive a restart |
| **Autopilot Toasts** has no effect | The visible switch is not currently connected to a saved preference | Do not rely on this control until a future app update wires it to behavior |

## Next Steps

- Use [Providers and API Keys](providers-and-api-keys) to connect and choose a
  Realtime provider, model, and speaking voice.
- Use [Audio Devices and Wake Word](audio-and-wake-word) to set up the
  microphone, speaker, activation phrase, and wake self-test.
- Read [Permissions](permissions) before enabling microphone or computer-control
  features that depend on operating-system approval.
- Open [Languages and Voices](languages-and-voices) to choose interface,
  recognition, reply, and speaking-voice behavior separately.
