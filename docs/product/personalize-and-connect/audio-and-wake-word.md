---
title: "Audio Devices and Wake Word"
slug: audio-and-wake-word
summary: "Choose a microphone and speaker, set a wake phrase, and tune reliable hands-free listening."
section: "Personalize and connect"
section_order: 3
order: 2
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [audio, microphone, speaker, wake-word, voice, pipeline, realtime]
related: [voice-conversations, speech-dictionary, permissions, troubleshooting]
---

Choose which microphone your assistant hears, where replies play, and how a
voice conversation starts. Automatic device selection and **Auto** wake
detection are the best defaults for most people.

Wake detection is separate from the request that follows. A wake phrase can be
processed locally even when the later conversation uses hosted speech or AI.

## Before You Start

- Connect the headset, microphone, or speakers you want to use.
- Wait until the desktop app shows **Ready**, not **Voice starting...**.
- Allow microphone access in the operating system. macOS users should also
  review **Settings > Privacy permissions**.
- Never use a password, recovery code, or other secret as a wake phrase.

Voice is optional. Chats and other text features continue on a computer with
no usable audio hardware.

## Choose and Test Audio Devices

1. Open **Settings > Audio devices**.
2. Keep **Voice output** and **Microphone** on **Automatic (recommended)** when
   possible. Automatic prefers a practical headset or microphone and adapts as
   devices appear or disappear.
3. Choose a named device when you need a fixed route, such as a USB microphone
   for input and desktop speakers for output. The selection saves immediately.
4. Select **Rescan devices** after connecting something that is missing. If the
   app says the change applies on the next start, restart before testing.
5. Use **Test wake word** to check that the host can hear a useful microphone
   level. Then test the actual selected route by saying the phrase end to end.
6. Test the speaker with **Preview voice** when your selected Voice Output card
   offers it, or ask one short Pipeline question and listen for the reply.

A disconnected named device remains saved. While it is absent, the audio
resolver can use an automatic fallback; reconnect it or select Automatic if
the route remains wrong.

The device pickers control audio on the computer running the native desktop
pipeline. A Realtime session started in a browser uses that browser device's
permission and operating-system routing instead.

## Choose Wake, Call, or Dictation

| Start method | What it does | Important difference |
|---|---|---|
| **Wake phrase** | A local listener waits for your chosen phrase and opens a voice conversation | Requires an available local wake engine and an open microphone while idle |
| **Call shortcut** | A key combination opens the same voice conversation without spoken activation | Works when wake activation is off or unavailable; the shipped default is F3+F4 |
| **Dictation** | A shortcut or button turns speech into text at the cursor | It does not ask the assistant to answer or act |

Change Call under **Settings > Voice Keybinds**. Dictation shortcuts live under
your assistant-named **Voice > Shortcuts** area; see [Dictate Into Any
App](dictation).

Dictation owns the microphone while it runs, so wake and Call activation pause.
If a conversation is still open when you start Dictation, the deliberate
Dictation action ends that conversation, waits for the microphone, and then
records. This prevents two audio sessions from competing for one device.

## Set Up a Custom Wake Phrase

1. Open **Settings > Wake Word** and turn on **Activate wake word**.
2. Enter the complete phrase. A distinct phrase such as `Hey Nova` is more
   reliable than a common single word. With that prefix saved, saying only
   `Nova` does not activate it. The configured phrase also supplies the
   assistant's display name: `Hey Nova` becomes **Nova**.
3. Under **Which language do you speak?**, choose how you pronounce the phrase,
   not where its name or spelling came from. English, German, Spanish, and
   **Auto** are available.
4. Keep **Detection engine** on **Auto (recommended)**.
5. Select **Save wake word**. A running desktop pipeline normally switches
   immediately; follow a restart notice when live switching was unavailable.
6. Select **Test wake word**. Check the resolved engine, effective language,
   vocabulary warning, and microphone result. Then return to **Ready** and say
   the phrase once at normal volume.

Wake language is its own setting. It does not change the app language, reply
language, general Pipeline recognition language, or Dictation language. **Auto**
uses those broader settings only as a sensible starting point. Pin the wake
language when automatic selection is wrong.

The self-test checks readiness; it does not prove that your voice, distance,
and room will trigger the phrase. The end-to-end attempt is the recognition
test. There is no sensitivity slider: each engine uses calibrated settings.

## Install a Local Wake Model

Auto resolves the best available path for the exact phrase:

