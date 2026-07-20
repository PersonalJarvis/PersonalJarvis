---
title: "Voice Conversations"
slug: voice-conversations
summary: "Learn how wake detection, speech recognition, the assistant, and text-to-speech form one voice turn."
section: "Everyday use"
section_order: 2
order: 2
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [voice, microphone, wake-word, speech-recognition, text-to-speech, pipeline, realtime, language]
related: [audio-and-wake-word, languages-and-voices, sessions-and-run-inspector, speech-dictionary]
---

A voice conversation turns spoken audio into a useful reply or action, then
speaks the result back to you. You can start it with a wake phrase, a voice
control in the app, or the configurable Call shortcut.

Jarvis offers two voice engines. **Pipeline** handles listening, answering, and
speaking as separate steps. **Realtime** keeps those parts in one live audio
session. Both lead to the same assistant and safety rules, but they differ in
timing, available features, and how they recover from a problem.

## Before You Start

- Wait until the desktop app shows **Ready** instead of **Voice starting...**.
- Make sure the selected microphone and speaker or headset work.
- Allow microphone access when your operating system asks.
- Open **API Keys & Providers** to review the **Voice engine** choice. Realtime
  can be selected only when a compatible live voice service is available.

You do not need to connect every service shown in the app. Pipeline can use
separate compatible services for speech recognition, the assistant, and voice
output. Realtime needs one compatible live voice connection for the session.

> [!warning] Never speak passwords, provider credentials, recovery codes, or
> other secrets. Enter a required credential only in **API Keys & Providers**.

## How a Voice Turn Works

One **turn** begins when you start speaking and ends when Jarvis finishes the
reply or action. A **session** is the call around those turns; depending on your
voice setup, it can contain one turn or remain open for several.

1. **You activate voice.** A saved wake phrase, the app's voice control, or a
   shortcut opens a listening session. Push-to-talk starts recording while you
   hold the shortcut and sends the turn when you release it.
2. **Jarvis receives microphone audio.** The app shows **Listening**. Wake
   detection only starts the session; it is separate from understanding the
   request that follows.
3. **Your request becomes usable input.** Pipeline waits for the end of your
   phrase and turns the recording into text. Realtime sends audio through the
   live session and receives a final transcript for the turn.
4. **The assistant handles the request.** A question can be answered directly.
   A request that needs an action can use Jarvis tools or hand work to the
   regular assistant. Actions still follow the same permission and confirmation
   rules as text chat.
5. **Jarvis prepares speech output.** Pipeline converts the finished answer
   from text into audio. Realtime normally returns spoken audio through the
   open session. The reply language and selected voice shape what you hear.
6. **The turn closes or listening continues.** The app moves through
   **Thinking** and **Speaking**, then returns to **Listening** or **Ready**.
   The Transcription view groups the recognized request and reply under the
   voice session.

### Pipeline and Realtime Compared

| Question | Pipeline | Realtime |
|---|---|---|
| How does it work? | Speech recognition -> assistant and tools -> speech output | One live audio session listens and replies; actions may use the regular assistant and tools |
| Conversation feel | Clear, separate stages after you finish speaking | More immediate back-and-forth and live interruption |
| Service choices | Input, assistant, and output can use separate compatible choices | Requires a compatible live voice choice |
| Speech Dictionary | Applies to the recognized request | Does not currently rewrite the live session's transcript |
| Call shortcut | Starts a normal voice session through Pipeline | The shortcut uses Pipeline even when Realtime is selected |
| If startup fails | The affected stage can use another configured compatible choice or report that it is unavailable | Jarvis can use another compatible live choice, then fall back to Pipeline before the turn starts |
| Current scope | Broadest feature coverage | Research preview; some tools and features are not yet available |

Choose Pipeline when you want predictable staged processing, independent
service choices, or Dictionary corrections. Choose Realtime when you want a
more conversational audio flow and the features you need are supported. The
best choice depends on the request, not on a particular provider brand.

## How It Fits Together

