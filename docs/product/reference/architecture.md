---
title: "How Personal Jarvis Fits Together"
slug: architecture
summary: Follow a request from the user interface through providers, tools, safety checks, events, agents, memory, and output.
section: "Reference"
section_order: 7
order: 5
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [architecture, desktop, web, api, providers, jarvis-agents, data]
related: [providers-and-api-keys, jarvis-agents, safety-and-approvals, control-api-reference]
---

Personal Jarvis is one local supervisor with several ways in. The desktop
window, browser interface, Jarvis CLI, Pipeline and Realtime voice modes, and
connected channels are not separate assistants. They share core services, but
each surface can use only the capabilities available to its client and host.

A direct API request already names the operation to run. For a natural-language
request, the core decides whether to answer through a Brain provider, use a
protected tool, or start a background mission. A Brain provider is the service
and model that generate or route a response. An open window proves that the
local server is reachable, but not that speech, providers, tools, or
Jarvis-Agents are ready.

## The System in One View

The shortest useful model is:

**request surface -> local Jarvis core -> direct operation, provider, tool, or
mission -> live events and saved results -> request surface**

| Part | What it does | Important boundary |
|---|---|---|
| **Request surfaces** | Accept input from the desktop or browser UI, voice, the Jarvis CLI, connected channels, or another trusted client | A surface can expose only the capabilities available on its device and operating system |
| **Local web server** | Serves the UI and its application programming interface (API); normal request-response calls use REST, while persistent WebSocket connections carry live updates | Desktop and headless mode share this server; headless mode has no native window or host microphone pipeline, but an enabled browser voice session can use the browser's microphone and speaker |
| **Shared core** | Applies configuration, language, conversation context, capability checks, routing, and cancellation | It coordinates the product; it is not itself an AI model or external account |
| **Providers and tools** | Supply reasoning, speech, vision, live audio, or a connection to another app or service | A provider sees the content sent to it, and a connected service receives the arguments needed for its action |
| **Jarvis-Agents** | Run substantial work as saved missions in isolated working copies, followed by review | Missions have their own worker selection, lifecycle, and output; they are not long chat replies |
| **Data and output** | Keep conversations, memory, mission events, settings, and generated files in purpose-specific stores | Live events and durable history are different; reconnecting surfaces reload saved state |

## Four Ways a Request Can Run

Jarvis chooses an execution path based on the request and the capabilities that
are ready. These paths can be combined, but they remain separate enough to fail
and recover independently.

| Path | Typical request | What happens |
|---|---|---|
| **Direct local operation** | Open a view, read settings, list documentation, inspect status, or perform a supported app command | The local API or a deterministic handler returns the result. A Brain provider is not required unless that operation explicitly asks one to interpret or generate content. |
| **Brain answer** | Explain something, continue a conversation, summarize available context, or decide which tool fits | The shared Brain manager selects a suitable model, streams the response when supported, and records the turn. Text and Pipeline voice use the same Brain stack, while their saved text and voice records remain distinct sources shown together in Chats. |
| **Protected tool call** | Search, control an app, use a connected service, or change something through a Jarvis tool | A tool-capable model can propose the call. Jarvis evaluates the exact tool and arguments, requests approval when required, then runs the call through the supervised executor. A button or direct API operation can have a separate route-specific confirmation path. |
| **Background mission** | Build a deliverable, change a project, or complete substantial multi-step work | Jarvis saves a mission, plans bounded steps, runs each step in an isolated workspace, reviews the result, and keeps the mission record plus any archived output. |

Pipeline voice adds speech-to-text before the shared Brain path and
text-to-speech after the response has been cleaned for speech. Realtime voice
uses one live-audio provider session. In its normal delegated mode, requests
about your data or actions return to the shared router through one
`jarvis_action` bridge, so tool safety and mission dispatch still apply.
Realtime does not automatically inherit every Pipeline-only feature.

The reply language is separate from the interface language. One resolver chooses
English, German, or Spanish for each turn. An explicit reply-language choice
wins; otherwise a short interjection keeps the established conversation
language, a substantive turn follows detected input, and an unclear turn falls
back to English. The Brain prompt, status phrases, action readbacks, and
text-to-speech voice are expected to use that same decision.

## Eight Layers and Replaceable Edges

Jarvis uses eight layers. The numbers describe dependency direction, not eight
separate processes.

