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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [architecture, desktop, web, api, providers, jarvis-agents, data]
related: [providers-and-api-keys, jarvis-agents, safety-and-approvals, control-api-reference]
---

Personal Jarvis is one local supervisor with several ways in. The desktop
window, browser interface, command line, voice pipeline, and connected channels
are not separate assistants: they reach the same core services and use the same
configured capabilities where their host supports them.

The core decides whether a request needs a local app operation, an answer from a
Brain provider (the service and model that generate a response), a protected
tool call, or a background mission. Understanding that choice makes failures
easier to place: an open window proves that the local server is reachable, but
it does not prove that speech, providers, tools, or Jarvis-Agents are ready.

## The System in One View

The shortest useful model is:

**request surface -> local Jarvis core -> direct operation, provider, tool, or
mission -> live events and saved results -> request surface**

| Part | What it does | Important boundary |
|---|---|---|
| **Request surfaces** | Accept input from the desktop or browser UI, voice, the Jarvis CLI, connected channels, or another trusted client | A surface can expose only the capabilities available on its device and operating system |
| **Local web server** | Serves the UI and its application programming interface (API); normal request-response calls use REST, while persistent WebSocket connections carry live updates | Desktop and headless server mode share this server, but headless mode has no native window, microphone pipeline, or desktop controls |
| **Shared core** | Applies configuration, language, conversation context, capability checks, routing, and cancellation | It coordinates the product; it is not itself an AI model or external account |
| **Providers and tools** | Supply reasoning, speech, vision, live audio, or a connection to another app or service | A provider sees the content sent to it, and a connected service receives the arguments needed for its action |
| **Jarvis-Agents** | Run substantial work as saved missions in isolated working copies, followed by review | Missions have their own worker selection, lifecycle, and output; they are not long chat replies |
| **Data and output** | Keep conversations, memory, mission events, settings, and generated files in purpose-specific stores | Live events and durable history are different; reconnecting surfaces reload saved state |

The local API is the common control plane. The desktop window displays the web
interface inside a native shell. A browser can display the same interface, and
the CLI calls authenticated API operations instead of reproducing each feature
inside the terminal. Voice enters through a speech pipeline and joins the same
Brain and event flow after transcription.

## Four Ways a Request Can Run

Jarvis chooses an execution path based on the request and the capabilities that
are ready. These paths can be combined, but they remain separate enough to fail
and recover independently.

| Path | Typical request | What happens |
|---|---|---|
| **Direct local operation** | Open a view, read settings, list documentation, inspect status, or perform a supported app command | The local API or a deterministic handler returns the result. A Brain provider is not required unless that operation explicitly asks one to interpret or generate content. |
| **Brain answer** | Explain something, continue a conversation, summarize available context, or decide which tool fits | The shared Brain manager selects a suitable model, streams the response when supported, and records the turn. Text and Pipeline voice use the same Brain stack, while their saved text and voice records remain distinct sources shown together in Chats. |
| **Protected tool call** | Search, control an app, use a connected service, or change something through a Jarvis tool | A tool-capable model can propose the call. Jarvis evaluates the exact tool and arguments, requests approval when required, then runs the call through the supervised executor. A button or direct API operation can have a separate route-specific confirmation path. |
| **Background mission** | Build a deliverable, change a project, or complete substantial multi-step work | Jarvis saves a mission, plans bounded steps, gives each step an isolated workspace, runs a Jarvis-Agent, reviews the draft, and archives an approved result or an honest failure. |

Pipeline voice wraps these paths with two additional stages: a speech-to-text
provider turns audio into text before the shared core runs, and a text-to-speech
provider turns the final, voice-safe response into audio afterward. Realtime
voice uses a different live-audio provider path and does not automatically have
every capability of the Pipeline path.

## Shared Core and Replaceable Edges

Jarvis depends on contracts rather than one provider implementation. Brain,
speech, tool, channel, worker, and Realtime integrations are plugins that state
what they can do. A capability is a usable feature such as calling tools,
understanding an image, transcribing speech, or producing audio.

For a normal Brain turn, Jarvis starts with the active provider and selected
model. It can try another suitable model or another connected provider family
when the first path is missing a credential, unavailable, rate limited, or out
of credit. A tool turn also needs a model that can actually call tools; a vision
turn needs image understanding. Fallback never grants a capability that the
replacement does not have.

