---
title: "App Permissions"
slug: permissions
summary: "Grant only the microphone, screen, accessibility, and input permissions needed for voice, global shortcuts, and Computer Use."
section: "Privacy, safety, and support"
section_order: 6
order: 3
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [permissions, macos, privacy, computer-use, safety, approvals]
related: [first-run-setup, audio-and-wake-word, computer-use, privacy-and-local-data]
---

A permission answers one narrow question: may this app, connection, or action
cross a particular boundary? Personal Jarvis does not have one master switch
that grants everything.

The **App Permissions** controls currently manage five macOS privacy grants for
the installed desktop app. External account access and approval for a
particular tool call are separate decisions. Granting microphone access does
not connect an email account, and connecting an account does not authorize
every action Jarvis might take in it.

## Before You Start

- On macOS, start the installed **Personal Jarvis** app and keep it in the
  foreground while requesting access. Do not grant access to Terminal, Python,
  or a copied app bundle; macOS attaches a grant to the exact app identity.
- Decide which features you actually plan to use. Text chat does not need
  microphone or desktop-control access.
- Close or finish active Jarvis-Agent missions before a permission change that
  requires a restart. The permissions card refuses to restart while a mission
  is running.
- Enter service credentials only in their protected connection screens. App
  permission prompts never ask for an API key, token, password, or recovery
  code.

During first-run setup, macOS users see all five current grants. **Continue**
becomes available only when all five are ready. If you want to grant fewer,
choose **Continue with text only**, finish setup, and return to **Settings**
later for only the features you use.

## Grant and Review macOS Access

Open **Settings > Privacy permissions > macOS permissions**. Each row names the
access and the features that depend on it.

| Permission | What it lets Jarvis do | Needed for |
|---|---|---|
| **Microphone** | Capture local microphone audio | Wake word and local voice input |
| **Screen Recording** | Capture visible content across connected displays | Computer Use and screen-based vision |
| **Accessibility** | Read supported interface structure, focus windows, click, and type reliably | Computer Use, window control, and global shortcuts |
| **Input Monitoring** | Listen for configured system-wide keyboard shortcuts | Global push-to-talk and other global shortcuts |
| **Input Control** | Send mouse and keyboard events | Computer Use |

Grant one item at a time:

1. Select **Allow**. macOS may show its own prompt. Read the prompt, then allow
   or deny it there.
2. If the prompt cannot be shown, the item was denied before, or you want to
   review the setting directly, select **Open Settings** and change the switch
   for Personal Jarvis in macOS System Settings.
3. Return to Personal Jarvis. The list checks again automatically when the app
   becomes visible and while it is waiting for a change.
4. If **Restart now** appears, finish or stop active missions, then restart.
   Screen Recording, Accessibility, and Input Monitoring changes can require a
   fresh app process before their features become ready.
5. Test the feature itself. A green permission status proves that macOS allows
   the app; it does not prove that the microphone, model, connection, or target
   service is otherwise healthy.

### Read the Status Correctly

| Status | Meaning | Next action |
|---|---|---|
| **Allowed** | macOS reports the grant for the installed app | Test the feature; restart first if the card asks |
| **Not requested** | macOS has not recorded a microphone choice | Select **Allow** and answer the native prompt |
| **Not allowed** | The native check cannot confirm access | Use **Allow** when offered, or **Open Settings** |
| **Denied** | Access was explicitly denied | Change it in System Settings; the app cannot silently ask again |
| **Restricted** | A device policy or system rule prevents the grant | Ask the device administrator or review macOS policy |
| **Unavailable** | Jarvis could not use the native permission interface | Reopen the installed app and check the installation |
| **Not required** | The in-app macOS permission flow does not apply on this host | Check the host or browser separately if the feature still cannot access a device |

To revoke a macOS grant, turn off Personal Jarvis in the matching System
Settings privacy pane. The app has no **Revoke** or **Reset all permissions**
button. Return to the app after the change and restart when prompted. Revoking
access stops future use; it does not erase audio, screenshots, actions, or
provider records created earlier.

> [!warning]
> If the card says to launch the installed app, quit the current process and
> reopen Personal Jarvis from the installation created by the supported
> installer. Moving or copying the app can create a different macOS identity.
> Never solve this warning by granting broad access to an interpreter or
> terminal application.

## Know What This Screen Does Not Control

The word “permission” appears in several parts of the product, but those
decisions have different owners and lifetimes.

| Boundary | Where you decide | Scope and lifetime | How to remove or change it |
|---|---|---|---|
| **macOS app grant** | First-run setup, Settings, and macOS System Settings | One installed app identity; normally persists across launches | Change the switch in System Settings |
| **Browser site grant** | The browser's microphone, camera, download, or notification prompt | One site in one browser profile | Use that browser's site settings |
| **Service authorization** | [Plugin](plugins), [MCP](mcp-connections), or [CLI](cli-connections) sign-in and the service's consent page | The connected account and granted scopes until expiry or revocation | Disconnect in Jarvis and revoke it at the service when complete removal matters |
| **Jarvis safety decision** | [Safety policy](safety-and-approvals) and, when available, an approval prompt | One proposed tool call unless a narrow standing rule applies | Deny it, let it expire, or review the separate safety policy |
| **Scheduled-task grant** | The plugin scope selected when creating a task | Selected plugin during that task's runs | Delete the task and recreate it with narrower scopes |
| **Jarvis-Agent capability** | Capabilities selected for a [mission](jarvis-agents) | A restricted, short-lived mission grant | It ends when the mission scope closes or expires |
| **Skill instructions** | The [Skills](skills) view | Guidance for choosing and using tools; no access by itself | Disable or remove the skill |

### Notifications and Files

