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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [languages, voice, speech, settings]
related: [voice-conversations, providers-and-api-keys, audio-and-wake-word]
---

You can use Personal Jarvis in English, German, or Spanish. The interface,
speech recognition, reply language, and speaking voice are separate choices,
so changing one does not silently change the others.

The speaking voice comes from the provider that handles the current voice
session. Pipeline and Realtime keep separate provider, model, and voice
choices.

> [!info] Changing **Interface Language** affects the app, not what Jarvis
> hears or says. Use **Voice Recognition Language** and **Reply Language** for
> the conversation itself.

## Before You Start

Typed conversations need a working Brain provider. Spoken conversations also
need microphone permission and a working voice provider.

Open **API Keys & Providers** to connect an online speech provider. Enter
credentials only there, never in chat or voice input. Voice previews make a
provider request and may use account quota.

## Understand the Four Choices

| Choice | Where to choose it | What it controls | Default |
|---|---|---|---|
| Interface Language | **Settings > Languages** | The translated desktop interface | English |
| Voice Recognition Language | **Settings > Languages** | Speech transcription and the language used for wake-word matching | Automatic |
| Reply Language | **Settings > Languages** | Replies, acknowledgements, status messages, and action readbacks | Automatic |
| Speaking Voice | **API Keys & Providers > Voice Output** or **Realtime** | The sound of spoken output | The saved choice or that provider's default |

The wake-word panel shares the **Voice Recognition Language** setting. Its
language list offers English, German, and Spanish, but not Automatic. Choosing
a language there also changes the recognition setting shown under
**Settings > Languages**.

The voice engine defaults to Realtime. If no Realtime provider can be used,
desktop voice falls back to Pipeline so a keyless Realtime setup does not
silence voice conversations.

## Choose Your Languages

1. Open **Settings** and find **Languages**.
2. Under **Interface Language**, choose English, German, or Spanish. The open
   app updates immediately.
3. Under **Voice Recognition Language**, keep **Automatic** to let the speech
   provider detect each spoken turn. Choose one language when you consistently
   speak that language and automatic recognition is unreliable.
4. Under **Reply Language**, choose **Automatic** to follow the conversation,
   or choose one language to pin new replies to it.
5. After changing recognition language, end any active Realtime call. Restart
   Jarvis before testing Pipeline transcription because its speech recognizer
   is built when the voice pipeline starts.

The recognition-language change can update wake-word matching while Jarvis is
running. The Pipeline speech recognizer still needs the restart described in
step 5.

Reply-language changes apply to new turns without restarting Jarvis. If a
desktop Realtime call is active, Jarvis closes and reopens that call so the new
reply policy reaches the provider. A reply that was already being generated
may finish under the earlier setting.

### How Automatic Reply Language Works

Jarvis resolves one output language for each turn and reuses it across the
answer, short acknowledgements, errors, progress updates, action readbacks,
and text-to-speech.

1. A pinned **Reply Language** wins over every other signal.
2. In Automatic mode, a one- or two-word interjection keeps the established
   conversation language. A brief `yes`, `stop`, or similar reply does not
   switch the conversation by itself.
3. A longer turn can switch the conversation when its text clearly indicates
   another language. The speech provider's language tag is used when the text
   is ambiguous.
4. Ambiguous input keeps the established conversation language when one
   exists. With no usable language signal and no conversation history, Jarvis
   falls back to English.

## Choose a Speaking Voice

Open **API Keys & Providers**, then use the **Pipeline** or **Realtime** tab set
for the engine you want to configure.

| Provider group | Current controls |
|---|---|
| OpenRouter (TTS) | Choose a speech model, then choose one of that model's voices. Each listed voice has English, German, and Spanish previews. |
| ElevenLabs, Gemini Flash TTS, xAI Text to Speech, and Inworld | Activate the provider, then choose from its **Voice** list. These cards do not currently show a preview button. |
| Cartesia Sonic 3.5 | Choose the Sonic model. The current card does not expose a separate voice picker. |
| OpenAI Realtime and Gemini Live | Choose a model and voice for each configured provider. **Provider default (recommended)** clears an explicit pin. Each concrete voice has English, German, and Spanish previews. |

Provider and model catalogs are not interchangeable. A voice from one family
may be rejected by another, so choose from the list on the provider card.

To hear a preview, choose the sample language beside **Preview in**, then use
**Preview voice**. Previewing does not save the voice and does not reconnect an
active call. Select the voice name to save it. Realtime cannot preview
**Provider default** because that option does not identify one concrete voice.

Pipeline rebuilds its text-to-speech provider after a saved voice or model
change. When the desktop speech runtime is available, the next spoken turn uses
the new choice. Otherwise, the choice applies the next time voice starts.

Realtime stores model and voice choices separately for OpenAI Realtime and
Gemini Live. Saving a choice for the provider serving an active desktop call
reconnects that call. A choice for another configured provider applies when
that provider is used later.

## Pipeline and Realtime

