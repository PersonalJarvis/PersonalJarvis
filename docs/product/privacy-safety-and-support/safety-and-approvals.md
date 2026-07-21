---
title: "Safety and Approvals"
slug: safety-and-approvals
summary: "Learn why some actions run immediately, some require confirmation, and some are blocked before they can affect your computer or accounts."
section: "Privacy, safety, and support"
section_order: 6
order: 4
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [safety, approvals, risk-tiers, permissions, computer-use, automation]
related: [computer-use, tasks-and-reminders, skills, privacy-and-local-data]
---

Jarvis routes supported model-initiated tool calls through one safety gate
before they can act on your computer or a connected account. The gate may let
a call run, pause it for a clear yes or no, or refuse it. A tool path that
bypasses this gate is a product defect.

This is a gate around **Jarvis tool calls**. It is not a universal sandbox,
an undo system, or a replacement for operating-system permissions and service
access. A button you select directly in the app, a command you run yourself,
or a provider's own behavior can have a separate confirmation path.

## Understand the Four Decisions

Jarvis evaluates the proposed tool together with its arguments. Some tools can
assign a different level to a read action than to a write action.

| Decision | What happens | What to remember |
|---|---|---|
| **Safe** | The call runs without asking first; captured action records identify it as safe | Safe means low-friction, not private, reversible, or guaranteed to succeed |
| **Monitor** | The call runs without asking first; captured action records identify it for review | Monitor is an audit level, not a preview or pause |
| **Ask** | The exact call pauses until an approval path answers; no answer normally becomes a denial after about 60 seconds | Review the destination and arguments before approving |
| **Block** | The call is refused before the tool runs | Approval cannot override a block |

Jarvis checks the proposed tool and arguments in this order: deny rule, allow
rule, then the tool's own level. It then runs, pauses, or blocks the call.

A matching deny rule always wins. A matching allow rule is standing authority:
it changes the matching call to **safe**, even when the tool would normally
ask. It also skips the voice plausibility check. Keep allow patterns narrow;
a broad wildcard can silently remove confirmation from many different
arguments. The current app has no dedicated screen for reviewing these
advanced allow and deny rules.

For voice requests, Jarvis also checks whether the transcript and wake timing
look plausible. Uncertainty is logged for monitored calls. Ask-level calls
still require confirmation. This check does not turn a monitored action into
an approval prompt.

## Review an Approval

One approval authorizes one paused tool call with one trace identifier. It does
not authorize the rest of a mission, future calls to the same service, or a
rewritten set of arguments. Pending approvals are held in memory, so an app
shutdown or restart does not preserve authority to run them later.

Before you approve, check:

- **Action:** Is this the tool you expected Jarvis to use?
- **Target:** Is the account, person, file, application, or service correct?
- **Arguments:** Do the visible details match what you requested?
- **Effect:** Could this send, publish, delete, purchase, call, or expose data?

Approval previews mask common credential shapes and can be shortened. That
protects sensitive values, but it can also hide detail you would need for a
confident decision. Deny the call when the preview is incomplete or unclear.

### Voice and Realtime

For an ask-level conversational action, Jarvis asks a short yes-or-no question
instead of waiting silently for the desktop UI. A clear **yes** runs the
stored action once. A clear **no** drops it. In the normal voice flow, an
unrelated next request also abandons the action and repeated unclear replies
eventually cancel it. In Realtime, give a clear no or close the session when
you change your mind.

The normal voice flow has no visible countdown and its pending entry has no
separate wall-clock expiry. Answer promptly. If you return later or no longer
remember the exact action, say no and start again. Realtime also keeps a
pending action until you clearly answer or close the session; changing the
subject is not a reliable denial there.

### Jarvis-Agent Missions

A connected tool used by a Jarvis-Agent remains owned by the supervisor. The
worker receives a short-lived grant for selected tools, not the saved
credential or a reusable approval. Recursive mission tools, Skill execution,
secret and configuration tools, host shells, and live desktop controls are not
included in that grant. An ask-level call can pause with the tool name, worker,
risk level, reason, a redacted argument preview, and an expiry time. The
detailed approval panel requires a second click before **Approve and run**;
**Deny** is immediate.

