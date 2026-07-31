---
title: "Voice Conversations"
slug: voice-conversations
summary: "Understand how Pipeline and Realtime handle spoken questions, actions, privacy, interruptions, and fallback."
section: "Everyday use"
section_order: 2
order: 2
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [voice, microphone, wake-word, speech-recognition, text-to-speech, pipeline, realtime, language]
related: [audio-and-wake-word, dictation, local-ai-providers, sessions-and-run-inspector]
---

A voice conversation turns speech into a request for your assistant. It can
answer aloud, use a supported tool, or ask before an action that needs your
approval. The normal conversation stays open for follow-up turns until you
hang up or it reaches its quiet-time limit.

This is different from [Dictation](dictation). Dictation delivers your words
as text to the field where you are typing; it does not ask the assistant to
reason, act, or speak a reply.

## Before You Start

- Wait for **Voice starting…** to become **Ready**.
- Choose a working microphone and speaker under **Settings > Audio devices**.
- On macOS, allow **Microphone** access. The Call shortcut can also need
  Accessibility and Input Monitoring access. Other systems use their own audio
  and shortcut permissions.
- Open **API Keys & Providers** and review **Voice engine**. Realtime needs one
  compatible live voice connection. Pipeline needs usable Voice Input, Brain,
  and Voice Output paths, which can be local, hosted, or mixed.

On a headless computer, native wake detection, desktop shortcuts, microphone,
and speaker output are unavailable. A browser can offer **Start Realtime
Voice** when the page is on localhost or HTTPS and microphone permission is
allowed.

> [!warning] Never speak passwords, API keys, recovery codes, or other
> secrets. Enter a required credential only in **API Keys & Providers**.

## How a Voice Conversation Works

A **turn** is one request and its result. A **session** is the call that can
contain several turns.

1. **Start the session.** Say your wake phrase, use the idle voice control, or
   press the configured **Call (answer / start talking)** shortcut. Wake
   detection opens the microphone; it does not answer or transcribe the
   request itself.
2. **Speak while the app shows Listening.** Pipeline waits for a pause long
   enough to mark the end of your request. Realtime lets the live voice model
   detect that boundary. The Pipeline **Thinking pause** setting therefore
   does not control Realtime.
3. **Jarvis prepares one understood request.** Pipeline converts the captured
   audio into text first. Realtime receives transcript updates from the live
   connection. Your Speech Dictionary corrects known names and terms in both
   modes before routing and session history use the text.
4. **Jarvis chooses the path.** An ordinary question can be answered directly.
   A request that needs an app, connected service, Computer Use, or longer work
   goes through the regular planning and Tool Model path. Realtime can hand an
   action request to that path instead of pretending the live voice model has
   every tool.
5. **Safety rules check an action.** Low-risk work may continue immediately.
   An ask-level action pauses for a clear yes or no, and a blocked action does
   not run. Review the exact target and effect before saying yes.
6. **Hear the result.** Pipeline sends reply text to Voice Output. Realtime
   normally returns speech through the open live connection. The visible flow
   is usually **Listening → Thinking → Speaking → Listening**.
7. **Continue or hang up.** Conversation mode listens for another turn. An
   optional single-turn setting ends the session after each answer. Silence can
   also end a conversation when an idle limit is enabled.

### Choose Pipeline or Realtime

| Question | Pipeline | Realtime |
|---|---|---|
| How is speech handled? | Voice Input creates text, the Brain handles it, and Voice Output speaks | One live model listens and answers within an audio session |
| What can you choose? | Separate compatible providers for input, reasoning, and output | One compatible Realtime provider and model |
| Can speech run locally? | Yes. Local recognition and Piper voice output are available on supported computers | No local Realtime engine is currently offered; live audio goes to the connected service |
| How do actions work? | Broadest feature and tool coverage | Ordinary conversation stays live; supported action requests use the regular planner and Tool Model |
| How does interruption work? | Sustained user speech can stop playback | Browser audio uses echo cancellation; desktop interruption is checked locally to avoid reacting to speaker echo |
| What happens on failure? | An unavailable stage can use another ready compatible choice or report the missing stage | Another ready Realtime family can be tried; before a turn is committed, the call can fall back to Pipeline |
| Current maturity | Established staged path | Recommended conversational default, but still a research preview with feature gaps |

Use Realtime for faster back-and-forth when its current feature set covers your
request. Use Pipeline when you need local speech, independent providers, or the
broadest tool support.

The **Voice engine** selection says what Jarvis should try next. Its runtime
line says what the current session actually uses. Changing the engine during
an active desktop call closes and reopens that call with the new choice.

### Know What Stays Local

“Local” applies to one Pipeline stage, not automatically to the whole
conversation.

- Desktop wake detection runs locally and only activates the session.
- Local Voice Input keeps recognition audio on the computer. If local
  recognition fails, Jarvis does not silently upload that recording just to
  recover.
- A hosted Brain can still receive the resulting transcript, even when Voice
  Input is local.
- Piper creates speech locally, but the text it speaks may have come from a
  hosted Brain.
- Realtime sends the live conversation audio to the selected Realtime service
  and bypasses the separate Pipeline Voice Input, Brain, and Voice Output
  selections.
- A failed local Brain may cross to another ready provider family. If that
  choice is hosted, the request can leave the device and the effective provider
  is reported in the session data.

Read [Local AI Providers](local-ai-providers) before treating a mixed setup as
offline.