Voice is a path into the same Jarvis experience you use in Chats. The features
around it each have one distinct job:

| Related feature | What it contributes | What it does not change |
|---|---|---|
| Audio and Wake Word | Chooses the microphone, speaker, wake phrase, and activation behavior | A wake phrase starts listening; it does not answer the request |
| Languages and Voices | Chooses the reply language and the voice you hear | It does not repair microphone input or grant permission |
| Speech Dictionary | Corrects names and specialist words in Pipeline speech recognition | It does not retrain wake detection or change Realtime transcripts |
| Sessions and Run Inspector | Groups voice turns and exposes more detail about work and failures | It does not change how a request is recognized |
| Chats | Provides a text alternative and can supply earlier conversation context when you start voice from an existing chat | It does not require voice to be available |

### Language Across a Turn

Jarvis decides the output language once for each turn so status phrases, the
answer, and the speaking voice agree. A language you explicitly select wins.
In automatic mode, a short interjection keeps the current conversation
language; a longer request can establish a different supported language. If
the input is unclear and there is no established conversation language, Jarvis
uses the app's default language.

Changing the language or voice engine during an active call may reconnect the
session. The new choice then applies consistently instead of changing only one
part of a spoken reply.

### What Happens When Voice Is Unavailable

Fallbacks protect the request without pretending that every path is healthy:

- If a Realtime session cannot open before Jarvis has accepted the turn,
  Jarvis can continue that call through Pipeline.
- If Realtime fails after a request or action has already been accepted, Jarvis
  ends the call instead of replaying the same audio and risking a duplicate
  action. Start a new call or choose Pipeline for the next turn.
- If speech recognition, the assistant, or speech output is unavailable,
  Jarvis can use another configured compatible option where one exists. When
  none is usable, the app reports the unavailable part rather than claiming the
  turn succeeded.
- If no voice path remains, continue in **Chats** while you review audio,
  permissions, or provider status.

## Check That It Works

1. Start a voice session with your wake phrase or the app's voice control.
2. Ask one short question and watch for **Listening**, then **Thinking** or
   **Speaking**.
3. Confirm that you hear a relevant reply and the app returns to **Ready** or
   **Listening**.
4. Open **Transcription** and confirm that the session contains your recognized
   request and the assistant reply.

When testing Realtime, keep the call open long enough to review the runtime
line under **API Keys & Providers > Voice engine**. It shows whether the active
session is Realtime, Pipeline, or a Pipeline fallback.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The wake phrase does nothing | Wake activation, its language, or the microphone is not ready | Run **Test wake word**, then use the app's voice control or Call shortcut while you review the wake setup |
| **Listening** appears, but the request is wrong or empty | The wrong microphone is selected, the level is too low, or speech recognition could not understand the turn | Check **Audio devices**, try one short request, and add repeated Pipeline mistakes to **Dictionary** |
| Realtime is selected, but the runtime line says Pipeline | No compatible live session could open for this call | Review the Realtime category in **API Keys & Providers**; keep using the working Pipeline fallback or choose Pipeline explicitly |
| A Realtime call ends after an error | The live path failed after Jarvis had already accepted part of the turn | Start a fresh call; use Pipeline if the failure repeats so Jarvis does not replay a possible action |
| **Speaking** appears, but there is no sound | The output device, volume, or speech-output service is unavailable | Check **Audio devices** and system volume, then test another configured compatible voice output |
| The reply uses the wrong language | The reply language is pinned differently, or automatic detection had too little context | Review **Languages**, then try a complete sentence in the language you want |

## Next Steps

- Read [Audio and Wake Word](audio-and-wake-word) to choose devices, set up
  activation, and test a wake phrase.
- Read [Languages and Voices](languages-and-voices) to control the language and
  speaking voice used across the whole turn.
- Read [Speech Dictionary](speech-dictionary) to correct recurring Pipeline
  recognition mistakes without changing the wake phrase.
- Read [Sessions and Run Inspector](sessions-and-run-inspector) to understand
  saved voice turns and investigate a request that did not finish as expected.
