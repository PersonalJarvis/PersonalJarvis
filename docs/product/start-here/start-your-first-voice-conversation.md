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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [voice, microphone, wake-word, keyboard-shortcut, realtime, tutorial]
related: [voice-conversations, audio-and-wake-word, languages-and-voices, permissions]
---

Your first voice conversation can be one short turn: start listening, speak,
and hear the reply. The status below your assistant's name shows what Jarvis
is doing throughout the turn.

Voice is optional. If this computer cannot use a microphone or a compatible
voice service, you can continue in **Chats** while you review the voice setup.

## Before You Start

- Open the desktop app and wait for **Voice starting…** to change to
  **Ready**.
- Make sure a microphone and a speaker or headset are available.
- Complete first-run setup, including any microphone permission your operating
  system requests.
- Connect only the services needed by your chosen voice mode. You do not need
  to connect every service shown in the app.

> [!warning] Never speak passwords, provider credentials, recovery codes, or
> other secrets. Enter a required credential only in **API Keys & Providers**.

## Start Your First Conversation

### 1. Check the microphone

During first-run setup, select **Test your microphone**, speak normally, and
wait for **Sounds good**.

If setup is already complete, open **Settings > Audio devices**. Choose your
microphone or keep **Automatic (recommended)**. If you use a wake phrase, also
open **Settings > Wake Word** and select **Test wake word**. That check confirms
that the phrase, language, wake engine, and microphone are ready together.

### 2. Start listening

Use the activation method you chose during setup:

- **Wake phrase:** Say your saved phrase. Begin your request when the status
  changes to **Listening** and the Jarvis Bar becomes active.
- **Talk / Push-to-talk:** Hold your configured shortcut, speak, and release
  the keys to send the request. The shortcut works without a wake phrase.

You can review or change the **Call**, **Hangup**, and **Talk / Push-to-talk**
shortcuts under **Settings > Voice Keybinds**.

### 3. Ask one short question

Speak at a normal volume and finish the request before waiting for the answer.
For this first check, choose something that needs only a short spoken reply.

### 4. Follow the turn

The normal flow is:

**Ready → Listening → Thinking → Speaking → Ready**

| Status | What it means | What to do |
|---|---|---|
| **Listening** | The microphone is open for this voice turn | Speak your request |
| **Thinking** | Jarvis is preparing the answer or completing an action | Wait for the result |
| **Speaking** | The spoken reply is playing | Listen, or end the session if needed |
| **Ready** | The voice session has ended | Start another turn when you want |

A longer request may move between **Thinking** and **Speaking** more than once
while Jarvis gives a brief update and continues the work.

### 5. End the conversation

The default voice setup ends the session after the reply. If your setup keeps
the conversation open, use any one of these controls:

- Say a clear closing command such as **hang up**.
- Press your configured **Hangup** shortcut.
- Hover over the active Jarvis Bar and select the **X**.

The bar returns to its quiet state and the sidebar shows **Ready**.

## How It Fits Together

1. [Audio and Wake Word](audio-and-wake-word) supplies the microphone choice
   and the activation method. [Permissions](permissions) controls whether the
   operating system lets Jarvis hear the microphone.
2. In Pipeline mode, voice input becomes text, the Brain prepares the answer,
   and voice output speaks it. In Realtime mode, one live audio session listens
   and replies, while longer actions can still use the same Brain.
3. Both modes publish the same **Listening**, **Thinking**, and **Speaking**
   states, so the app remains understandable even when the voice engine changes.
4. [Languages and Voices](languages-and-voices) decides the reply language and
   speaking voice. It does not replace microphone or provider setup.
5. If Realtime cannot open before the turn begins, Jarvis tries the classic
   Pipeline path. If no usable voice path remains, use **Chats** and review the
   microphone, permission, and provider status instead of repeatedly speaking.

Read [Voice Conversations](voice-conversations) for a fuller explanation of
how listening, the Brain, actions, and spoken output form one turn.

## Check That It Works

Start a voice turn with your saved wake phrase or Call shortcut. Ask a
short question. Voice is working when you see **Listening**, then **Thinking**
or **Speaking**, hear a reply, and finally see **Ready** again.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Voice starting…** does not change | The voice stack is still warming or did not become available | Wait briefly, then review microphone permission and **Audio devices** |
| The wake phrase does nothing | Wake activation, language, model, or microphone readiness does not match | Run **Test wake word** and use the Call shortcut while you fix the reported item |
| **Listening** appears, but no useful request is heard | The wrong microphone is selected, its level is low, or speech recognition is unavailable | Select the correct input, repeat the microphone check, then try one short request |
| **Speaking** appears, but you hear nothing | The output device or system volume is wrong | Review **Settings > Audio devices** and the operating-system output volume |
| Realtime reports that it is unavailable | The live voice connection could not open | Let Jarvis try Pipeline mode; if voice still fails, use **Chats** and review provider status |
| The status changes to **Error** | The turn could not continue safely | End the session, correct the reported setup problem, and try again |

## Next Steps

- Read [Voice Conversations](voice-conversations) to understand the complete
  voice turn and how Realtime and Pipeline mode relate.
- Read [Audio and Wake Word](audio-and-wake-word) to choose devices, tune a wake
  phrase, or rely on the Call shortcut.
- Read [Languages and Voices](languages-and-voices) to choose the reply language
  and the voice you hear.
- Read [Permissions](permissions) when the operating system blocks microphone
  access or another desktop capability.
