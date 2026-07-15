---
title: "Troubleshooting"
slug: troubleshooting
summary: "Diagnose startup, connection, provider, voice, permission, and extension problems using safe checks before changing configuration."
section: "Privacy, safety, and support"
section_order: 6
order: 5
diataxis: troubleshooting
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [troubleshooting, diagnostics, recovery, startup, providers, voice, connections, support]
related: [install-personal-jarvis, providers-and-api-keys, audio-and-wake-word, platform-support]
---

Start with the smallest check that can identify the failing part. Personal
Jarvis combines a local app, optional online providers, operating-system
permissions, connected tools, and background work. One of those parts can be
unavailable while the rest of the app is healthy.

This guide uses visible app controls. Ordinary diagnosis should not require
deleting data, editing configuration, exposing a local service, or reinstalling.

## Start With the Safest Check

1. **Decide whether one feature or the whole app is affected.** Open an
   unrelated view. If Docs does not load, try Chats or Settings. If chat fails,
   try the provider card's **Test** button.
2. **Read the exact status before changing anything.** **Starting**, **Voice
   starting**, **Offline**, **Key invalid**, **Reconnect needed**, and
   **Permission required** point to different layers. Record the wording, not
   just “it does not work.”
3. **Use one built-in Retry, Refresh, Recheck, or Test action.** It repeats a
   focused check without changing unrelated settings.
4. **Change only the failing layer.** A working chat provider does not prove
   that speech, Realtime, the Tool Model, or Jarvis-Agents can work. Test the
   category that owns the symptom.
5. **Restart only when the app asks, or when several views have lost the local
   connection.** Finish active work first. The global **Restart** control
   protects running Jarvis-Agent missions; forcing it can stop them.
6. **Preserve a small, safe record if the problem remains.** Note the view,
   visible status, approximate time, and the shortest steps that reproduce the
   problem.

> [!warning] Never put an API key, token, password, recovery code, private
> configuration file, or unreviewed log into a support message. Screenshots,
> transcripts, raw run exports, and logs can contain conversation text, file
> names, account details, and personal paths. Share only a narrow, reviewed,
> redacted excerpt.

Use this map to choose the first focused check:

| What is affected? | First place to look | First safe action |
|---|---|---|
| The app window or every view | Global status and startup screen | Wait for the current start once; then use one normal restart or quit and reopen |
| Docs only | **Docs** loading or error panel | Select **Try again**; use the online Docs link if needed |
| Chat answers | **API Keys & Providers > Brain** | Run **Test** on the active provider |
| Wake, listening, or spoken replies | **Settings > Audio devices / Wake Word** and the relevant provider category | Rescan devices or run **Test wake word** |
| An action is denied | **Settings > Privacy permissions** and the approval message | Grant only the requested access or approve the intended action |
| A plugin, MCP server, or CLI | **Skills, Plugins & MCPs** or **CLIs** | Refresh, reconnect, reload, or recheck that one connection |
| A task, mission, or generated file | **Tasks**, **Jarvis-Agents**, and **Outputs** | Read its status or timeline before retrying |

## Troubleshooting

### The App Does Not Open, Stays Blank, or Shows Offline

- A name, spinner, or startup message means the desktop shell is alive and the
  local app is still starting. Let that start finish once. **Voice starting**
  applies only to voice readiness; text chat can already be available.
- **Starting** in the app means its live local connection is warming up.
  **Offline** across several views means that connection was lost. If it does
  not recover, use the global **Restart** control and confirm once.
- If one view displays a crash panel, select **Back to Chats**, then reopen the
  view. A single view crash does not require an immediate app restart.
- If the entire window remains blank or unresponsive and no recovery control
  appears, use the normal application **Quit** action and reopen the registered
  app. Do not start a cycle of repeated restarts.
- If the app never opens after installation, or its normal launcher is missing,
  continue with [Install Personal Jarvis](install-personal-jarvis) and
  [Platform Support](platform-support). A headless host does not provide the
  same desktop window, audio, or overlay surfaces.

### Docs Keep Loading or Show an Error

The desktop Docs library is local. On its first open after an install or update,
Jarvis may need a moment to build the index. While that happens, the page shows
an indexing message and visible placeholders instead of an empty screen.

- If loading reaches the error panel, select **Try again**. Documentation
  requests stop waiting after a bounded timeout, so the control should not
  remain on an endless spinner.
- If only Docs fails, do not replace a provider key or restart the entire app
  first. Providers do not build the local documentation index.
