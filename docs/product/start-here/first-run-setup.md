---
title: "Complete First-Run Setup"
slug: first-run-setup
summary: "Agree to the terms, connect one Brain, find your coding agents, choose how voice starts, then take the tour of the app."
section: "Start here"
section_order: 1
order: 3
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-09-30
phase: "-"
audience: end-user
tags: [setup, onboarding, tour, language, permissions, microphone, wake-word, providers]
related: [providers-and-api-keys, audio-and-wake-word, permissions, start-your-first-chat]
---

First-run setup is one card that changes shape from step to step. It asks only
what the assistant needs before it can work: your agreement, one Brain, how
voice starts, and (on macOS) system access. Everything else is explained
afterwards by a short tour of the real app.

## Before You Start

- Open the installed desktop app. The first launch may take several seconds.
- Have one API key ready (OpenAI or Google Gemini is the shortest path), or run
  Ollama with one installed model for a keyless Brain.
- On macOS, launch the signed app from its application bundle before granting
  access; permissions belong to that exact app identity.

> [!warning] Paste a provider credential only into the masked key field of the
> setup card or of **API Keys**. Never paste one into chat, speak it, put it in
> a wake word, add it to configuration, or include it in a screenshot.

## Complete the Setup

The dots at the bottom of the card show where you are, and **Step X of Y**
counts the steps this computer needs (macOS has one more). **Back** returns to
the previous step, never behind the agreement. If the window reloads, the card
reopens on the step you were on.

### 1. Agree to the terms

The first card lists what the assistant does on this computer: it runs
commands and changes files, can see your screen when asked, sends what you say
to the provider you choose, is billed by that provider, and can make mistakes.

1. Pick **English**, **Deutsch** or **Español** in the card's corner if you want
   another interface language. You can change it later under **Settings >
   Languages**.
2. Optionally open **Read the full Terms of Use**.
3. Tick the agreement, then select **Agree and continue**. **Decline and quit**
   closes the app without saving anything; the card asks again next time.

### 2. Give it a brain

Choose one way to think and talk:

- **OpenAI GPT-Live** (recommended) or **Gemini Live**: paste that provider's
  key into the field. The key is tested as soon as it is saved, and live voice
  plus its thinking model are pointed at it. The card confirms **Connected**.
- **Another provider**: opens the full list of Brain providers; saving the first
  key makes that provider the active Brain.
- **Run on this computer**: uses Ollama with no key. The link reads **Ollama
  found** when a local server answers; **Use the local model** switches the
  Brain to it and voice to the listen-think-speak pipeline.

**Continue** unlocks once one path works. **I'll add a key later** moves on;
the last step then says that chat and voice stay off until a key exists.

### 3. Hand off the big jobs

The card shows the coding agents this computer is already signed in to (for
example a subscription you use in a terminal). Connect one with its own
browser sign-in, or select **Check again** after signing in elsewhere. This
step is optional: **Continue** is always available.

### 4. Allow access on this Mac (macOS only)

The rows are the same as under **Settings > Privacy permissions**: Microphone,
Screen Recording, Accessibility, Input Monitoring, Input control, Automation
and Keychain. Use **Allow** or **Open Settings**, return, and wait for the row
to update. A grant that only reads back after a restart counts as ready; the
final restart applies it. **Skip for now, text only** defers all of them.

Windows and Linux do not show this step.

### 5. Choose how you call it

- **A wake word**: **Hey** is fixed, you type the rest (at least two letters).
  The word also becomes the assistant's name; the card shows it as you type,
  for example **Your assistant will be called Nova**. Tick that you are
  responsible for the word, then select **Save and continue**. If no local
  engine can hear that word yet, the card offers the local speech pack, or
  **Continue with the shortcut for now**.
- **A keyboard shortcut**: nothing listens until you press the Call keys the
  card shows (for example **F3 + F4**).

**Test microphone** listens for three seconds and fills the level meter. It
reports a clear, quiet, missing or blocked input and never blocks saving.