| Layer | Reader-facing role | Examples |
|---|---|---|
| **L7: Interfaces** | Accept requests and show results | Desktop and browser UI, CLI, channels, notifications |
| **L6: Supervisor** | Coordinates a turn or mission | State, routing, Brain manager, mission manager |
| **L5: Harness adapters** | Connect supervised work to an execution environment | Computer Use and Python-script harnesses |
| **L4: Brain** | Generates, routes, or reviews model-backed work | Brain providers and the short acknowledgement tier |
| **L3: Intent and risk** | Classifies a request and applies action policy | Intent choice, risk tier, approval, rate-limit tracking |
| **L2: Speech** | Converts between audio, text, and turn boundaries | Wake detection, voice activity, transcription, speech synthesis |
| **L1: Audio input and output** | Captures and plays audio | Device selection, routing, playback, sound feedback |
| **L0: Operating system and hardware** | Supplies physical capabilities | Display, microphone, speakers, input control, accessibility, optional GPU |

Higher layers reach lower layers through protocols, which are small behavioral
contracts rather than dependencies on one implementation. Components at the
same level communicate through typed events instead of calling across the
architecture sideways.

Replaceable integrations are registered as plugins for wake detection,
speech-to-text, text-to-speech, Brain, Realtime voice, harnesses, tools, and
channels. A turn-detection plugin slot also exists, but no entry-point provider
currently ships in that group. Mission worker backends are selected by the
mission runtime; they are not ordinary provider plugins. A capability is a
usable feature such as calling tools, understanding an image, transcribing
speech, or producing audio.

The shared live event stream is called the EventBus internally. Its events are
immutable records with a trace identifier and a timestamp. The UI bridge,
speech state, recorders, and other subscribers can observe the same turn, and a
subscriber exception is logged without failing the publisher. This bus is
in-process delivery, not durable history. WebSockets forward live updates to
connected clients, while conversations and missions are saved separately.

## Routing and Provider Fallback

Direct REST operations and deterministic handlers do not need a model to choose
their action. A natural-language turn first passes high-confidence local gates,
then the router Brain when model judgment is needed. The router is a dispatcher
with a curated tool set. It can answer, call an allowed direct tool, or use
`spawn-worker` for a mission. Spawn tools are not exposed to mission workers,
which prevents one worker from recursively starting the supervisor path.

For a normal Brain turn, Jarvis starts with the active provider and selected
model. It can try another suitable model and then another credential-ready
provider when the first path has no usable credential, is unavailable, is rate
limited, or is out of credit. Known unusable providers are skipped for the
current session or cooldown. A tool turn still needs a model that can call
tools, and a vision turn needs image understanding. Fallback does not add a
missing capability or make an unavailable account healthy.

Realtime voice has its own credential-aware provider chain. If no Realtime
session opens, the caller can use an available Pipeline or classic browser
voice path. Once a Realtime provider has accepted a meaningful turn, Jarvis does
not replay the captured audio through another path because that could repeat an
action.

If every compatible provider fails, Jarvis returns an unavailable or
provider-down result. It does not treat a local server response as proof that a
remote model completed the turn.

## Commands, Tools, and Safety

The app-command catalog maps each curated command to one mounted REST endpoint.
The web UI, CLI, and `app-command` Brain tool therefore share the endpoint's
schema and behavior. The catalog is a curated subset, not a claim that every
REST route is safe to call through natural language.

Model-proposed tools run through one supervised executor. It evaluates the
specific tool and arguments before execution:

- `safe` actions normally run without confirmation.
- `monitor` actions run with an audit trail, unless a plausibility check asks
  for confirmation.
- `ask` actions wait for the user's decision.
- `block` actions do not run.

A matching blacklist wins. A matching whitelist can downgrade an otherwise
confirming action to `safe`; otherwise the tool's declared tier applies. An
operating-system permission, connected-service scope, route confirmation, and
mission grant remain separate boundaries. Passing one never grants the others.

Mission workers receive a limited, mission-scoped capability grant. Tool
objects and credentials remain with the supervisor, and a worker request still
passes through the same executor. Secret, configuration-mutation, skill, and
recursive mission tools are not exported to workers.

## Jarvis-Agent Missions

A mission has a durable event log and a lifecycle separate from chat. The
mission decomposer can split the goal into bounded steps. Each step gets a fresh
workspace:

- A source-dependent task uses a Git worktree based on the source checkout.
- A repository-independent deliverable can use a lean, empty Git workspace.

The worker writes only in that workspace. The reviewer, called the Critic,
checks the captured work and can approve, reject, or request a correction. The
worker and Critic loop is capped at three rounds. Approved files and useful
partial work are copied into the mission archive before disposable workspaces
are cleaned up.

Review is a quality gate, not a guarantee that an output is correct. A worker,
Critic, budget, safety check, or provider can fail. The mission records that
failure, and Outputs can mark retained partial work as needing review. A task
that needs the Personal Jarvis source tree also needs Git and a real source
checkout; a packaged installation without Git history cannot run that kind of
source-editing mission.