In the app and spoken updates, the worker name follows your configured wake
word, such as **Nova-Agent**. With no configured name, the fallback is
**Assistant-Agent**. This page uses **Jarvis-Agent** for the internal system.

> [!warning]
> The detailed Missions view and its **Approvals** tab exist in the current
> frontend, but the main desktop navigation does not expose that view. A global
> toast can report a pending mission call, but the toast has no decision button.
> Use the supported CLI path below or let the request expire. Do not broaden an
> allow rule just to make the mission continue.

Replace the uppercase placeholders with the identifiers shown by the toast or
the first command:

```bash
jarvis missions tool-approvals MISSION_ID
jarvis missions deny-tool MISSION_ID TRACE_ID --reason user_denied
jarvis missions approve-tool MISSION_ID TRACE_ID --yes
```

The list command shows only live requests. A decision removes that request; a
wrong mission, unknown trace, or expired request is rejected as not found. The
approve command requires `--yes` because the CLI marks it as dangerous. These
commands use the same mission approval API as the hidden panel and resume only
the matching call. A direct API client does not add the CLI's `--yes` gate, so
it must apply its own deliberate confirmation before calling the approve route.

Standard text chat also has no general foreground approval panel today. An
ask-level action started there can wait and then be denied. Use voice for a
supported yes-or-no flow, or take the action yourself after reviewing it.

## Know What Approval Does Not Cover

Approving means **try this exact call now**. It does not:

- grant microphone, screen, keyboard, file, or accessibility access;
- connect an account or create a missing credential;
- expand the scopes allowed by Google, GitHub, an MCP server, or another
  service;
- verify that a model interpreted your intent correctly beyond the displayed
  call;
- guarantee success, prevent a provider charge, or undo a completed effect;
- approve a later step in a workflow, Skill, task, or mission;
- make information sent to a remote service local or private.

Operating-system prompts, browser consent, account scopes, in-app destructive
buttons, and the CLI's required `--yes` gate are separate controls. Passing one
does not silently pass the others.

## Standing Authority and Automation

Automation changes when you can be present to answer.

### Tasks

When you create an agent task, a plugin grant set to **Write** or **Full** is
pre-authorization for that plugin during the task's own run. Matching ask-level
calls can be approved automatically without waking you. A **Read** grant does
not provide that standing approval. Treat Write and Full as durable authority
and review the prompt, schedule, and grants together before creating the task.
The current task view cannot edit an existing grant; cancel and recreate the
task when you need narrower access.

Cancelling a task prevents later scheduled work, but it is a soft cancel. It
does not reliably interrupt a Computer Use loop that is already controlling
the desktop.

### Skills and Workflows

A Skill is guidance, not a permission. AI-generated Skills remain drafts until
you review and promote them. In conversational use, the tools a Skill asks
Jarvis to call keep their normal safety decisions. Scheduled Skill steps use
the Skill's declared policy and refuse ask-level work when no approver is
present.

Workflows and app commands can also include their own deliberate confirmation
or hold-to-stop control. When a sequence makes separate tool calls, one
approval is not meant to cover the rest. These controls complement the tool
gate; they are not evidence that every later service call will ask again.

### Connections, Plugins, and MCP

Connecting a service gives Jarvis a route to that service. It does not prove
that every method is read-only. Current MCP and marketplace-plugin tools
normally inherit one shared **monitor** default instead of classifying every
server method as read versus write. A write-capable method can therefore run
without a prompt in the default setup.

Connect only servers you trust, grant the narrowest account scopes available,
and test on non-critical data first. A plugin's own service permission is an
important backstop when its Jarvis tier is not specific enough.

### Computer Use

Computer Use is a monitored live-desktop tool, and its mouse and keyboard
steps are mostly safe or monitored. It does **not** ask before every click or
keystroke. Screen, accessibility, and input permissions plus foreground-window
checks provide additional boundaries. Login, password, two-factor, CAPTCHA,
and operating-system elevation screens require human handoff rather than secret
entry by Jarvis. You should still watch the run and avoid sensitive screens.