1. A compatible user-supplied ONNX model for that phrase, when configured.
2. Local Vosk keyword spotting with a model for the spoken language.
3. Local Whisper phrase matching when that speech pack is available.
4. No wake listener, with the Call shortcut as the honest fallback.

If a save reports a degraded path, select **Download wake model**. This fetches
the language-specific Vosk pack, roughly 40 MB, and rechecks the phrase. It uses
the CPU and stays offline after the one-time download. Installed language packs
can also help when a name and the speaker's language differ.

If the app instead offers **Enable any wake word**, it installs the local
faster-whisper engine and its wake checkpoint. The install needs a connection,
free disk space, and a compatible prebuilt package for the operating system and
Python version. A failed install remains retryable and does not disable Call.

These wake components are related to local speech recognition but are not the
same setting. Installing a wake pack does not select local **Voice Input** for
the request after activation. Likewise, selecting Whisper or Nemotron as Voice
Input does not prove that the matching wake model is ready. Test each stage;
see [Use Local AI Providers](local-ai-providers).

A graphics processor is not required. Avoid forcing advanced engines unless
you have the model they require. A missing or mismatched model never causes a
hidden substitute phrase: wake stays unavailable and Call remains usable.

## Privacy and Reliability

The recommended Vosk and local Whisper wake paths process their rolling audio
locally. The spoken request after activation follows the selected voice mode:
Pipeline uses its Voice Input, Brain, and Voice Output choices; Realtime uses
its live connection.

A custom ONNX candidate can be checked with the selected Voice Input service.
If that service is hosted, a short candidate-audio window may leave the device.
Use the local Auto path or Call when that boundary is unsuitable.

For fewer missed or accidental wakes:

- use a distinct two- or three-word phrase with a prefix;
- select the language you actually speak;
- keep the microphone close and input gain high enough for the self-test;
- download the offered Vosk pack when a hard name uses the weaker phrase-match
  fallback; and
- use headphones when speaker echo or a noisy room causes confusion.

## Operating-System and Remote Limits

- Windows, macOS, and Linux can use local wake detection when desktop audio and
  compatible packages are available.
- macOS requires Microphone permission; global shortcuts can also require
  Accessibility and Input Monitoring.
- Linux global shortcuts need a supported X11 hotkey backend. Wayland normally
  blocks them, so use wake activation or an in-app control.
- A headless host has no native wake listener, Call shortcut, Dictation target,
  or local speaker route. Its text features remain available.
- A remote browser can use its own microphone for Realtime over localhost or a
  properly secured HTTPS connection. The host's Audio device pickers do not
  choose that remote microphone or speaker.

## How It Fits Together

1. The selected microphone supplies wake, Call, conversation, or Dictation
   audio, one owner at a time.
2. Wake or Call opens a conversation; Dictation opens a transcription-only
   lane instead.
3. The wake engine checks the phrase locally, except the optional custom-ONNX
   verification boundary described above.
4. Pipeline or Realtime handles the spoken request after activation.
5. Pipeline replies through the selected Voice Output; browser Realtime uses
   the browser's audio route.

## Check That It Works

1. Choose Automatic or the intended microphone and output.
2. Save a prefixed phrase with Auto detection and the correct wake language.
3. Resolve self-test warnings, then say the phrase from **Ready**.
4. Confirm **Listening**, ask one short question, and hear the reply from the
   intended output.
5. End the call, use the Call shortcut once, then test Dictation separately.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| No audio devices | Hardware, permission, or audio backend is unavailable | Connect a device, review OS audio settings, and select **Rescan devices** |
| Saved device is absent | The named device is disconnected | Reconnect it or choose Automatic |
| Self-test says quiet or no microphone | Wrong input, low gain, or blocked permission | Check the microphone and [Permissions](permissions), then retry |
| Degraded engine or hard name never wakes | Matching local model is missing | Select **Download wake model**, or use a clearer prefixed phrase |
| Self-test is ready but no activation occurs | Readiness passed, but the real acoustic phrase did not | Try normal volume closer to the mic; verify spoken language and phrase |
| Call shortcut does nothing | Shortcut permission/backend is unavailable or the binding conflicts | Record another key; on Wayland or headless systems use an available in-app control |

For persistent voice or provider failures, continue with
[Troubleshooting](troubleshooting).

## Next Steps

- Read [Voice Conversations](voice-conversations) for Pipeline and Realtime.
- Set up [Dictation](dictation) for speech-to-text without an assistant reply.
- Use [Local AI Providers](local-ai-providers) to keep later speech stages local.
- Review [Permissions](permissions) when capture or shortcuts are blocked.