The Jarvis-Agents view shows live agent activity. Mission history and archived
Outputs are the durable sources to use after a reconnect or restart.

## Readiness and Platform Limits

Jarvis deliberately shows its interface before every heavy subsystem finishes
warming. A feature can report **getting ready**, return a temporary `503`, or
show a setup message while the rest of the app already works. The health route
proves the web boundary, not a Brain, voice, tool, or mission provider.

Windows, macOS, Linux desktop, and headless Linux share the web server and core.
Startup probes the host's display, terminal, accessibility, hotkey, cursor, and
elevation capabilities. Missing optional components or permissions should
produce an unavailable state or a clearly reported no-op, not a claim that the
action succeeded.

Headless mode keeps the browser UI, REST and WebSocket APIs, text chat, Docs,
missions, and file work. It has no host desktop window, local wake listener,
global shortcut, overlay, or physical Computer Use target. Browser voice is a
separate path: it needs an enabled voice surface, browser microphone permission,
working providers, and HTTPS when the browser is on another computer. Linux
Wayland can also prevent global input and desktop control even when a display is
present.

## Data, Events, and Trust Boundaries

Jarvis separates live state from durable records.

| Data or boundary | Where it belongs | What can leave the local device |
|---|---|---|
| **Chats and memory** | Local conversation, profile, Wiki, and workspace stores | A provider receives only the request and context Jarvis selects for that turn |
| **Missions and Outputs** | A durable event store plus archived workspace snapshots | Worker and reviewer providers receive the instructions and content needed for their work |
| **Product Docs** | Local Markdown indexed and served through the Docs API | Reading bundled documentation needs no Brain provider |
| **Settings and credentials** | Atomic local configuration plus the protected credential layer | Integrations receive the settings and secret needed to authenticate; the UI normally receives status, not the saved secret |
| **Live state** | In-process events forwarded to clients through WebSockets | Authenticated remote surfaces receive the updates allowed for their session |

> [!warning] Local orchestration does not make a remote provider or connected
> service local. Review the provider's data policy and the account scopes before
> sending private conversation, memory, files, screen content, or contact data.

Remote access adds another boundary. A server that accepts connections from
another computer requires a Control API key. Protected routes then use the
authenticated app session or the credential required by that route. Provider
credentials cannot replace the Control API key.

## How It Fits Together

Every surface enters one core, which selects a safe path and returns the result.

## Check That It Works

Use one harmless chat turn to check the main shared path:

1. Open **Chats**, create a conversation, and ask a short, non-sensitive
   question.
2. Confirm that your message appears, Jarvis enters a working state, and a real
   answer returns through the Brain provider stack.
3. Open another view, return to **Chats**, and reopen the conversation. Confirm
   that both messages are still present.

This checks the UI, local server, live event flow, Brain routing, provider
selection, and text history together. A provider setup or unavailable message
proves that the local path answered, but it does not prove the provider leg.
Tools, voice, and Jarvis-Agents each need their own feature check.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The window opens, but a view says **getting ready** or returns `503` | The server has painted the interface while that subsystem is still warming | Wait briefly and retry the view once. If it persists, check the feature's status rather than restarting every component blindly. |
| Health is online, but chat, voice, or missions are unavailable | Health proves the web server, not every provider or runtime | Test the relevant category in **API Keys & Providers** and check the dedicated feature page. |
| A chat message is saved, but no normal answer appears | The Brain failed to build or no connected provider could serve the turn | Open **API Keys & Providers > Brain**, test the active card, and try a ready provider from another compatible family. |
| An action waits, is denied, or has no effect | A safety decision, operating-system permission, service scope, or route-specific confirmation blocked it | Review the exact request and permission. Do not bypass a block; use [Safety and Approvals](safety-and-approvals) to find the missing boundary. |
| Browser voice is absent on a headless host | Browser voice is disabled, the browser lacks microphone permission, the connection is not secure, or a speech provider is unavailable | Keep using text, then check the browser permission, HTTPS, voice mode, and matching provider cards. |
| A mission is missing, stays active, or finishes without the expected file | The request was not delegated, the worker or Critic is unavailable, review is still running, or only partial work was archived | Open **Jarvis-Agents** for live state and **Outputs** for the durable record, then follow [Jarvis-Agents](jarvis-agents). |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) to connect services,
  understand capabilities, and see how compatible fallback works.
- Read [Jarvis-Agents](jarvis-agents) to follow the isolated worker, review, and
  output path for substantial background work.
- Read [Safety and Approvals](safety-and-approvals) to understand why a tool
  runs, waits for a decision, or is blocked.
- Read the [Control API Reference](control-api-reference) to build a trusted
  client against the same local control plane used by Jarvis surfaces.