To stop a running mission from **Outputs**, hold the stop control until its
ring completes. For a live voice or Computer Use turn, use the app's hang-up
control. If desktop control continues or the UI is unresponsive, close Jarvis.
Stopping prevents later steps; it cannot reverse a click, message, command, or
upload that already completed.

## How It Fits Together

A successful action normally passes several independent boundaries:

1. **Capability:** a Skill, workflow, Brain, or Jarvis-Agent chooses an
   available tool.
2. **Permission:** the operating system allows the app to reach the required
   microphone, screen, input, or file boundary. See [App
   Permissions](permissions).
3. **Connection:** Jarvis can retrieve the required credential and the service
   account has enough scope. See [Credentials and
   Secrets](credentials-and-secrets).
4. **Safety:** deny and allow rules plus the tool's risk level decide whether
   the exact call runs, waits, or stops.
5. **Execution:** the computer or remote service accepts the request and may
   apply its own prompts, limits, charges, and policies.
6. **Review:** captured action events and results can appear in Sessions, the
   Run Inspector, a task timeline, or mission history. These views show what
   their active recorder received; they are not a complete, immutable audit log
   and they cannot undo an action.

This is why a fully connected email account can still produce a denied send,
and why an approved desktop action can still fail when the operating system
blocks input.

## Check That It Works

Use harmless checks rather than creating a real side effect:

1. Start a normal voice session and ask Jarvis to perform a simple web search.
   Open **Run Inspector** and check that the captured tool call has a low risk
   level and a result.
2. Ask Computer Use to open a calculator. Expect it to run as monitored work,
   not to request approval for every step. Watch the desktop and use hang-up or
   the stop control if it leaves the requested task.
3. During a normal voice request that naturally needs approval, listen to the
   question and answer **no**. Confirm that no message, call, or other external
   effect occurred. Do not create a real send or purchase merely to test this
   page.
4. If you maintain a scheduled task, expand its card and inspect the saved
   **Spec**. Confirm that every Write or Full grant is intentional and that
   read-only tasks use Read. Cancel and recreate the task if a grant is too
   broad.

The Run Inspector may show a risk level, who approved a call, its duration,
and its outcome when those events were captured. Some automatic and spoken
decisions do not currently produce a separate approval row, so a blank
approval field is not proof that no standing rule applied.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| An action ran without asking | It was safe or monitor, a standing allow rule matched, or the connection inherited the monitor default | Review the Run Inspector, external account scopes, and any advanced allow rules |
| Text chat waits and then reports a denial | The call was ask-level, but text chat has no general approval control | Retry through voice when appropriate, or perform the action yourself |
| A mission toast says approval is needed, but no Approvals screen is visible | The detailed Missions view is not reachable from the current main navigation | Use `jarvis missions tool-approvals MISSION_ID`, then deny it or approve the exact trace with `--yes`; otherwise let it expire |
| A task sent or posted without asking | Its Write or Full plugin grant pre-authorized matching work | Cancel the task, then recreate it with Read or no plugin grant |
| Computer Use starts immediately | Its outer tool and most desktop steps are monitored or safe | Watch the run; use hang-up or stop when it leaves the requested scope |
| Approval expired before you decided | The paused call reached its decision timeout | Recheck the target and start a fresh request; never approve a stale description from memory |
| Approval succeeded, but the action failed | A permission, credential, account scope, network, service limit, or target state blocked execution | Fix the failing boundary; approval is not a success guarantee |
| Stop was accepted, but an effect remains | The effect completed before cancellation reached the next checkpoint | Repair or reverse it at the destination when possible, then review the run history |

## Next Steps

- Read [Computer Use](computer-use) before allowing live mouse and keyboard
  control.
- Review [Tasks and Reminders](tasks-and-reminders) before granting unattended
  Write or Full access.
- Use [Skills](skills) to understand drafts, activation, and how instructions
  call normal tools.
- Read [Privacy and Local Data](privacy-and-local-data) to understand what an
  approved local or remote action may store or send.
