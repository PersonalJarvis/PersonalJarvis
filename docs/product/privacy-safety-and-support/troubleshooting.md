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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [troubleshooting, diagnostics, recovery, startup, providers, voice, connections, support]
related: [install-personal-jarvis, providers-and-api-keys, audio-and-wake-word, platform-support]
---

Personal Jarvis combines a local app, optional online providers, operating
system permissions, connected tools, and background work. One part can fail
while the rest of the app remains healthy. Find the affected part before you
change any settings.

The checks below use visible app controls. Routine troubleshooting should not
require deleting data, editing configuration files, exposing the local API, or
reinstalling the app.

## Start With a Focused Check

1. **Find the scope.** Open one unrelated view. If Docs fails, try Chats or
   Settings. If every view shows **Offline**, treat it as an app connection
   problem instead of a Docs problem.
2. **Record the exact status.** **Starting…**, **Voice starting…**, **Offline**,
   **Key invalid**, **Reconnect needed**, and **Not allowed** point to different
   causes.
3. **Use one matching control.** Select the relevant **Try again**, **Test**,
   **Refresh**, **Reconnect**, or **Recheck status** action once.
4. **Check the same layer again.** A passing Brain test does not prove that
   voice, Realtime, a tool connection, or an agent worker is ready.
5. **Restart only when needed.** Finish active work first. The app asks for a
   second confirmation if running agent missions would be stopped.
6. **Keep a small record.** Note the view, exact status, approximate time, and
   shortest steps that reproduce the problem.

> [!warning] Never share an API key, token, password, recovery code, private
> configuration file, or unreviewed log. Screenshots, transcripts, run details,
> and logs can contain conversation text, file names, account details, and
> personal paths. Share only a narrow, reviewed, redacted excerpt.

| What is affected? | First place to look | First check |
|---|---|---|
| The whole app | Top status and startup screen | Let the current start finish once, then restart or quit and reopen |
| Docs only | **Docs** loading or error panel | Select **Try again** |
| Chat replies | **API Keys & Providers > Brain** | Run **Test** on the selected provider |
| Wake, listening, or speech | **Settings > Audio devices** and **Wake Word** | Rescan devices, then run **Test wake word** |
| An action is denied | The permission card or approval message | Grant or approve only the named access |
| A plugin, MCP server, or CLI | **Skills, Plugins & MCPs** or **CLIs** | Check that connection's status and one read-only action |
| A task, agent mission, or file | **Tasks**, the Agents view, or **Outputs** | Read its status or timeline before retrying |

## Troubleshooting

### The App Does Not Open, Stays Blank, or Shows Offline

- **Starting…** means the live local connection is still warming up.
  **Voice starting…** refers only to voice readiness, so text chat may already
  work.
- If **Offline** appears across several views and does not clear, select the
  top-bar **Restart** control. The first click changes it to **Confirm
  restart?**. If missions are active, the app asks **Restart anyway?** before
  it stops them.
- If one view shows a crash panel, select **Back to Chats**, then reopen that
  view. A single view crash does not require an immediate app restart.
- If the whole window remains blank or unresponsive, quit the application
  normally and reopen it once. Repeated restart attempts can hide the original
  symptom.
- A headless installation has no native desktop window, local microphone,
  overlay, or desktop Computer Use. Open its browser interface and see
  [Platform Support](platform-support) before treating those missing desktop
  surfaces as a crash.
- If the normal launcher never appears after installation, follow
  [Install Personal Jarvis](install-personal-jarvis). Do not delete the data
  directory as a first repair step.

### An Update or Restart Does Not Finish

Managed official installs show **Update available** only when a newer
published release is available. Selecting it stages that release and restarts
the app. If staging finishes but the restart fails, the control changes to
**Finish update** so you can try the restart again.

- Finish or stop active agent missions before updating. Continuing through
  **Restart anyway?** can stop them.
- If the app reports that it rolled back, keep using the restored version and
  report the exact update message.
- Manual source checkouts, development installs, and headless processes do not
  use the desktop managed-update control. Update them through the method used
  to install them.
- When one device works and another does not, compare their installed versions
  first. Then compare provider credentials, selected devices, and settings.
  Only investigate an operating system gap after the version and setup match.

To print the installed command-line version, run:

```bash
jarvis version
```

### Docs Keep Loading or Show an Error

The desktop Docs library is local. On its first open after an install or
update, it may show **Building local index…** or **Preparing your
documentation** while it builds the search index.

- If **Documentation could not be loaded** appears, select **Try again**. The
  app stops waiting after a bounded request timeout instead of keeping an
  endless loading state.
- If only Docs fails, do not replace a provider key. Online providers do not
  build the local documentation index.
