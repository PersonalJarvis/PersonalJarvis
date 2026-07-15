---
title: "Languages and Voices"
slug: languages-and-voices
summary: Choose reply language, speech language, and speaking voice while keeping every layer of a conversation consistent.
section: "Personalize and connect"
section_order: 3
order: 3
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [languages, voice, speech, settings]
related: [voice-conversations, providers-and-api-keys, audio-and-wake-word]
---

You can use Personal Jarvis in English, German, or Spanish without making every
part of the app use the same language. Choose separately what the interface
shows, how speech is recognized, and which language Jarvis uses for replies.

The speaking voice is a separate choice. It comes from the active speech
provider and can change when you switch between Pipeline and Realtime voice.

> [!info] Changing the interface language never changes what Jarvis hears or
> says. Use **Voice Recognition Language** and **Reply Language** for that.

## Before You Start

Typed conversations only need a working Brain provider. Spoken conversations
also need microphone permission and a working voice setup.

If a language or voice choice depends on an online provider, connect it under
**API Keys**. Enter credentials only in that view, never in chat or voice input.

## Understand the Four Choices

| Choice | Where to choose it | What it controls | Options |
|---|---|---|---|
| Interface Language | **Settings > Languages** | Labels, buttons, and app messages | English, German, Spanish |
| Voice Recognition Language | **Settings > Languages** | How spoken audio becomes text | Automatic, English, German, Spanish |
| Reply Language | **Settings > Languages** | Answers, acknowledgements, status messages, and action readbacks | Automatic, English, German, Spanish |
| Speaking Voice | **API Keys**, on the active TTS or Realtime provider | The sound of spoken replies | The choices supported by that provider and model |

**Automatic** has a different purpose in each language setting:

- For voice recognition, it detects the language of each spoken turn.
- For replies, it follows the language you speak or type while keeping a
  running conversation stable.

## Choose Your Languages

1. Open **Settings** and find **Languages**.
2. Under **Interface Language**, choose English, German, or Spanish. The open
   app changes immediately, including labels and buttons.
3. Under **Voice Recognition Language**, keep **Automatic** if you regularly
   switch languages. Choose one language if automatic recognition repeatedly
   mishears speech that is always in that language.
4. Under **Reply Language**, choose **Automatic** to follow your conversation,
   or select one language to keep every reply in it.
5. End any active voice call after changing the recognition language. For the
   classic Pipeline, restart Jarvis so the speech recognizer is rebuilt with
   the new choice.

When **Reply Language** is Automatic, a full sentence can move the conversation
to another language. A short interjection such as “yes” or “stop” keeps the
current conversation language, which prevents accidental switching.

If Jarvis cannot identify a language and no conversation language exists yet,
it uses English as the fallback. An explicit reply-language choice always wins.

## Choose a Speaking Voice

Voice choices depend on the voice engine you use. Jarvis shows only the catalog
known for the selected provider and model; there is no universal voice list.

1. Open **API Keys**.
2. For **Pipeline** voice, open the active text-to-speech provider card. Choose
   a displayed model or voice when that provider offers a picker. A preview may
   be available for supported choices.
3. For **Realtime** voice, open the active Realtime provider card and use its
   **Voice** list. **Provider default** lets that provider choose.
4. Start a new voice conversation and listen to one complete reply.

Pipeline and Realtime store their voice choices separately. Switching the
voice engine can therefore change how Jarvis sounds without changing its reply
language. A fallback to another provider may also use that provider's default
voice.

## Pipeline and Realtime

| Voice engine | How speech moves through Jarvis | Which voice it uses |
|---|---|---|
| Pipeline | Speech recognition turns audio into text, the Brain prepares a reply, then text-to-speech creates audio | The active text-to-speech provider's voice |
| Realtime | One live provider listens and speaks in the same session | The voice selected on that Realtime provider |

Both paths use the same reply-language policy. Pipeline passes the resolved
language to text-to-speech for pronunciation. Realtime receives the recognition
preference when the session starts and is updated with the resolved language
after a complete transcript. Changing a pinned reply language during an active
Realtime call may reconnect the call so the new policy takes effect.

If no credential-ready Realtime provider is available, the Realtime switch is
disabled and Pipeline remains available. Learn how the two paths behave in
[Voice Conversations](voice-conversations).

## How It Fits Together

1. You type a message, or your microphone supplies spoken audio.
2. For speech, **Voice Recognition Language** guides transcription. The
   [Speech Dictionary](speech-dictionary) can then correct specific names or
   terms; it does not choose the conversation language.
3. Jarvis makes one language decision for the turn. A pinned **Reply Language**
   wins; otherwise, the current conversation and detected input decide.
4. The Brain creates the answer, and the same decision is reused for the main
   reply, short acknowledgements, errors, progress updates, and action
   readbacks.
5. Pipeline sends the text and language to the active text-to-speech provider.
   Realtime speaks through its live provider. **Speaking Voice** controls the
   sound, not the words or language policy.

Your [Providers and API Keys](providers-and-api-keys) supply the recognition,
Brain, text-to-speech, or Realtime capability. They do not replace the shared
language decision. [Audio Devices and Wake Word](audio-and-wake-word) controls
how a voice conversation starts; its wake-language choice should match the
language in which you pronounce the wake phrase.

## Check That It Works

Set **Interface Language** to English and **Reply Language** to Spanish. Type a
simple question in English. The app labels should remain English, while the
answer text—and spoken output when voice is active—should be Spanish.

Then return **Reply Language** to **Automatic** if you want Jarvis to follow
the language of each substantive turn.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The interface changed, but replies did not | Interface and reply language are separate | Change **Reply Language** under **Settings > Languages** |
| Spoken words are transcribed in the wrong language | Automatic detection is uncertain, or the recognizer still uses the earlier setting | Choose a recognition language, end the call, and restart Jarvis for Pipeline |
| One name or technical term is repeatedly wrong | The overall language is correct, but the vocabulary is unfamiliar | Add the term and its common mishearing in [Speech Dictionary](speech-dictionary) |
| A short word does not switch the reply language | Automatic mode keeps brief interjections in the current conversation language | Use a full sentence, or pin the reply language temporarily |
| The expected voice is missing or changes | Voice catalogs and fallbacks belong to providers and models | Check the active TTS or Realtime card under **API Keys** and choose from its displayed list |
| Realtime cannot be enabled | No installed Realtime provider has a usable credential | Connect a supported provider under **API Keys**, or continue with Pipeline |

For microphone, provider, or audio failures beyond these checks, follow
[Troubleshooting](troubleshooting).

## Next Steps

- Read [Voice Conversations](voice-conversations) to start, continue, and end
  spoken sessions in Pipeline or Realtime mode.
- Open [Providers and API Keys](providers-and-api-keys) to connect the services
  that supply speech recognition, speaking voices, and Realtime audio.
- Use [Audio Devices and Wake Word](audio-and-wake-word) to match your
  microphone, speaker, and spoken wake phrase to the language you use.
- Add difficult names in [Speech Dictionary](speech-dictionary) when the
  language is right but individual words are still misheard.
