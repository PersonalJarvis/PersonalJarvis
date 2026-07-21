---
title: "Providers and API Keys"
slug: providers-and-api-keys
summary: Connect the services you choose, see which capabilities they provide, and learn where fallback is available.
section: "Personalize and connect"
section_order: 3
order: 1
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [providers, api-keys, models, capabilities, fallback, connections]
related: [credentials-and-secrets, languages-and-voices, troubleshooting, tasks-and-reminders]
---

Use **API Keys & Providers** to choose the services that power each part of
Jarvis. Chat, actions, speech, Realtime voice, and longer Jarvis-Agent missions
can use different providers.

A saved credential only means that Jarvis can find a key or login. Make the
provider active, then test or use the feature before depending on it.

## Before You Start

- Get API keys only from the provider's official dashboard. A consumer plan and
  an API account can be separate products, even when they use the same login.
- Check the provider's prices and account limits. A live provider test makes a
  small real request and may count as billable usage.
- Never put a key, token, password, or recovery code in chat, voice input, a
  task, a screenshot, documentation, or `jarvis.toml`.

## Choose What Each Provider Powers

A **provider** is the service Jarvis connects to. A **model** is one engine from
that service. A **capability** is something that model can do, such as accept an
image, call a tool, transcribe speech, or produce audio.

These are the provider choices currently shown in the app:

| View | Selectable providers | Access | What it powers |
|---|---|---|---|
| **Brain** | Google Gemini, OpenRouter, xAI Grok, Claude (API-Key), OpenAI, NVIDIA NIM | API key | Chat and voice answers |
| **Tool Model** | The same API providers as Brain | API key | Tool routing, actions, and Computer Use |
| **Voice Output** | OpenRouter, ElevenLabs, Gemini Flash TTS, xAI Text to Speech, Cartesia, Inworld | API key | Speech in Pipeline voice |
| **Voice Input** | Groq STT, OpenAI Whisper STT, OpenRouter STT | API key | Transcription in Pipeline voice |
| **Realtime** | OpenAI Realtime, Gemini Live | API key | One live audio stream for listening and replying |
| **Jarvis-Agents** | Claude, Gemini, OpenAI, OpenRouter, xAI Grok, NVIDIA NIM, OpenAI Codex, Antigravity | API key or supported subscription login | Longer background missions |
| **Advanced** | Team key proxy, telephony, and Wiki provider | Varies by integration | Optional connections |

The model picker is the current source for available models and voices. A
provider can offer both capable and unsuitable models. The Tool Model needs
tool support, and Computer Use also needs image input. The picker removes
models known to be text-only, but missing capability metadata is not proof that
a model will work. The runtime still checks the capability when it can.

**Pipeline** uses Voice Input, the Brain, and Voice Output as three separate
steps. **Realtime** uses one full-duplex model for the audio conversation.
Realtime is a research preview. Many tools and features are unavailable, and
screen actions are delegated to the separate Tool Model.

The voice-engine switch changes which tabs you see. Pipeline shows Brain, Tool
Model, Voice Output, and Voice Input. Realtime shows Realtime and Tool Model.
Both modes also show Jarvis-Agents, your install's control key, and Advanced.

## Connect and Activate a Provider

1. Open **API Keys & Providers**. Choose **Pipeline** or **Realtime**, then open
   the tab for the feature you want to configure.

2. Choose a provider and read its access and billing note. For example, the
   Gemini cards distinguish an AI Studio API key from a Vertex AI service
   account because they bill separate Google projects.

3. Select **Get your key here** to open the official provider dashboard. For a
   supported Jarvis-Agent subscription, install the named command-line tool and
   use **Connect** to start its sign-in flow. Current subscription choices use
   ChatGPT through Codex, Google through Antigravity or the Gemini CLI, and
   Claude through the Claude CLI.

4. Paste an API key into its password field and select **Save**. After a
   successful save, the field collapses to a masked value. Jarvis returns only
   whether a credential exists; it never sends the saved value back to the
   page.

   A related card may instead say that a shared family key already covers it.
   In that case, adding a dedicated key is optional. Realtime and Jarvis-Agent
   keys have dedicated slots, but compatible family keys remain fallback paths
   for existing and single-key installations.