- Select **Open redesigned online docs** to use the hosted copy at
  [personaljarvis.ai/docs](https://personaljarvis.ai/docs/) while the local
  index recovers.
- If the page list is visible but a title is missing, clear the sidebar filter.
  Use the magnifying-glass search, or `Ctrl+K` on Windows and Linux or
  `Command+K` on macOS, to search page content.
- If both copies fail, test them separately. Local Docs depends on the running
  Jarvis app. Hosted Docs depends on the browser and internet connection.

### Chat Does Not Answer or Uses the Wrong Provider

Open **API Keys & Providers > Brain** and test the provider selected for chat.
A saved credential, an active selection, and a successful request are separate
facts.

- Run **Test**. The current results are **Works**, **No key set**, **Key
  invalid**, **Out of credits**, **Rate limited**, **Model unavailable**,
  **Unreachable**, and **Integration error**. Repair that account or select a
  ready provider that supports the required capability.
- Jarvis can skip an unavailable provider and cross to another compatible
  provider family when one is configured. Fallback cannot help if every
  compatible provider is missing, unavailable, or out of credit.
- If chat works but a tool action fails, check the **Tool Model** and the tool
  connection. Their readiness is independent from the Brain card.
- Agent missions have their own worker connections. Scheduled agent tasks use
  the worker chosen when the task was created and do not silently move to a
  different worker later.
- If the message field shows **Starting…** or **Offline**, fix the local app
  connection before replacing a provider credential.
- Enter credentials only in the protected provider field. Never paste them
  into chat, voice, tasks, Docs, or a support report.

See [Providers and API Keys](providers-and-api-keys) for category-specific
tests and fallback behavior.

### Voice Does Not Hear, Understand, Wake, or Speak

Voice is a chain. Check the first step that fails.

| Symptom | Check |
|---|---|
| **Voice starting…** remains visible | Wait for the current warm-up once. If it never clears, check microphone access, the selected input, and the voice provider cards. |
| The wake phrase does nothing | Open **Settings > Wake Word**, keep **Auto (recommended)** unless you have a reason to choose another engine, and run **Test wake word**. |
| Listening starts but the transcript is wrong or empty | Select the intended microphone under **Settings > Audio devices**, then use **Rescan devices** if it is missing. |
| The transcript is correct but no reply is heard | Check the selected output device and the active **Voice Output** provider. |
| Realtime cannot start or ends early | End the call, test the Realtime provider card, and start a new session. Long gaps can still occur on some Realtime connections. |
| Normal conversation triggers the wake phrase | Choose a distinct two-word or three-word phrase with a prefix. There is no sensitivity slider. |

**Test wake word** checks the configured engine, model or vocabulary when
applicable, and microphone level. It does not prove that the engine will
recognize your spoken phrase. After it passes, say the phrase once for an
end-to-end check.

On macOS, use **Settings > Privacy permissions** for microphone access and
restart only if the card shows **Restart now**. A Realtime session opened in a
browser uses that browser's microphone permission and device, not the native
desktop audio selection.

For a recorded voice turn, **Transcription** and **Run Inspector** can show
whether speech recognition, a provider, a tool, or spoken output was the last
recorded stage. They do not diagnose a turn that was never recorded.

### A Permission or Approval Blocks an Action

An operating system permission and a Jarvis safety approval answer different
questions. The operating system controls microphone, screen, accessibility,
and input access. Jarvis then decides whether a particular action is safe,
needs confirmation, or must be blocked.

- On macOS, open **Settings > Privacy permissions** and act on the named row.
  Current controls include **Allow**, **Open Settings**, **Check again**, and
  **Restart now**. Current states include **Allowed**, **Not requested**,
  **Denied**, **Restricted**, **Not allowed**, **Unavailable**, **Not
  required**, and **Restart pending**.
- Windows and Linux do not show the macOS permission cards. Use the operating
  system's own microphone, display, and accessibility controls when the
  affected feature requires them.
- Review the exact target and effect before approving an action. Do not weaken
  a safety rule merely to make an unclear request proceed.
- **Allowed** proves the operating system grant. It does not prove that the
  action is supported or that Jarvis will approve every later request.

### A Plugin, MCP Server, or CLI Is Disconnected

- For a packaged plugin, select **Refresh catalog** to reread catalog state.
  It does not repair or update the plugin. Use **Reconnect** when the card
  shows **Reconnect needed**, complete sign-in with the intended account, and
  return to the app.
- For an MCP server, check whether it is enabled and read its connection error.
  Then try one harmless read-only tool. A server can be running while a tool
  still lacks permission. Prefer the packaged plugin when one exists, and
  never place a raw secret in `mcp.json`.
- For a CLI connection, select **Recheck status** after installation or login.
  Its states include **connected**, **disconnected**, **not installed**, and
  **error**. **connected** confirms discovery and account readiness, not every
  command's authorization, so try one supported read-only action.
- If every connection says the backend is unreachable, return to the app-wide
  startup check. If one connection fails, repair that service without changing
  unrelated providers.

See [Plugins](plugins), [MCP Connections](mcp-connections), and
[CLI Connections](cli-connections) for their individual setup steps.

### A Task, Agent Mission, or File Appears Stuck or Missing

- In **Tasks**, select **Refresh** and expand the card's Timeline. A scheduled
  task can run one missed occurrence when the scheduler returns, so Jarvis does
  not have to be running at the exact due time. Work interrupted during
  shutdown is not automatically repeated.
- In the Agents view, open the mission row and read its status and detail before
  restarting. If no worker is ready, check the **Agents** category under
  **API Keys & Providers**. Subscription CLI workers have a built-in test;
  API-backed workers are verified with a short mission.
- The live mission board is not permanent history. **Outputs** keeps a recent
  set of archived deliverables, not every past mission and not a backup of
  files delivered elsewhere.
- **This session has no saved files** means that attempt produced no archived
  deliverable. Check its mission result before starting a new attempt.
- **Restart** on an Outputs card creates a new linked mission attempt. It is
  different from the top-bar **Restart** control. **Continue** does the same
  for a cancelled attempt.
- **Open** and **Reveal in folder** are available only when the desktop app can
  reach the local file. Remote and headless views do not offer a file download.
  A shortened preview or binary notice is not evidence that the file is
  damaged.

### The Built-In Checks Are Not Enough

Advanced users can run the read-only command-line doctor:

```bash
jarvis --doctor
```

It checks command registration, Brain selection, external agent worker CLIs,
and Linux Computer Use prerequisites. It is not a complete app, network,
provider-account, audio, or wake-word test. Use the feature-specific checks
above for those areas.

## When to Seek Help

Ask for help when a focused test still fails after one retry, the problem
returns after one normal restart, the app repeatedly crashes, or a record
appears lost. Open **Feedback**, then choose **Open #report-a-bug**. If needed,
select **Join Discord first**. Posts in that Discord channel are public. Use
the project's GitHub Issues page if Discord is unavailable.

Include only what another person needs to reproduce the problem:

- Personal Jarvis version, operating system, and whether you use the desktop
  app, a browser, or a headless host;
- the affected view and exact visible status or error;
- the shortest numbered steps, expected result, and actual result;
- the approximate time and whether one feature or the whole app is affected;
  and
- one redacted screenshot or small reviewed excerpt when it adds evidence.

Do not attach a complete log, raw configuration, `.env` file, credential-store
export, full transcript, or raw Run Inspector data. If a credential was ever
exposed, revoke or rotate it with the provider. Redacting a later message does
not invalidate the exposed value.

## How It Fits Together

| Area | What it can tell you | What it cannot prove |
|---|---|---|
| [Find Help in the App](find-help-in-the-app) | Which procedure matches the visible symptom | That the affected feature is working |
| [Sessions and Run Inspector](sessions-and-run-inspector) | What happened during a recorded voice turn | What happened before a turn was recorded or after a separate task started |
| [Settings](settings-and-appearance) | Selected audio devices, wake setup, and macOS permission state | Provider-account health or approval for a later action |
| [Privacy and Local Data](privacy-and-local-data) | What local and exported records can contain | That a record is safe to share without review |
| [API Keys & Providers](providers-and-api-keys) | Whether one provider can complete its own test | That another category or provider can work |
| **Feedback** | A public place to submit a reproducible report | Private handling of anything you post |

## Check That It Works

Verify only the path you repaired:

1. Return to the affected view and confirm that its error, warning, or
   **Offline** state has cleared.
2. Run its smallest harmless check. Reload one Doc, run one provider **Test**,
   use **Test wake word**, recheck one connection, or refresh one task.
3. Complete one end-to-end action with no external side effect. For chat, ask
   for a short reply. For voice, ask one short question and confirm the
   transcript and spoken reply.
4. If it was a recorded voice problem, open **Transcription** and **Run
   Inspector** and confirm that the new session contains the expected turn.
5. Stop when the focused check passes. Do not rotate unrelated credentials or
   change several working settings as a precaution.

## Next Steps

- Read [Install Personal Jarvis](install-personal-jarvis) if the app or its
  normal launcher never reaches a usable start.
- Use [Providers and API Keys](providers-and-api-keys) for account, model,
  quota, category, and fallback problems.
- Follow [Audio Devices and Wake Word](audio-and-wake-word) for microphone,
  speaker, wake phrase, and local wake-engine checks.
- Read [Platform Support](platform-support) before diagnosing a desktop,
  audio, permission, or headless limitation as an app failure.
