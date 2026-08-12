---
title: "Providers and API Keys"
slug: providers-and-api-keys
summary: Connect cloud services or local models to each assistant capability, then verify what is actually ready.
section: "Personalize and connect"
section_order: 3
order: 1
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [providers, api-keys, models, capabilities, fallback, connections]
related: [local-ai-providers, dictation, credentials-and-secrets, jarvis-agents]
---

Use **API Keys & Providers** to decide what powers chat, speech, Computer Use,
Realtime voice, and background Agents. Each capability has its own selection,
so one provider does not have to run everything.

The cards in the live app are the current provider catalog. Providers, models,
voices, prices, and account rules change; use this guide to understand the
choices rather than as a permanent list of every available service.

## Before You Start

- Get credentials only from the provider's official dashboard, linked from its
  card. A consumer subscription and API billing may be separate products.
- Check pricing, regional availability, model access, and usage limits. A live
  **Test** makes a small real request and may be billable.
- Decide which data may leave the computer. Hosted speech recognition receives
  audio, hosted text models receive prompts or transcripts, and hosted voices
  receive the text they speak.

> [!warning] Never put a key, token, password, service-account file, or recovery
> code in chat, voice input, a task, a screenshot, documentation, or
> `jarvis.toml`. Enter it only in the matching credential field.

## Understand the Provider Areas

| Area | What it powers | Important distinction |
|---|---|---|
| **Brain** | Chat and Pipeline voice answers | Choose a provider and a model. |
| **Computer Use** | Planning actions from screenshots | This is a separate global choice and needs an image-capable model. |
| **Voice output** | Speech in Pipeline mode | Choose a voice as well as a provider where offered. |
| **Voice input** | Speech recognition in Pipeline mode and dictation | Cloud and on-device cards are available. |
| **Dictation** | Optional punctuation and wording cleanup after recognition | It improves text; it does not transcribe audio. |
| **Realtime** | One live model that listens and speaks | Model and voice are selected together; Computer Use remains separate. |
| **Agents** | Longer background work | API-key providers and supported coding-CLI subscriptions appear separately. |
| **Advanced** | Optional team proxy, telephony, and classic Wiki model | These do not have to be configured for normal chat. |

The **Pipeline / Realtime** switch changes the voice-provider tabs, not the
whole app. Pipeline joins Voice input, Brain, and Voice output. Realtime uses a
single full-duplex connection. The Computer Use and Agent choices remain
independent in both modes.

Card labels describe state, not a guarantee:

| Label | Meaning |
|---|---|
| **open** | No usable credential or login was found. |
| **ready** | Access was found, but it has not necessarily passed a live request. |
| **active** | This is the selected card for that capability. |
| **Setup needed** | The active capability lacks a usable selection or access method. |
| **Not working** | A live health check found a problem. |

## Connect a Cloud Provider

1. Open **API Keys & Providers**, choose the capability, and read the card's
   access and billing note.
2. Select **Get your key here** and create a key in the official provider
   account. When a card offers two credential types, choose deliberately. For
   example, an AI Studio key and a Vertex AI credential use different Google
   projects and billing. Google keys need no manual endpoint choice: paste
   either an AI Studio key or a Vertex AI express-mode key (both may start
   with `AQ.`) into the same field — the app detects which service issued it
   and routes every capability (brain, tool model, realtime, speech) through
   the matching endpoint automatically.
3. Paste the value into the password field and select **Save**. The field then
   becomes masked. The page receives only whether the credential exists; it
   does not read the saved value back.
4. Select **Set active** or **Use this provider**. Saving the first usable key
   in an empty area may activate it automatically.
5. Choose the model or voice shown on the active card. The picker is the
   authoritative catalog and may also allow a custom model ID.
6. Select **Test**. **Works** means the minimal request succeeded; it does not
   prove every tool, language, model, or long-running workflow.

A card may say it is covered by a shared family key. That is a working setup;
a dedicated key is optional. Dedicated Realtime or worker slots take priority
when present, while compatible shared keys keep single-key installations
working.

Brain and Computer Use changes apply to the next request. Voice input applies
to the next transcription. Voice output switches the running Pipeline when it
can, otherwise the saved choice is used when speech starts again. Activating a
Realtime provider selects Realtime mode and reconnects an active session.

## Use Local Providers

Local cards avoid cloud API billing, but they still need a running engine,
downloaded model, enough storage, and suitable hardware.

- **Ollama** powers the Brain through an Ollama server. Install Ollama and pull
  a tool-capable model outside the app, then check the server URL on the card.
  The default points to this computer; another address sends data to that
  machine instead.
- **Local server (OpenAI-compatible)** connects to a self-hosted server such as
  llama.cpp, LM Studio, vLLM, or another compatible endpoint. Enter its server
  URL. A key is optional unless that server requires one.
- **Whisper** and **Nemotron** provide on-device Voice input. Whisper favors
  multilingual accuracy with a download of roughly 3 GB; Nemotron is the
  smaller, faster CPU-oriented choice at roughly 690 MB.
- **Piper** provides on-device Voice output. Its roughly 200 MB installation
  includes voices for the supported languages.

The speech cards include an in-app **Install** action. Installation runs in the
background while the card reports progress. A card becomes ready only after
the app verifies both the engine and model files on disk. Read
[Local AI Providers](local-ai-providers) for hardware, endpoint, and privacy
tradeoffs.