- Use **Open redesigned online docs** to open the hosted copy at
  [personaljarvis.ai/docs](https://personaljarvis.ai/docs/) while the local
  index recovers.
- If the sidebar is visible but no title matches, clear its filter. Use the
  magnifying-glass search for full-text results across page content.
- If both local and hosted Docs fail, separate the checks: the local library
  depends on the Jarvis app; the hosted copy depends on the browser and
  internet connection.

### Chat Does Not Answer or Uses the Wrong Provider

Open **API Keys & Providers > Brain** and inspect the active card.

- **Saved**, **active**, and **Works** are separate states. A stored credential
  does not prove that the selected account has credit, model access, or a
  working connection.
- Run **Test**. **Key invalid**, **Out of credits**, **Rate limited**, **Model
  unavailable**, **Unreachable**, and **Integration error** each name a more
  specific recovery path. Correct that account or model, or activate another
  ready provider family that supports the same capability.
- If ordinary chat works but a tool action fails, test the **Tool Model** and
  the connected tool. A Brain answer does not prove that Computer Use, a
  plugin, or an MCP call is ready.
- If the message field says **Starting** or **Offline**, address the local app
  connection before replacing a provider credential.
- Enter or replace credentials only in the protected provider field. Never
  paste them into chat, voice, tasks, Docs, or a support report.

### Voice Does Not Hear, Understand, Wake, or Speak

Treat voice as a chain and check the first broken step.

| Symptom | Likely layer | Safe check |
|---|---|---|
| **Voice starting** remains visible | Voice components are still warming or could not become ready | Wait once; Chats remain usable. If it never becomes ready, check microphone permission and the selected voice providers. |
| The wake phrase does nothing | Microphone, permission, wake engine, phrase, or spoken language | Open **Settings > Wake Word**, keep **Auto**, and run **Test wake word**. |
| The Bar listens but no useful text appears | Input device, speech recognition, or Realtime input | Rescan and select the intended microphone; test **Voice Input** or the selected Realtime connection. |
| The transcript is correct but no reply is heard | Output device or Voice Output | Check **Settings > Audio devices > Voice output**, then test the active **Voice Output** provider. |
| Realtime cannot start or ends early | Realtime provider, browser audio, or session connection | End the call, test the Realtime card, and start a new session. Confirm whether the runtime used Pipeline fallback. |
| The phrase activates during normal conversation | The phrase is too short or common | Use a distinct two- or three-word phrase with a prefix; there is no sensitivity slider. |

On macOS, use **Settings > Privacy permissions** for microphone access and
restart only when the card asks. A Realtime session started in a browser uses
that browser's microphone permission and device, not the native desktop audio
picker. If a voice turn was recorded, **Transcription** and **Run Inspector**
can show whether speech recognition, a provider, a tool, or spoken output was
the last recorded stage.

### A Permission or Approval Blocks the Action

An operating-system permission and a Jarvis safety approval answer different
questions. The operating system decides whether Jarvis may use the microphone,
screen, accessibility tree, or input control. Jarvis then decides whether a
particular action is safe, needs confirmation, or must be blocked.

- Open **Settings > Privacy permissions** and act on the named row. Use
  **Request** or **Open Settings**, then return to Jarvis and restart if shown.
- If a confirmation asks about a specific action, review the target and effect
  before approving. Do not weaken a safety rule merely to make an unclear
  request proceed.
- If restart is refused, active Jarvis-Agent missions are being protected.
  Finish or stop them intentionally before trying again.
- A status of **Allowed** proves the operating-system grant, not that every
  later action will be approved or supported.

### A Plugin, MCP Server, or CLI Is Disconnected

- For a packaged plugin, use **Refresh catalog** and then **Reconnect** when
  shown. Complete sign-in with the intended account and return to the app.
- For an MCP server, review its status, reload it, and try one harmless
  read-only action. Its process, URL, authorization, or tools can be
  unavailable. Do not paste a missing secret directly
  into `mcp.json`; prefer the packaged plugin when one exists.
- For a CLI connection, select **Recheck status** after installation or login.
  **Connected** can confirm account discovery without proving that a particular
  command has permission, so test a supported read-only action first.
- If every connection reports that the backend is unreachable, return to the
  app-wide startup check. If only one connection fails, repair that service
  rather than restarting unrelated features.

### A Task, Jarvis-Agent, or File Appears Stuck or Missing

- In **Tasks**, select **Refresh** and expand the card's Timeline. A scheduled
  task needs Jarvis to be running when it becomes due; a failed task can point
  to its Brain, plugin, permission, or action layer.
- In **Jarvis-Agents**, read the health banner and current mission state before
  restarting. Longer work can be queued, running, reviewing, or correcting.
  Test **API Keys & Providers > Jarvis-Agents** if no worker is reachable.
- Use **Outputs** as the durable place to find mission results. The live
  Jarvis-Agents board is not a permanent history, and Outputs currently shows
  the newest cards rather than every historical mission.
- **This session has no saved files** means the run produced no archived file
  or failed before archiving. Ask for a named deliverable and success criteria
  before starting a new attempt.
- **Restart** on an Outputs card starts a new linked mission attempt. It is not
  the same as the global app **Restart** button.
- **Open** and **Reveal in folder** diagnose file-opening problems without
  rerunning the mission. If a preview is shortened or marked binary, use a
  suitable local app.

## When to Seek Help

Ask for help when a focused test still fails after one retry, the problem
returns after one normal restart, the app repeatedly crashes, or a record
appears lost. Use **Feedback** to open the project's public Discord bug forum;
an invite button is available if needed.

Include only what helps another person reproduce the problem:

- Personal Jarvis version, operating system, and whether you use the desktop
  app, a browser, or a headless host;
- the affected view and exact visible status or error wording;
- the shortest numbered steps, expected result, and actual result;
- the approximate time and whether the problem affects one feature or all
  views; and
- one redacted screenshot or a small reviewed excerpt when it adds evidence.

Do not attach a complete log, raw configuration, `.env` file, credential-store
export, full transcript, or raw Run Inspector JSON unless a trusted maintainer
requests a specific narrow part and you have reviewed it. If a credential was
ever exposed, revoke or rotate it with the service first; redacting the later
message does not make the old value safe again.

## How It Fits Together

| Area | Role in troubleshooting | Important boundary |
|---|---|---|
| [Find Help in the App](find-help-in-the-app) | Uses the Docs overview, sidebar filter, full-text search, related guides, and online copy to find the right procedure | Reading Docs does not test or change the affected feature |
| [Sessions and Run Inspector](sessions-and-run-inspector) | Provides read-only evidence for recorded voice turns: transcript, outcome, timing, providers, tools, and errors | It does not contain the full later lifecycle of standalone tasks, missions, or output files |
| [Settings](settings-and-appearance) | Owns audio devices, wake checks, permissions, app startup, shortcuts, and restart-required choices | Change one relevant control at a time so the result remains attributable |
| [Privacy and Local Data](privacy-and-local-data) | Explains what transcripts, missions, outputs, audit data, exports, and provider records may contain or retain | A local or exported copy remains separate from anything shared with support |
| [API Keys & Providers](providers-and-api-keys) | Tests each Brain, speech, Realtime, Tool Model, and Jarvis-Agent connection at its own boundary | A working category does not prove another category works |
| [CLI Reference](cli-reference) and [Control API Reference](control-api-reference) | Help advanced and headless users find supported discovery, status, and diagnostic operations | Prefer the app first; never expose the local API or put secrets in commands |
| **Feedback** | Opens the public support channel after you have a safe, reproducible report | Treat anything posted there as shared with other people |

The usual evidence path is: **visible symptom -> focused built-in check ->
feature record or status -> one safe recovery -> redacted support report if it
still fails**.

## Check That It Works

Verify only the path you repaired:

1. Return to the affected view and confirm that its error, warning, or offline
   state has cleared.
2. Run its smallest harmless check: reload one Doc, run one provider **Test**,
   use **Test wake word**, recheck one connection, or refresh one task.
3. Complete one end-to-end action with no external side effect. For chat, send
   `Reply with: recovery check complete.` For voice, ask one short question and
   confirm both the transcript and spoken reply.
4. If it was a voice problem, open **Transcription** and **Run Inspector** and
   confirm that the new finished session contains the expected turn and a
   usable outcome.
5. Stop after the focused check passes. Do not rotate unrelated credentials or
   change several working settings “just in case.”

## Next Steps

- Read [Install Personal Jarvis](install-personal-jarvis) when the app or its
  normal launcher never reaches a usable start.
- Use [Providers and API Keys](providers-and-api-keys) for connection, model,
  account, quota, and fallback problems.
- Follow [Audio Devices and Wake Word](audio-and-wake-word) for microphone,
  speaker, wake phrase, and local wake-engine checks.
- Read [Platform Support](platform-support) before diagnosing a desktop, audio,
  permission, or headless limitation as an app failure.
