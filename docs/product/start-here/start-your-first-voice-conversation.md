---
title: "Start Your First Voice Conversation"
slug: start-your-first-voice-conversation
summary: "Start a voice conversation, follow each listening and reply state, and end the call safely on desktop or in the browser."
section: "Start here"
section_order: 1
order: 6
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [voice, microphone, wake-word, keyboard-shortcut, realtime, pipeline, dictation, tutorial]
related: [voice-conversations, audio-and-wake-word, dictation, languages-and-voices]
---

A voice conversation lets you speak naturally, hear the answer, and continue
with follow-up questions. You can begin with your wake phrase, the Call
shortcut, or the idle Jarvis Bar.

The assistant's displayed name comes from your wake phrase. For example,
`Hey Nova` displays `Nova`; without a saved name, the neutral fallback is
`Assistant`. This guide therefore refers to **your assistant**, whatever name
you chose.

This is different from [Dictation](dictation), which inserts spoken words as
text without asking the Brain or speaking a reply.

## Before You Start

- Open the desktop app and wait for **Voice starting…** to become **Ready**.
  A green **Ready – you can speak now** notice confirms that voice finished
  warming up.
- Connect a microphone and speaker or headset, then check both under
  **Settings > Audio devices**.
- On macOS, allow **Microphone** access. The Call shortcut can also require
  Accessibility or Input Monitoring access. Review
  [Permissions](permissions) if the app reports that access is missing.
- Under **API Keys & Providers**, connect only what your voice engine needs:
  one compatible live provider for Realtime, or usable Voice Input, Brain,
  and Voice Output paths for Pipeline.

> [!warning] Never speak a password, API key, recovery code, or other secret.
> Enter credentials only in **API Keys & Providers**.

## Start Your First Conversation

### 1. Confirm that voice is ready

Look below your assistant's name. When **Voice starting…** becomes **Ready**,
voice is available. Wake users should run **Settings > Wake Word > Test wake
word** once to check the phrase, local detector, language, and microphone.

### 2. Start listening

Choose one method:

- **Wake phrase:** Say the saved phrase, wait for **Listening**, then speak.
  Wake detection opens the session; it does not interpret the request.
- **Call shortcut:** Press the configured keys once. Call also works when wake
  activation is off; it is not push-to-talk.
- **Jarvis Bar:** Select the body of the idle bar.

Change **Call (answer / start talking)** and **Hangup** under
**Settings > Voice Keybinds**. On Wayland, use wake or an in-app control if
global shortcuts are unavailable.

Headless installs have no native audio controls. From a browser on localhost
or HTTPS, use **Start Realtime Voice** when offered and allow the microphone.

### 3. Ask a short question

Wait for **Listening**, then ask a short question at normal volume. Activation
feedback is visual; there is no required chime or spoken greeting.

### 4. Follow the conversation states

The default conversation flow is:

**Ready → Listening → Thinking → Speaking → Listening**

| Status | What it means | What to do |
|---|---|---|
| **Ready** | No call is open | Start when you want |
| **Listening** | The microphone is accepting this turn | Speak a request or follow-up |
| **Thinking** | The assistant is answering or acting | Wait for the result |
| **Speaking** | A reply or progress update is playing | Listen, interrupt, or hang up |
| **Error** | The voice path could not continue | End the call and follow the reported fix |

After an answer, the normal setting returns to **Listening** for a follow-up.
A quiet Pipeline call ends after about 30 seconds with the shipped settings;
Realtime may stay connected longer.

## Choose Realtime or Pipeline

The setting chooses what to try; the runtime status shows what this call uses.

| Engine | How it works | When to choose it |
|---|---|---|
| **Realtime** | One live model listens and answers in the same stream | Recommended for natural, low-delay conversation; some tools remain unavailable during the preview |
| **Pipeline** | Voice Input creates text, the Brain handles it, and Voice Output speaks | Best for separate providers and broad feature support; speech can use connected or local options |

Wake detection stays local and separate. Installed local Pipeline speech
input or output can work without a provider credential, while the Brain still
needs a response path.

If Realtime cannot open, the call can continue as **Pipeline**. Once an action
has started, the call may end instead of replaying it twice.

## Voice Conversation or Dictation?

| Use | Result | Control |
|---|---|---|
| **Voice conversation** | The assistant can answer or act aloud | Wake, Call, or the Jarvis Bar; finish with Hangup |
| **Dictation** | Speech is inserted as text without a reply | Separate Dictation controls |

## End or Cancel the Conversation

Use any one of these controls:

- Say **hang up** or **end the call**.
- Press your configured **Hangup** shortcut.
- Hover over the active Jarvis Bar and select its visible **X** precisely.

Hangup closes the microphone, stops spoken output, and cancels the active turn
or Computer Use action. A longer background agent mission may continue; check
its view if you started one. The sidebar returns to **Ready**.

## How It Fits Together

1. [Audio and Wake Word](audio-and-wake-word) supplies devices and optional
   local activation; Call or the Bar can open the same session.
2. Realtime handles one live stream. Pipeline connects Voice Input, the Brain,
   and Voice Output, with safe startup fallback between them.
3. One language choice controls the answer and voice. A pinned reply language
   wins; **Auto** keeps short interjections in context and can switch after a
   substantive turn. See [Languages and Voices](languages-and-voices).
4. Dictation shares audio and recognition, but stops before the Brain and
   spoken reply. If voice is unavailable, **Chats** remains available.

## Check That It Works

Start one call with your wake phrase, Call shortcut, or the idle Jarvis Bar.
Ask a short question and watch the status move from **Listening** to
**Thinking** or **Speaking**. You should hear the answer and then see
**Listening** again. Use Hangup and confirm that the status returns to
**Ready**.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Voice starting…** never becomes **Ready**, or **Error** appears | Voice is warming, or audio/provider setup is unavailable | Wait briefly; review **Audio devices**, **Permissions**, and **API Keys & Providers** |
| The wake phrase does nothing | Wake is off, its local model or language is wrong, or input is quiet | Run **Test wake word** and use Call or the Bar meanwhile |
| The Call shortcut does nothing | The binding is cleared or global shortcuts are blocked | Review **Voice Keybinds**; check macOS permissions or use an in-app control on Wayland |
| **Listening** captures nothing, or **Speaking** is silent | The selected device is wrong, muted, or quiet | Check both selectors and operating-system levels |
| Realtime becomes **Pipeline** | Realtime could not open safely | Continue with Pipeline or test Realtime under **API Keys & Providers** |
| Browser Realtime is unavailable | The page is not localhost/HTTPS, or microphone access is blocked | Use a secure address, grant access, and retry |

## Next Steps

- Read [Voice Conversations](voice-conversations) for follow-up turns,
  interruptions, tools, and fallback behavior.
- Read [Audio and Wake Word](audio-and-wake-word) to tune devices and local
  activation.
- Read [Dictation](dictation) when you want speech inserted as text without an
  assistant reply.
- Read [Languages and Voices](languages-and-voices) to choose recognition and
  reply languages or the voice you hear.