### 6. All set

The last card lists what each step set up and, in plain lines, what is still
missing. Turn on **Start at login** if the switch appears. **Start** (or
**Start Nova** with a wake word) saves the setup and restarts the app once so
every choice takes effect together.

## Take the Tour

After the restart the app opens with a short guided tour. It dims the window,
lights up one part of the real interface at a time, and explains it in a small
card: the voice bar, a new chat, the agents and their world, Voice, Artifacts,
the Agentic IDE, Plugins and the Marketplace, and Settings.

- **Next** moves on; clicking the highlighted part yourself does the same.
- The tour navigates by itself where needed (into the agents' world and back)
  and ends on the home screen. It never starts a call or any work for you.
- **Skip tour** or **Escape** ends it at any point.
- Replay it anytime under **Settings > App > App tour**.

## Recover a Skipped or Deferred Choice

- Change the interface and reply languages under **Settings > Languages**.
- Connect and test models under **API Keys**; the same place connects coding
  agents by key or subscription.
- Repair macOS access under **Settings > Privacy permissions**.
- Change the phrase, spoken wake language, activation switch, or local wake
  pack under **Settings > Wake Word**, and the Call shortcut under **Settings >
  Voice Keybinds**.
- Change login startup under **Settings > App** where supported.

## How It Fits Together

1. The agreement is the only consent moment; the installer asks nothing.
2. One Brain is enough to start. A starter plan points live voice and its
   thinking model at the same key; any other single Brain key works too.
3. Coding agents are optional helpers for longer jobs and connect separately.
4. The wake phrase supplies both local activation and the assistant's name.
   The Call shortcut starts voice without an always-listening wake engine.
5. Permissions allow an operating-system capability; they do not approve a
   later Computer Use action or bypass its safety check.
6. One restart at the end applies every choice; the tour runs once after it.

## Check That It Works

1. After **Start**, confirm the app reopens and the tour begins.
2. Open **API Keys**, select **Test** on the active Brain card, and look for
   **Works**.
3. Start a new chat and send a harmless message. Confirm a reply arrives.
4. For voice, press the Call shortcut or say your wake word.

On a headless system there is no restart; verify text chat or the Control API.
Desktop-only features report their limits rather than prevent startup.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Continue** stays disabled on the brain step | The key was not saved or its test failed | Read the line under the key field, fix the key, or choose **I'll add a key later** |
| **Ollama found** does not appear | Ollama was unreachable or has no model | Continue, prepare Ollama, then use its card under **API Keys** |
| **Continue** is disabled on macOS | A permission or stable app-identity check is unresolved | Use **Allow** or **Open Settings**, return and wait, or choose **Skip for now, text only** |
| Microphone test reports quiet, missing, or blocked | The input has no usable signal | Check OS access and **Settings > Audio devices** |
| Saved wake word does not respond | Its local model, language, microphone, or activation switch is not ready | Use the Call shortcut; under **Settings > Wake Word**, install the offered model and run **Test wake word** |
| The tour does not appear after the restart | The app opened in the Agentic IDE, or the tour was already seen | Leave the IDE, or replay it under **Settings > App** |
| App does not reopen | The restart could not start a fresh process | Open the app; setup was already saved |
| First-run setup returns every launch | The completion state is not read from the same writable data location | Follow [Troubleshooting](troubleshooting) for data-directory and version checks |

## Next Steps

- Follow [Start Your First Chat](start-your-first-chat) for a safe first test.
- Read [Providers and API Keys](providers-and-api-keys) before connecting a
  cloud account or changing fallbacks.
- Read [Local AI Providers](local-ai-providers) for Ollama setup and limits.
- Use [Audio and Wake Word](audio-and-wake-word) to finish microphone, wake
  language, activation, or shortcut setup.
- Review [Permissions](permissions) before enabling Computer Use or global
  shortcuts on a new operating system.
