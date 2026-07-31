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
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [languages, voice, speech, settings]
related: [voice-conversations, providers-and-api-keys, audio-and-wake-word]
---

The interface, replies, speech recognition, dictation, and speaking voice are
separate choices.

## Before You Start

Voice conversations need a microphone and a working Pipeline or Realtime setup.
Enter credentials only in **API Keys & Providers**, never in chat or speech.

## Understand the Language Settings

| Choice | Where it lives | What it changes |
|---|---|---|
| **Interface Language** | **Settings → Languages** | Labels, buttons, and other app text |
| **Voice Recognition Language** | **Settings → Languages** | How normal spoken assistant turns are transcribed |
| **Reply Language** | **Settings → Languages** | Answers, acknowledgements, status messages, action readbacks, and spoken output |
| **Dictation Language** | **Voice → Language** | Speech inserted into another app or the assistant's text box |
| **Translation target** | **Voice → Language** | The fixed language in which translated dictation is delivered |
| **Speaking voice** | **API Keys & Providers** | The sound of the audio, not its words |

Interface and Reply Language support English, German, and Spanish equally.
Recognition and Dictation offer Automatic plus a much wider language list. A
provider may support fewer languages than the app lists.

Wake-word pronunciation has its own setting and need not match these choices.
See [Audio Devices and Wake Word](audio-and-wake-word).

## Choose Interface and Reply Language

1. Open **Settings → Languages**.
2. Choose an **Interface Language**. The open app changes immediately.
3. Under **Reply Language**, choose **Automatic** or pin English, German, or
   Spanish.

A reply pin wins for every new turn and keeps acknowledgements, errors, action
readbacks, and speech aligned with the answer.

In **Automatic**, a one- or two-word interjection keeps the established
conversation language. A complete turn can switch to another supported reply
language. Ambiguous speech can use the recognition tag; with no usable signal
or history, the fallback is English.

Names, commands, and code terms may keep their original form.

## Choose Recognition and Dictation Language

For normal voice conversations, start **Voice Recognition Language** on
**Automatic**. Pin one language only after repeated wrong detection.

Dictation is independent. Open **Voice → Language** and choose its language:

- Keep **Automatic** when you use multiple languages.
- Pin one language only when detection repeatedly fails; other languages can
  become less accurate.
- Use **Improve wording** to let a small text model repair punctuation and
  wording. **Automatic provider** uses an available family; a pin uses only
  that family.
- Turn on **Translate dictation** to deliver every dictation in one selected
  target language. It is off by default and shares the wording model call.

Wording and translation may send text to their provider. If no provider
answers, it times out, or the result changes too much, the raw transcript is
delivered and remains visible in history. With local recognition, Automatic
wording avoids silently choosing cloud processing; selecting a cloud family
opts in. A local provider such as Ollama can keep the pass on-device.

If source and target are pinned to the same language, nothing is translated.
Read [Dictate Into Any App](dictation) for history and recovery.

## Choose Pipeline or Realtime Voice

Open **API Keys & Providers** and choose the voice engine:

| Engine | How it works | Where the voice comes from |
|---|---|---|
| **Pipeline** | Speech recognition creates text, the Brain writes a reply, and text-to-speech creates audio | The active **Voice Output** provider |
| **Realtime** | One live provider listens and speaks in the same session | The model and voice selected on that Realtime provider card |

The engines keep separate choices. Realtime requires a ready Realtime provider;
if no session opens, the app can fall back to Pipeline. Any fallback can change
the exact sound.

For Pipeline, activate a **Voice Output** provider and use its model or voice
list. Catalogs are provider-specific. Where **Preview voice** is offered,
listening does not save the voice.

For Realtime, choose a model and voice on each provider card. **Provider
default** clears an explicit voice pin and cannot be previewed.

### Use Local Piper Voices

**Piper (on this machine)** is keyless Pipeline output. Its in-app install
downloads about 200 MB: one voice each for English, German, and Spanish. Piper
selects the voice matching each reply; text and speech stay on-device.

Piper runs on CPU, may sound less natural than cloud speech, and is not a
Realtime voice. A missing language voice can cause the wrong accent; complete
the provider-card download. See [Use Local AI Providers](local-ai-providers).

## Know When Changes Apply

| Change | When it applies |
|---|---|
| Interface language | Immediately in every open client |
| Reply language | On new turns; an active Realtime call reconnects |
| Pipeline recognition language | The running recognizer is swapped when possible; restart the app only if the change does not take effect |
| Realtime recognition language | End and start the current Realtime call |
| Dictation language, wording, or translation | The next dictation; no app restart |
| Pipeline provider, model, or voice | Next spoken turn when live switching succeeds; otherwise next voice start or app restart |
| Realtime provider, model, voice, or engine | The active call reconnects; otherwise the next call |

A running turn may finish with its old setting. Restart only when live switching
did not apply; a restart interrupts live terminal panes before they reattach.

## How It Fits Together

1. Recognition creates a transcript; Dictation has its own setting.
2. The reply resolver applies a pin or follows the conversation language.
3. Pipeline sends that language to text-to-speech; Realtime uses its live
   provider.
4. Voice selection changes sound, never reply policy.

## Check That It Works

1. Set the interface to English and pin replies to Spanish.
2. Ask in English. Labels should stay English while answer and speech are
   Spanish.
3. Return replies to **Automatic**, dictate a test, and inspect its history.

## Troubleshooting

| What you see | What to do |
|---|---|
| The interface changed but replies did not | Change **Reply Language**, not Interface Language |
| Speech is transcribed in the wrong language | Pin **Voice Recognition Language**; restart only if the running Pipeline recognizer did not switch |
| Dictation is wrong while voice chat is correct | Change **Voice → Language**; it is independent of normal recognition |
| Translation returns the spoken language | Check that translation is on, the target differs from a pinned source, and the wording provider test succeeds; failures return raw text |
| A short turn does not change reply language | Use a complete sentence or temporarily pin Reply Language |
| A voice is missing | Open the active provider card; catalogs do not transfer between providers |
| Piper is silent or has the wrong accent | Complete its local voice download and confirm Piper is the active Pipeline output |
| A Realtime call reconnects | A language, engine, provider, model, or voice setting changed at session level; test the next turn |

## Next Steps

- Read [Voice Conversations](voice-conversations) for Pipeline and Realtime use.
- Read [Dictate Into Any App](dictation) for shortcuts, delivery, and history.
- Read [Use Local AI Providers](local-ai-providers) for offline speech options.
- Read [Providers and API Keys](providers-and-api-keys) for setup and fallback.