| Voice engine | How speech moves through Jarvis | Language and voice behavior |
|---|---|---|
| Pipeline | Speech-to-text creates a transcript, the Brain prepares a reply, and text-to-speech creates audio | Uses the active Voice Input and Voice Output providers. The resolved reply language is passed to text-to-speech for pronunciation. |
| Realtime | One live provider listens and speaks in the same session | Uses the selected model and voice for the active Realtime provider. The recognition preference is set when the session opens, and the resolved reply language is updated after a completed transcript. |

Push-to-talk always uses Pipeline. Normal wake-word and hotkey voice sessions
can use Realtime when it is selected and a credential-ready provider is
available.

The app will not let you turn Realtime on from Pipeline until it finds a usable
Realtime credential. At session start, Jarvis tries credential-ready Realtime
providers in its configured order. If none opens, the desktop session uses
Pipeline. During a live Realtime call, a terminal provider failure can move the
call to another credential-ready Realtime provider. Any provider fallback can
also change the exact voice.

> [!note] Realtime is currently labelled **Research preview**. Some tools and
> features are not available there yet. Use Pipeline when a task does not work
> in Realtime.

## Limitations

- The shared interface, recognition, and reply controls currently cover
  English, German, and Spanish. A provider may support additional speech
  languages, but they are not choices in these controls.
- Automatic detection can be uncertain for names, short phrases, or text that
  looks the same in several languages. Pin a language when consistency matters.
- A language pin controls words and pronunciation guidance. It cannot guarantee
  that every provider voice has the same accent or quality in all three
  languages.
- A fallback provider uses its own catalog. Jarvis may choose a compatible
  voice profile when possible, but the voice name and sound can still change.

## How It Fits Together

1. You type a message, use push-to-talk, or start a normal voice session with a
   wake word or hotkey.
2. For speech, **Voice Recognition Language** guides transcription and wake-word
   matching. The [Speech Dictionary](speech-dictionary) can correct specific
   names and terms, but it does not choose the conversation language.
3. Jarvis resolves one reply language for the turn. A pinned **Reply Language**
   wins; otherwise, conversation history and the detected input decide.
4. The Brain creates the answer, and the same language decision is reused for
   the reply and other user-facing messages from that turn.
5. Pipeline sends the text and language to its active text-to-speech provider.
   Realtime speaks through its live provider. The speaking voice changes the
   sound, not the reply-language policy.
6. If a provider is unavailable, Jarvis uses another credential-ready option
   where the current engine supports it. The fallback may have a different
   voice.

[Providers and API Keys](providers-and-api-keys) explains how credentials and
provider fallback work. [Audio Devices and Wake Word](audio-and-wake-word)
explains how a voice session starts and how to choose the pronunciation
language for a wake phrase.

## Check That It Works

Set **Interface Language** to English and **Reply Language** to Spanish. Type a
complete question in English. The app labels should remain English, while the
answer text, and spoken output when voice is active, should be Spanish.

Return **Reply Language** to **Automatic** if you want later substantive turns
to change the conversation language.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The interface changed, but replies did not | Interface and reply language are separate | Change **Reply Language** under **Settings > Languages** |
| Spoken words are transcribed in the wrong language | Automatic detection is uncertain, or the running recognizer still uses the earlier setting | Choose a recognition language, end a Realtime call, and restart Jarvis before testing Pipeline |
| The wake phrase stops matching after a language change | Wake matching and voice recognition share one language setting | In the wake-word panel, choose the language in which you pronounce the phrase |
| A short word does not switch the reply language | Automatic mode keeps one- and two-word turns in the current conversation language | Use a complete sentence, or pin **Reply Language** temporarily |
| A name or technical term is repeatedly wrong | The overall language is correct, but the recognizer does not know the term | Add the term and its common mishearing in [Speech Dictionary](speech-dictionary) |
| A voice is missing | The selected provider or model has a different voice catalog | Open the active **Voice Output** or **Realtime** provider card and choose from its displayed list |
| A preview fails | The provider credential, quota, network, model, or voice is unavailable | Test the provider under **API Keys & Providers**, then try the preview again |
| The current Realtime call reconnects after a change | Reply language, model, or voice changed connection-level settings | Wait for the call to reopen, then test a new turn |
| Realtime cannot be enabled | No installed Realtime provider has a usable credential | Connect OpenAI Realtime or Gemini Live, or continue with Pipeline |

For microphone, provider, or audio failures beyond these checks, follow
[Troubleshooting](troubleshooting).

## Next Steps

- Read [Voice Conversations](voice-conversations) to start, continue, and end
  spoken sessions in Pipeline or Realtime mode.
- Open [Providers and API Keys](providers-and-api-keys) to connect services and
  understand provider fallback.
- Use [Audio Devices and Wake Word](audio-and-wake-word) to match your
  microphone, speaker, and wake phrase to the language you speak.
- Add difficult names in [Speech Dictionary](speech-dictionary) when the
  language is right but individual words are still misheard.
