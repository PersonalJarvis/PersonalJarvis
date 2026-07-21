---
title: "Start Your First Voice Conversation"
slug: start-your-first-voice-conversation
summary: "Check the microphone, wake Jarvis, ask a question, and know when the app is listening or speaking."
section: "Start here"
section_order: 1
order: 6
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [voice, microphone, wake-word, keyboard-shortcut, realtime, tutorial]
related: [voice-conversations, audio-and-wake-word, languages-and-voices, permissions]
---

Start a voice session with your wake word, the Call shortcut, or the idle
Jarvis Bar. Then ask a question and listen for the reply. The status below your
assistant's name shows when voice is ready, listening, thinking, or speaking.

This tutorial is for the desktop app. A headless installation has no local
microphone or Jarvis Bar, but you can still use **Chats** or a connected
channel.

## Before You Start

- Open the desktop app and wait for **Voice starting…** to change to
  **Ready**.
- Connect a microphone and a speaker or headset.
- Finish first-run setup. On macOS, grant **Microphone** access when the app
  asks for it. If access is still missing, open **Settings > Permissions**.
- Set up the services required by your voice mode under **API Keys**. Realtime
  needs a compatible Realtime provider. Pipeline needs working Voice Input,
  Brain, and Voice Output paths. You do not need to configure every provider.

> [!warning] Never say a password, API key, recovery code, or other secret.
> Enter provider credentials only in **API Keys & Providers**.

## Start Your First Conversation

### 1. Check your audio

If you chose a wake word during first-run setup, select **Test your
microphone**, speak during the check, and look for **Sounds good**.

After setup, open **Settings > Audio devices**. Select your microphone and
output device, or keep **Automatic (recommended)**. If you use a wake word,
also open **Settings > Wake Word**, select **Test wake word**, and speak during
the check. The result reports whether the saved phrase, detection engine,
recognition language, and microphone signal are ready.

### 2. Start listening

Use one of these methods:

- **Wake word:** Say your saved phrase. Start your request when the sidebar
  says **Listening** and the Jarvis Bar becomes active.
- **Call shortcut:** Press the configured Call keys once. The shortcut starts a
  normal voice session even when wake-word activation is off.
- **Jarvis Bar:** Select the body of the idle bar to start a session.

**Settings > Voice Keybinds** contains the editable **Call** and **Hangup**
shortcuts. Call is a one-press action, not push-to-talk. A global shortcut may
be unavailable on Wayland or another desktop without global-hotkey support; in
that case, use the wake word or Jarvis Bar.

### 3. Ask one short question

Wait for **Listening**, then speak at a normal volume. For this first check,
ask something that needs only a short answer.

### 4. Follow the turn

With the default conversation setting, a normal turn looks like this:

**Ready → Listening → Thinking → Speaking → Listening**

| Status | What it means | What to do |
|---|---|---|
| **Ready** | Voice is available, but no session is open | Start a session when you want |
| **Listening** | The microphone is open for the current session | Speak your request or a follow-up |
| **Thinking** | The assistant is preparing an answer or working on an action | Wait for the result |
| **Speaking** | A spoken reply or update is playing | Listen, interrupt, or end the session |
| **Error** | The current voice path could not continue | End the session and review the reported problem |

A quick Realtime answer may pass through **Thinking** very briefly. A longer
action can move between **Thinking** and **Speaking** while the assistant gives
an update and continues working.

### 5. End the conversation

After a reply, the default conversation setting returns to **Listening** so
you can ask a follow-up without waking the assistant again. In Pipeline mode,
the shipped idle timeout ends a quiet session after 30 seconds. A Realtime
connection can remain open longer, so end it explicitly when you are done.

To end it sooner, use any one of these controls:

- Say **hang up** or another clear closing command.
- Press your configured **Hangup** shortcut.
- Hover over the active Jarvis Bar and select the visible **X**.

If you enabled single-turn behavior, the session returns to **Ready** after
each reply instead.

## How It Fits Together

1. [Audio and Wake Word](audio-and-wake-word) supplies the microphone,
   speaker, and activation method. [Permissions](permissions) explains the
   operating-system access that may be required.
2. Pipeline turns microphone audio into text, sends the text to the Brain, and
   plays the answer through a Voice Output provider. Realtime uses one live
   speech-to-speech connection and can delegate longer actions to the Brain.
3. Both modes feed the same sidebar states, so **Listening**, **Thinking**, and
   **Speaking** keep the same meaning when the voice engine changes.
4. **Settings > Languages** separates interface language, speech-recognition
   language, and reply language. English, German, and Spanish are supported
   choices. **Auto** follows the current substantive spoken turn and the
   conversation language; if no language signal is available, the verified
   fallback is English. [Languages and Voices](languages-and-voices) explains
   these choices and the provider voice you hear.
5. Realtime tries its available provider chain. If no Realtime provider can
   open before a turn is committed, the desktop session changes to Pipeline.
   A failure after a request or action has already been committed may end the
   session instead of replaying it and risking a duplicate action. If no usable
   voice path remains, continue in **Chats** and review audio, permissions, and
   provider status.

Read [Voice Conversations](voice-conversations) for a fuller explanation of
how listening, the Brain, actions, and spoken output form one turn.

## Check That It Works

Start a session with your wake word, Call shortcut, or the idle Jarvis Bar. Ask
a short question. Voice is working when the status changes from **Listening**
to **Thinking** or **Speaking**, you hear a reply, and it returns to
**Listening**. End the session with the Hangup shortcut and confirm that the
status returns to **Ready**.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Voice starting…** does not change, or the status becomes **Error** | The voice stack is still warming or a required audio or provider path is unavailable | Wait briefly, then review **Audio devices**, **Permissions**, and provider status under **API Keys** |
| The wake word does nothing | Wake activation is off, its local model is missing, the recognition language is wrong, or the microphone signal is too quiet | Run **Test wake word**, follow its reported fix, and use Call or the idle Jarvis Bar meanwhile |
| The Call shortcut does nothing | The binding is cleared or the desktop cannot register a global hotkey | Review **Settings > Voice Keybinds** and start the session from the idle Jarvis Bar |
| **Listening** appears but no useful words are captured, or **Speaking** appears without sound | The selected input or output device is wrong, muted, or too quiet | Check both selectors under **Settings > Audio devices**, then check the operating-system input and output levels |
| Realtime is unavailable | No configured Realtime provider could open a live session | Let the startup fallback use Pipeline when it is available; otherwise use **Chats** and test the selected provider under **API Keys** |

## Next Steps

- Read [Voice Conversations](voice-conversations) to understand follow-up turns
  and the difference between Realtime and Pipeline.
- Read [Audio and Wake Word](audio-and-wake-word) to choose devices, check a
  wake phrase, or use the Call shortcut.
- Read [Languages and Voices](languages-and-voices) to choose recognition and
  reply languages and the voice you hear.
- Read [Permissions](permissions) when the operating system blocks microphone
  access or another desktop capability.