5. Select **Set active**. If the category has no active provider yet, saving
   its first key can activate that provider automatically. Brain and Tool Model
   are separate choices, as are both speech categories, Realtime, and
   Jarvis-Agents.

6. Choose a model or voice where the card offers one. Brain and Tool Model
   choices are stored per provider. Voice Input and most Voice Output choices
   appear only on the active connected card. Realtime has separate model and
   voice selectors.

7. Select **Test** on a Brain, Tool Model, Voice Input, Voice Output, or
   Realtime card. The result appears on that card. A provider test proves a
   minimal request, not every model capability or tool workflow.

   Jarvis-Agent subscription cards have a separate **Test** action for the
   installed command-line tool and its login. API-backed Jarvis-Agent cards
   show credential readiness but do not have a live provider test. Run a short,
   non-sensitive mission to verify that worker path.

Brain and Tool Model switches apply without an app restart. A Realtime switch
also selects Realtime mode and reconnects an active voice session when
possible. A Voice Input change applies at the next voice or app start. Voice
Output tries to switch the running Pipeline; if no Pipeline is active or the
live switch fails, it applies at the next start. A Jarvis-Agent switch applies
to the next mission.

### Understand the Status Labels

| Label | What it means |
|---|---|
| **open** | No usable key or login was detected for this card. |
| **ready** | A key or login was detected, but it may still be invalid, expired, rate limited, or out of credit. |
| **active** | This is the saved or currently resolved choice for the category. It is not a guarantee that a request will succeed. |
| **Setup needed** | The active category has no usable credential or selection. |
| **Not working** | A live health check found a problem with the active provider or model. |

The manual test uses these more specific results:

| Test result | Meaning |
|---|---|
| **Works** | The provider completed the minimal live request. |
| **No key set** | Jarvis could not resolve a usable credential. |
| **Key invalid** | The service rejected the credential. |
| **Out of credits** | The account has no usable credit or quota. |
| **Rate limited** | The service is temporarily throttling requests. |
| **Model unavailable** | The selected model cannot be used by this account or endpoint. |
| **Unreachable** | The service did not answer within the test limit or could not be reached. |
| **Integration error** | The adapter or response failed in another way. |

## Replace or Remove Access

Select **Replace** to enter a new value, save it, and test again. Replacing a
key does not revoke the old key at the provider. Revoke it separately in the
provider's dashboard when rotation requires that.

Use the delete button to remove the saved credential slot. If other cards read
the same slot, Jarvis names those cards and asks for confirmation. If the
operating system's credential store cannot confirm deletion, Jarvis reports an
error instead of claiming the key is gone.

Deletion does not remove a value supplied by the host environment or `.env`,
and it does not disconnect a subscription login owned by an external
command-line tool. Remove an environment-provided value at its source. Use
**Disconnect** on the relevant subscription card for a CLI login.

Jarvis normally stores an in-app key in the operating system's credential
store. If that store is missing or fails, such as on a headless server, Jarvis
uses a local `credentials.json` file with owner-only permissions. Read
[Credentials and Secrets](credentials-and-secrets) for the storage order and
its security limits.

## How Provider Fallback Works

Fallback is specific to each feature. It does not grant access, copy keys
between accounts, or guarantee the same model, voice, price, or capability.

1. Jarvis resolves a usable credential for the provider family. A dedicated
   key wins on its own surface; compatible shared keys can cover related
   surfaces when the dedicated slot is empty.
2. Jarvis builds a chain for the capability the request needs. The selected
   provider is a preference, but a separate deep model or Tool Model can lead
   when the request requires it.
3. Supported paths skip providers known to be missing credentials, unavailable,
   rate limited, or blocked by account limits. They can continue with another
   connected provider family.
4. When no suitable option remains, the feature reports or records a failure
   instead of claiming success.

The current paths differ in important ways:

- Brain and Tool Model requests can cross provider families. A fallback answer
  does not necessarily change the provider saved as active.