### Keep One Language Across the Turn

The recognition language controls how speech becomes text. The reply language
controls how the assistant answers. **Voice > Language** belongs to Dictation;
use **Languages** for voice-conversation recognition and reply settings.

Jarvis resolves the reply language once for each turn. A language you pin wins.
In **Auto**, a one- or two-word interjection keeps the conversation's current
language, while a complete request can switch it. When neither speech nor
context gives a clear answer, English is the fallback.

That one decision covers clarification questions, approval prompts, status and
error phrases, the final answer, and the speaking-language choice. A provider
fallback can change the sound of the voice without changing the language rule.
After changing recognition language, end an active Realtime call; Pipeline
applies that recognition change after the app restart requested by the
**Languages** view.

### Interrupt, Cancel, or End the Call

- **Interrupt a reply:** speak clearly over it. Desktop voice waits for
  sustained speech so the assistant's own speaker output is less likely to
  trigger a false interruption. A whisper may not interrupt reliably; use
  **Hangup** when you need an immediate stop.
- **Reject an approval:** answer with a clear no. In Realtime, changing the
  subject is not a reliable denial; say no or end the call.
- **End the session:** say a clear end-call command, press the configured
  **Hangup** shortcut, or use the active voice control. Hangup stops listening
  and playback and cancels the active assistant or Computer Use turn.
- **Check delegated work:** a background agent mission that already started
  can continue after hangup. Its visible name follows your assistant name, such
  as **Nova-Agent**, with **Assistant-Agent** as the neutral fallback. Review
  its own view and Outputs for the result.

Stopping a call does not undo a tool effect that already completed. If a
Realtime connection fails after a request or action has been committed, Jarvis
ends the call instead of replaying the captured audio through Pipeline and
risking a duplicate action. Start a new call for the next turn.

### Understand Session Records

The local session recorder is enabled by default. **Transcription** groups the
recognized user text, recorded replies, spoken notices, and available provider
details by voice session. It does not save the microphone audio for these
views. If a provider supplies no transcript for part of a Realtime exchange,
Jarvis cannot reconstruct those missing words later.

**Run Inspector** adds captured routing, tool, timing, approval, and error
details. Dictation uses separate **Recent dictations** history and can retain
recovery audio only for incomplete results when that option is enabled.

## How It Fits Together

1. [Audio and Wake Word](audio-and-wake-word) supplies the desktop devices and
   optional activation phrase; Call or the in-app control can open the same
   session without wake detection.
2. The chosen voice engine receives the request. Pipeline connects Voice
   Input, the Brain, and Voice Output; Realtime uses one live audio connection.
3. The Speech Dictionary corrects recognized terms in both engines. The turn's
   recognition and reply language settings keep the conversation consistent.
4. The planner keeps ordinary conversation on the simplest path and sends
   supported actions through the Tool Model, connected tools, permissions, and
   any required safety confirmation.
5. The reply returns as speech. Transcription and Run Inspector record the
   available text and events without controlling the live turn.
6. [Dictation](dictation) can use the same microphone and recognition choices,
   but its result goes to a text field instead of the Brain or spoken output.

When the preferred provider is unavailable, Jarvis uses another compatible,
ready path when it can and names the effective mode. When no safe path remains,
it reports the unavailable part and leaves Chats usable.

## Check That It Works

1. Start a session with your wake phrase, Call shortcut, or voice control.
2. Ask one harmless question and watch **Listening** change to **Thinking** or
   **Speaking**.
3. Confirm that you hear a relevant answer and the app returns to **Listening**.
4. Use **Hangup**, confirm **Ready**, then open **Transcription** and find the
   completed turn.

For Realtime, also check the runtime line under **API Keys & Providers > Voice
engine**. It should name Realtime, Pipeline, or a Pipeline fallback honestly.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Wake or Call does not reach **Listening** | Voice is still warming, the microphone is unavailable, or activation is not ready | Wait for **Ready**, check **Audio devices**, and run **Test wake word**; use an in-app control meanwhile |
| The transcript is empty, wrong, or misses a name | The input device, recognition language, provider, or vocabulary is wrong | Try one short sentence, review **Languages**, and add repeated term mistakes under **Voice > Dictionary** |
| Realtime is selected but the runtime line says Pipeline | No compatible live session opened safely | Test the active Realtime provider in **API Keys & Providers**, or choose Pipeline deliberately |
| **Speaking** appears but you hear nothing | The output device, volume, or speech path is unavailable | Check the selected speaker and system volume; in Pipeline, test another ready Voice Output choice |
| Browser Realtime is unavailable or cannot hear you | The page is not localhost/HTTPS, microphone access is denied, or the browser lacks required audio support | Use a secure address, allow microphone access, and retry; continue in Chats if audio remains unavailable |

If a Realtime call ends after an error, check Transcription and Run Inspector
before repeating an action. The safe recovery may be a new call rather than an
automatic replay.

## Next Steps

- Read [Audio and Wake Word](audio-and-wake-word) to choose devices, activation,
  and the Call and Hangup controls.
- Use [Dictation](dictation) when you want speech inserted into another app
  without an assistant reply.
- Read [Local AI Providers](local-ai-providers) to separate local recognition,
  local reasoning, and local speech output.
- Open [Sessions and Run Inspector](sessions-and-run-inspector) to review saved
  turns, effective providers, actions, and failures.
