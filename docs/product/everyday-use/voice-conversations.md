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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [voice, microphone, wake-word, speech-recognition, text-to-speech, pipeline, realtime, language]
related: [audio-and-wake-word, languages-and-voices, sessions-and-run-inspector, speech-dictionary]
---

A voice conversation turns what you say into a reply or action and plays the
answer aloud. On a desktop, you can start one with a wake phrase, the voice
control in the app, or the configurable Call shortcut.

Jarvis offers two voice engines. **Pipeline** handles listening, answering, and
speaking as separate steps. **Realtime** keeps those parts in one live audio
session. Both use the same permission and confirmation rules. Realtime can
delegate action requests to the regular assistant, but it does not yet support
every Pipeline feature.

Realtime is the recommended default for a fresh configuration. If no compatible
Realtime connection is ready, Jarvis uses Pipeline instead.

## Before You Start

- Wait until the desktop app shows **Ready** instead of **Voice starting...**.
- Make sure the selected microphone and speaker or headset work.
- On macOS, open **Settings > Privacy permissions > macOS permissions** and
  allow **Microphone**. The Call shortcut also needs **Accessibility** and
  **Input Monitoring**. Windows and Linux do not have this extra Jarvis
  permission panel, but the audio devices still need to be available to the
  app.
- Open **API Keys & Providers** to review the **Voice engine** choice. Realtime
  can be selected explicitly only when a compatible live voice service is
  available.

You do not need to connect every service shown in the app. Pipeline can use
separate compatible services for speech recognition, the assistant, and voice
output. Realtime needs one compatible live voice connection for the session.

On a headless server, local desktop audio, wake detection, and desktop
shortcuts are unavailable. Open the web app over HTTPS or on localhost, select
Realtime, and use **Start Realtime Voice** in the sidebar. The browser then owns
the microphone and speaker and asks for its own microphone permission.

> [!warning] Never speak passwords, provider credentials, recovery codes, or
> other secrets. Enter a required credential only in **API Keys & Providers**.

## How a Voice Turn Works

One **turn** begins when you start speaking and ends when Jarvis finishes the
reply or action. A **session** is the call around those turns. Conversation mode
keeps the call open for another turn until you hang up or it reaches its idle
timeout. Single-turn mode ends the session after each answer.

1. **You activate voice.** A saved wake phrase, the app's voice control, or the
   Call shortcut opens a listening session. A headless installation instead
   uses the browser's **Start Realtime Voice** control.
2. **Jarvis receives microphone audio.** The app shows **Listening**. Wake
   detection only starts the session; it is separate from understanding the
   request that follows.
3. **Your request becomes usable input.** Pipeline waits for the end of your
   phrase and turns the recording into text. Realtime sends audio through the
   live session and receives transcript events when the service supplies them.
4. **The assistant handles the request.** A question can be answered directly.
   A request that needs an action can use supported tools or be delegated to the
   regular assistant. Actions follow the same permission and confirmation rules
   as text chat.
5. **Jarvis prepares speech output.** Pipeline converts the answer from text to
   audio with the selected speech-output service. Realtime normally returns
   spoken audio through the open live session.
6. **The turn closes or listening continues.** The app moves through
   **Thinking** and **Speaking**, then returns to **Listening** or **Ready**.
   **Transcription** saves the user and assistant text that the active services
   returned. If a service did not return a transcript, that missing speech
   cannot be reconstructed in the view.

### Pipeline and Realtime Compared

| Question | Pipeline | Realtime |
|---|---|---|
| How does it work? | Speech recognition finishes, then the assistant handles the request, then speech output plays | One live audio session listens and replies; supported actions may use the regular assistant and tools |
| Conversation feel | Separate stages after you finish speaking; speaking over a reply can stop playback | Lower-latency back-and-forth; browser voice uses echo cancellation, while desktop interruption is checked locally to avoid speaker echo |
| Service choices | Input, assistant, and output can use separate compatible choices | Requires a compatible live voice choice |
| Speech Dictionary | Applies to the recognized request | Does not currently rewrite the live session's transcript |
| Call shortcut | Starts Pipeline when Pipeline is selected | Starts Realtime when Realtime is selected |
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

Jarvis supports English, German, and Spanish for reply-language selection. It
decides the output language once for each turn so status phrases, the answer,
and the speaking voice agree. A reply language you explicitly select wins. In
**Auto**, a short interjection keeps the current conversation language, while a
complete request can switch it. If there is no language pin, useful input, or
established conversation language, the reply defaults to English.

Changing the voice engine during an active desktop call closes and reopens the
session with the selected engine. A reply-language change affects the next
resolved turn. Realtime updates an active provider when it can; a provider that
fixes its instructions when the call opens needs a new session. The current
turn still uses one resolved language throughout.

Pipeline and Realtime store their speaking-voice choices with their respective
providers. Switching the engine or falling back to another provider can change
how the assistant sounds without changing the reply-language policy.

### What Happens When Voice Is Unavailable

Fallbacks protect the request without pretending that every path is healthy:

- If a Realtime session cannot open before Jarvis has accepted the turn,
  Jarvis can continue through Pipeline without replaying an action.
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
4. Open **Transcription** and confirm that the session contains the available
   request and assistant transcripts.

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
| **Start Realtime Voice** is unavailable in a remote browser | The page is not on HTTPS or localhost, the browser lacks the required audio feature, or no Realtime key is ready | Use HTTPS, allow microphone access in the browser, and test the active Realtime service in **API Keys & Providers** |

## Next Steps

- Read [Audio and Wake Word](audio-and-wake-word) to choose devices, set up
  activation, and test a wake phrase.
- Read [Languages and Voices](languages-and-voices) to control the language and
  speaking voice used across the whole turn.
- Read [Speech Dictionary](speech-dictionary) to correct recurring Pipeline
  recognition mistakes without changing the wake phrase.
- Read [Sessions and Run Inspector](sessions-and-run-inspector) to understand
  saved voice turns and investigate a request that did not finish as expected.