## Configure Dictation, Agents, and Wiki Providers

**Dictation wording** is optional. Recognition works without it. Choose a
wording provider only if you want punctuation, sentence cleanup, or translation
after transcription. A compatible Brain key may cover the card. Automatic
wording does not silently select an unavailable local server; failure leaves
the safer unpolished text. See [Dictation](dictation).

**Background Agents** have their own provider and model. API cards use normal
provider billing. Subscription cards use installed coding CLIs, currently
including supported OpenAI, Google, and Anthropic login paths. Install the CLI,
select **Connect**, finish the provider's browser or terminal sign-in, then use
the card's **Test**. The selected worker applies to the next mission.

The account panel can keep multiple subscription seats for a supported coding
CLI. Give each account a recognizable label and choose the active one. These
external logins are separate from API keys and may also be selected inside an
Agentic IDE workspace.

**Advanced > Wiki provider** chooses the model used for classic Wiki
maintenance. Leaving it blank follows the normal Brain choice.

UltraWiki has a separate provider screen with four capability slots:

- **Storage** keeps the knowledge data. Local storage remains the working
  floor; changing to a connected cloud store takes effect when the store opens
  again.
- **Embedding** creates the vector space used for semantic search. It has no
  automatic cross-provider fallback. Changing provider or model requires the
  existing corpus to be embedded again, so the app asks before starting.
- **Distillation** turns captured material into concise knowledge. Automatic
  mode uses a ready provider and can cross families.
- **Rerank** improves result ordering. It can stay off and can cross families
  when enabled.

Each UltraWiki slot has its own **Test**, model picker where relevant, and
credential card. Shared keys use the same safe storage and delete warnings as
the main provider screen.

## Replace or Remove Access

Select **Replace**, save the new value, and test again. Replacing a key in the
app does not revoke the old key at the provider; revoke it in the provider
dashboard when rotation requires that.

The delete button removes the saved credential slot. If other cards use the
same slot, the app names them and asks for confirmation. Deleting a slot does
not remove a value supplied by the host environment or `.env`, and it does not
sign out an external CLI. Remove an environment value at its source; use
**Disconnect** for a subscription login.

In-app credentials normally go to the operating system's credential store. If
that is unavailable, the app can use its protected local credential file. Read
[Credentials and Secrets](credentials-and-secrets) before rotating or deleting
shared access.

## How Provider Fallback Works

Fallback is capability-based and feature-specific. The selected provider is a
preference, not permission to use an unsuitable model.

1. The app resolves a dedicated credential, a compatible shared credential, a
   connected subscription, or a keyless local provider.
2. It filters choices for the required capability, such as tools, images,
   transcription, or speech output.
3. Supported paths can move to another connected provider family after a
   missing key, unavailable service, rate limit, or blocked account.
4. If no eligible choice remains, the feature reports failure instead of
   claiming success.

Fallback never copies credentials, guarantees identical output, or preserves
the same speed, price, privacy boundary, model, or voice. UltraWiki embeddings
are intentionally pinned to one vector space; optional Dictation wording and
reranking may simply be skipped. A local endpoint on another computer is local
to your network, not necessarily to this device.

## How It Fits Together

1. A feature identifies the capability it needs.
2. The active card supplies a provider, model or voice, and access method.
3. Capability checks and supported fallback paths choose an eligible runtime.
4. Safety and permission checks still govern actions after a provider answers.
5. Chat, speech, dictation, an action, an Agent result, or Wiki data returns to
   its own feature surface.

Changing the Brain does not silently change Computer Use, speech, Realtime,
Agents, or UltraWiki. Configure and verify each capability you plan to rely on.

## Check That It Works

Open **API Keys & Providers > Brain**, select **Test** on the active card, and
confirm **Works**. Then send a short, non-sensitive chat message and confirm an
answer appears.

Test Voice input, Voice output, Realtime, Computer Use, Agent subscriptions,
and every enabled UltraWiki slot separately. They use different providers and
capabilities even when they share one key.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **ready**, but **Test** fails | Access exists, but the account, model, network, or quota rejected the request | Read the result, check the provider account, and try another eligible family. |
| A local card stays unready | Its engine, model files, or server is unavailable | Use **Install** for local speech, or start the configured Ollama/compatible server and refresh. |
| A deleted card still looks ready | A shared key, environment value, or CLI login still covers it | Read the shared-access note and remove or disconnect the real source. |
| A speech change seems ignored | The current turn began before the live switch, or Voice output needs a new Pipeline | Finish the turn, restart voice, and check the active card again. |
| A subscription card will not connect | The coding CLI is missing, outdated, or signed out | Install the CLI shown on the card, reconnect, then run its **Test**. |
| UltraWiki asks to rebuild vectors | The embedding provider or model changed | Confirm only if you intend to recreate the corpus vector space. |

## Next Steps

- Read [Local AI Providers](local-ai-providers) before choosing an on-device
  model or network endpoint.
- Use [Dictation](dictation) to decide whether recognition and wording should
  be local or hosted.
- Read [Credentials and Secrets](credentials-and-secrets) before rotating or
  deleting a shared key.
- Open [Background Agents](jarvis-agents) before relying on a subscription
  account or long-running mission.