- Pipeline Voice Input and Voice Output choose another keyed family when the
  configured provider has no usable key. Voice Output also supports runtime
  fallback in supported adapters. Voice Input can use local Faster-Whisper as a
  last resort when its optional local dependencies are installed; it is not a
  selectable card.
- A Realtime session tries credential-ready Realtime providers in order. If no
  Realtime session can serve the call, the voice surface can use Pipeline when
  that full path is available.
- A Jarvis-Agent mission can move to another reachable worker family when the
  selected worker is missing, expired, in quota cooldown, or otherwise unusable.
- A scheduled **agent** task currently calls its active Brain provider and
  selected fast or deep model directly. If that call fails, the task records
  **failed**; it does not run the normal chat fallback chain.

## How It Fits Together

| Connected feature | Relationship to providers | Where to continue |
|---|---|---|
| **Credentials and Secrets** | Stores, replaces, and removes provider keys. Provider lists receive presence signals, not the values. | Read [Credentials and Secrets](credentials-and-secrets) before rotating or removing access. |
| **Languages and Voices** | The reply-language setting chooses the language. Voice Output or Realtime supplies the spoken voice. | Read [Languages and Voices](languages-and-voices) to align language and voice choices. |
| **Phone Calls** | Telephony has its own account, then uses the configured Pipeline speech and Brain path. | Read [Phone Calls](phone-calls) before adding telephony credentials. |
| **Jarvis-Agents** | Heavy missions have their own worker and model choice, plus a cross-family worker fallback. | Read [Jarvis-Agents](jarvis-agents) before running a long mission. |
| **Tasks and Reminders** | Scheduled agent tasks use an isolated Brain turn and do not inherit normal chat fallback. | Read [Tasks and Reminders](tasks-and-reminders) before relying on unattended work. |

The usual request path is: your request, the required capability, an eligible
provider and model, safety or tool checks, then an answer, action, speech, or
mission result.

## Check That It Works

1. Open **API Keys & Providers > Brain**.
2. On the active provider card, select **Test** and confirm that it shows
   **Works**.
3. Open **Chats** and send `Reply with: provider check complete.`
4. Confirm that a matching answer appears.

This checks the active Brain credential, selected model, provider connection,
and chat path. Verify speech, Realtime, Tool Model actions, and Jarvis-Agent
missions separately because they use different selections and capabilities.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A card is **ready**, but **Test** fails | Ready confirms credential presence, not account health | Follow the specific test result. Check the provider account, model access, quota, and service status. |
| **Key invalid**, **Out of credits**, **Rate limited**, or **Model unavailable** | The service answered but blocked the request | Fix the account or model issue, or activate a ready provider from another compatible family. |
| **Unreachable** or **Integration error** | The service, network, adapter, or app connection did not complete the test | Retry once, check the provider's status and your network, then use [Troubleshooting](troubleshooting) if other providers also fail. |
| A deleted card still looks ready | A shared family key, environment value, or CLI login still covers it | Read the card's shared-key note and remove or disconnect the actual credential source. |
| A speech change is not used yet | Voice Input needs a new voice start; Voice Output could not switch the current Pipeline live | End and start voice again, then confirm the runtime status names the expected engine. |
| Realtime is selected, but the current session says Pipeline | The Realtime handshake failed or no Realtime provider was usable | Test the Realtime card, then test all three Pipeline categories before trying again. |
| A Jarvis-Agent mission uses another provider | The selected worker was not reachable or was in a quota cooldown | Reconnect or replace that worker's access, then run a short mission and review its provider status. |

## Next Steps

- Read [Credentials and Secrets](credentials-and-secrets) to understand storage,
  replacement, deletion, and the local-file fallback.
- Use [Languages and Voices](languages-and-voices) to choose the reply language
  and spoken voice after the providers work.
- Read [Jarvis-Agents](jarvis-agents) before selecting a worker or relying on
  mission fallback.
- Open [Troubleshooting](troubleshooting) for app-wide startup, connection, and
  provider recovery checks.