The current App Permissions list does **not** display, request, or verify a
Notification permission or a general Files permission. Manage notification
delivery in the operating system or browser that shows it.

File access is feature-specific. It can come from the operating-system account
running Jarvis, a folder you selected, a browser file picker, a connected
service, or a Jarvis-Agent's isolated mission workspace. There is no blanket
**Allow all files** switch in the current permissions card. If the operating
system blocks a folder, review that folder or app access in its own privacy
settings; an **Allowed** status on this page does not cover it.

On Windows and Linux, this panel currently reports that extra macOS grants are
not required. That is not a device-health test. Windows privacy controls,
Linux display-session restrictions, file ownership, and browser site settings
can still block a feature. For example, a Wayland desktop can prevent synthetic
input even though this page shows no missing macOS permission.

### Tool Approvals Are Single-Action Decisions

Jarvis evaluates a proposed tool call as **safe**, **monitor**, **ask**, or
**block**. Safe calls can run directly. Monitored calls are recorded and can
still be stopped by another safety check. Ask-level calls require a supported
confirmation path. Blocked calls do not run. A denial or unanswered approval
defaults to no action.

In a live voice session, a yes-or-no follow-up authorizes only the pending call
and is single-use. For a Jarvis-Agent mission, any visible approval is tied to
the exact paused tool call and expires; future calls are reviewed separately.
The worker receives neither a reusable approval nor the stored service
credential. Connected tools run through the supervisor's normal safety path.
Stored credentials have their own lifecycle; see [Credentials and
Secrets](credentials-and-secrets).

Standard text chat currently has no general foreground approval panel. An
ask-level text action can therefore time out and be denied even though the
connection and app permissions are ready. Retry only after choosing a path
that can obtain a decision, or perform the consequential action yourself.

> [!warning]
> The current main desktop navigation does not expose the detailed mission
> **Approvals** panel. A mission can report that approval is needed and then
> time out without a reachable review control. Keep unattended missions
> read-only, or perform the consequential external action yourself.

Scheduled tasks are a deliberate exception to per-call confirmation. A
**write** or **full** plugin scope chosen when the task is created can
pre-authorize matching ask-level plugin calls during that task's own run. A
**read** scope does not. Review unattended task scopes as standing authority,
not as a harmless label.

## How It Fits Together

A request succeeds only when every relevant boundary allows it:

1. **Local access:** the operating system lets the correct app identity use the
   microphone, screen, keyboard, pointer, or files needed for the feature.
2. **Connection access:** a plugin, MCP server, or CLI is connected and its
   account or local process has enough service permission.
3. **Capability selection:** Jarvis or a Skill chooses a tool that is actually
   available. A Skill explains a method; it cannot create access.
4. **Safety decision:** Jarvis evaluates the exact proposed action. An app or
   service grant never bypasses an ask or block decision.
5. **Execution scope:** a foreground conversation runs the action in that
   session, while a Jarvis-Agent receives only its short-lived mission tools.
6. **External enforcement:** the operating system or service can still refuse
   the operation. An approval means “try this call,” not “guarantee success.”

Computer Use demonstrates all six boundaries. On macOS it needs Screen
Recording, Accessibility, and Input Control; it also needs a working visual
model and the live desktop. Every proposed mouse or keyboard action still
passes safety. Prefer a Plugin, MCP connection, or CLI when a structured tool
can complete the work without exposing the screen.

## Check That It Works

Use the smallest feature that needs the permission you granted:

1. On macOS, open **Settings > Privacy permissions** and confirm that the
   intended row says **Allowed** and no restart message remains.
2. For Microphone, open the wake-word microphone check and speak a harmless
   phrase. Confirm that it reports a usable signal.
3. For Computer Use, confirm all three required rows are allowed, then run the
   harmless calculator check from [Computer Use](computer-use).
4. Revoke the tested grant in macOS System Settings only if you no longer want
   the feature. Confirm that the feature fails honestly rather than continuing
   with hidden access.

On Windows or Linux, test the feature directly and use that platform's privacy,
display, and file settings when access fails; the App Permissions list does not
validate them.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| No permission rows, only **Not required** | The host is not macOS, so this panel does not inspect platform or browser controls | Test the feature and review the host or browser settings that own the device |
| App identity warning and no buttons | Jarvis was started from an interpreter, terminal, copied bundle, or unexpected location | Quit it and reopen the installed Personal Jarvis app |
| **Allow** does not appear | The item is already granted, denied, restricted, unavailable, or the app is not eligible to prompt | Use **Open Settings** when shown; otherwise fix app identity or device policy |
| Status stays **Not allowed** after changing macOS settings | The app is still waiting for the native state or the change belongs to another app identity | Bring Personal Jarvis forward, wait briefly, confirm the correct app entry, and restart if asked |
| **Restart now** keeps refusing | One or more Jarvis-Agent missions are active | Finish or stop the missions, then restart; the permission card does not force-stop them |
| Every macOS row is allowed but the feature fails | A device, model, connection, browser grant, file boundary, or service authorization is still missing | Test the dependent feature and follow its own troubleshooting page |
| A tool or mission says approval is required | The action reached the safety gate, not the App Permissions gate | Review the exact action when a decision control is available; otherwise let it deny and perform the action yourself |

## Next Steps

- Use [First-Run Setup](first-run-setup) to understand the complete onboarding
  path and the text-only option.
- Read [Audio and Wake Word](audio-and-wake-word) to test microphone access,
  device selection, and wake readiness together.
- Read [Computer Use](computer-use) before granting screen and input control or
  allowing Jarvis to operate a live desktop.
- Review [Privacy and Local Data](privacy-and-local-data) to understand what a
  granted feature can store locally or send to a connected service.