The shared live event stream, called the EventBus internally, lets the UI,
speech state, recorders, and other interested features observe the same turn.
Events carry a trace identifier so related work can be followed across layers.
A failing observer is isolated from the others, but this stream is in-process
delivery, not durable history. Conversations and missions are saved separately,
and a reconnected UI fetches their current state from those stores.

Jarvis deliberately shows its interface before every heavy subsystem finishes
warming. This makes the window responsive sooner, but it creates more than one
readiness level:

- **Server ready** means the UI and health route answer.
- **Brain ready** means a usable provider stack has finished loading.
- **Voice ready** means capture, transcription, and speech output are usable.
- **Mission ready** means the mission store, worker runner, and review path are
  available.

A feature can therefore report **getting ready**, return a temporary `503`, or
show a setup message while the rest of the app already works. Persistent
unavailability is a subsystem problem, not evidence that every layer is down.

## Data, Events, and Trust Boundaries

Jarvis separates data by purpose so one live connection is not treated as the
source of truth for everything.

| Data or boundary | Where it belongs | What can leave the local device |
|---|---|---|
| **Text and voice history** | Local conversation stores, with separate records presented together in Chats | A provider receives the current request and the context Jarvis includes for that turn |
| **Memory, profile, and Wiki** | Local database entries and workspace files | Only the relevant context selected for a provider or connected action is sent onward |
| **Mission history** | A dedicated mission event store that survives UI reconnects and app restarts | The selected worker and reviewer providers receive the mission instructions and review context they need |
| **Mission files** | Isolated working copies during execution, then archived and user-visible Outputs | A worker tool or connected service can receive file content only when the mission is allowed to use it |
| **Credentials** | The protected credential layer chosen for the installation, separate from chat and mission prompts | The credential is used to authenticate its service; the UI normally receives status rather than the saved value |
| **Live state** | In-process events and WebSocket updates | Authenticated remote surfaces can receive the live updates their session is allowed to view |

> [!warning] Local orchestration does not make a remote provider or connected
> service local. Review the provider's data policy and the account scopes before
> sending private conversation, memory, files, screen content, or contact data.

The local server also has its own security boundary. Desktop use normally stays
on the same computer. A server configured to accept connections from another
computer must have a Control API key, and protected requests use either an
authenticated app session or the credential required by that route. Provider
credentials and the Control API key have different purposes and cannot replace
each other.

## How It Fits Together

Follow one request from start to finish:

1. **A surface accepts the request.** The desktop or browser UI, voice pipeline,
   CLI, channel, task, or integration supplies text, audio, or a direct command.
2. **The local boundary checks the caller.** UI and API traffic passes host,
   origin, session, and Control API checks that apply to that surface. Voice and
   local events enter through their own trusted runtime paths.
3. **Jarvis prepares one turn.** It resolves the output language, loads the
   conversation context that belongs to the request, and checks which local and
   connected capabilities are available.
4. **The core chooses the execution path.** A deterministic operation can run
   locally. A conversational turn goes to a Brain provider. An action becomes a
   supervised tool call. Substantial work becomes a saved Jarvis-Agent mission.
5. **Capability and fallback rules select an edge.** Jarvis prefers your active
   selection, skips a connection already known to be unusable, and can try a
   compatible provider family. If nothing fits, it reports the missing or
   unavailable capability instead of inventing a result.
6. **Permissions and safety apply where work has effects.** Operating-system
   access, service scopes, tool risk, approval, and mission grants are separate
   checks. Passing one does not silently pass the others. Read [Safety and
   Approvals](safety-and-approvals) for the exact action boundary.
7. **Results return through live events and direct responses.** The active
   surface receives text, audio, progress, state changes, or an error. A mission
   can continue after the originating conversation is free again.
8. **Durable features save their own record.** Chats save messages, voice saves
   session turns, memory keeps selected long-term context, and missions keep
   events plus archived outputs. The live UI can then reconnect without turning
   the event stream into a database.

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
| A mission is missing, stays active, or finishes without the expected file | The request was not delegated, the worker path is unavailable, review is still running, or no deliverable was archived | Open **Jarvis-Agents** for live state and **Outputs** for the durable record, then follow [Jarvis-Agents](jarvis-agents). |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) to connect services,
  understand capabilities, and see how compatible fallback works.
- Read [Jarvis-Agents](jarvis-agents) to follow the isolated worker, review, and
  output path for substantial background work.
- Read [Safety and Approvals](safety-and-approvals) to understand why a tool
  runs, waits for a decision, or is blocked.
- Read the [Control API Reference](control-api-reference) to build a trusted
  client against the same local control plane used by Jarvis surfaces.
