---
title: "Computer Use"
slug: computer-use
summary: "Understand how Jarvis sees and operates desktop apps, when it asks for confirmation, and how to stop or recover a run."
section: "Extend and automate"
section_order: 5
order: 6
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [computer-use, desktop, vision, permissions, safety, providers]
related: [permissions, safety-and-approvals, jarvis-agents, platform-support]
---

Computer Use lets Jarvis operate the desktop interface you can see. It can
open or switch apps, click controls, type into fields, press keys, scroll, and
drag when an app does not offer a direct connection.

Jarvis works in a loop: it captures the target window, chooses an action,
performs it, and checks the visible result. This makes a run more careful than
a blind sequence of clicks, but it does not make the result infallible. Watch
the screen and start with a small, reversible task.

## Before You Start

- Use a real desktop session. Computer Use is implemented for Windows, macOS,
  and Linux desktops using X11, but its availability depends on the permissions
  and controls exposed by that computer. The project does not yet have a dated
  live macOS or Linux GUI sign-off, so test one harmless action on the actual
  desktop before relying on it. Wayland and servers without a display refuse
  mouse and keyboard input. Read [Platform Support](platform-support) for the
  current verification record.
- Open **API Keys & Providers > Tool Model**. Connect a provider, choose a
  model that understands images, select **Set active**, and run the card's
  test. The Tool Model is separate from the model that writes chat replies.
- On macOS, open **Settings > Privacy permissions > macOS permissions** and
  grant **Screen Recording**, **Accessibility**, and **Input Control**. Restart
  the app when the permission view asks you to do so.
- Check the provider's usage terms and billing. A multi-step desktop task can
  make several model calls with screenshots.

> [!note] Computer Use is off in a fresh installation, and the current desktop
> app does not provide an on/off control. If Jarvis reports that desktop
> control is off, the person managing the installation must enable it and
> restart Jarvis. The [Configuration Reference](configuration-reference)
> identifies the setting; credentials still belong only in protected fields in
> the app.

> [!warning] A captured window can contain private information. Computer Use
> stores captured frames in the local flight recorder and sends the working
> screenshot to a compatible Tool Model. If the selected provider is
> unavailable, a configured compatible fallback may receive it instead. Close
> unrelated private windows and never include a password, API key, token, or
> recovery code in the request.

## What Computer Use Can See and Do

| Part | Current behavior | Boundary |
|---|---|---|
| **View** | Captures the current target window and reads available accessibility labels | Hidden windows and off-screen content are not visible until Jarvis brings them forward or scrolls |
| **Pointer** | Clicks, double-clicks, scrolls, and drags | A display or foreground-window change makes Jarvis discard stale coordinates and look again |
| **Keyboard** | Types text and sends keys or shortcuts to the focused control | Text can land in the wrong place if focus changes; Jarvis checks editable fields when the operating system exposes them |
| **Apps** | Opens an installed app or switches to an existing window | It cannot operate an app that is absent, blocked, or running with incompatible system privileges |
| **Verification** | Looks again after actions and stops after repeated misses, no progress, timeout, or the step limit | A visible change is evidence, not proof that an external transaction was correct |
| **Sensitive screens** | Stops when it detects a password field, two-factor prompt, or human-verification challenge | Complete that step yourself, then give Jarvis a new request |

## Run a Bounded Desktop Task

1. **Prepare the target.** Close unrelated private windows and bring the app
   you want to use to the foreground. Keep login, payment, deletion, sending,
   and publishing steps for yourself.

2. **State one observable goal.** Name the app, the action, and the result that
   should be visible. For example: “Open the system calculator, enter 25 times
   4, and stop when 100 is visible.” Do not include information that should not
   appear in a screenshot, transcript, or provider request.

3. **Wait for the acknowledgement.** Jarvis confirms that it will handle the
   task on screen, then continues in the background. Do not start a second
   desktop goal until the first one finishes; different goals can currently
   overlap.

4. **Watch without changing the target.** Jarvis may open an app, capture a
   fresh view, perform one or a few related actions, and capture again. Avoid
   switching windows, moving the target between displays, or typing into the
   same app while a step is in progress.

5. **Handle any human-only screen.** When Jarvis recognizes a password,
   two-factor, or human-verification prompt, it stops. Complete that step
   manually, make the intended app active again, and send a smaller follow-up
   request.

6. **Check the completion message and the screen.** Jarvis reports success
   only after a final visual check. Confirm the actual app state yourself,
   especially when the result affects another person, an account, or money.

### Understand Confirmations

Every desktop action passes through Jarvis's safety layer, where a blocked
policy stops it. The current Computer Use mouse and keyboard actions are
classified as **safe** or **monitor**. Because the default approval policy asks
only for **ask** actions, a normal Computer Use run does not show a confirmation
before each click or typed value.

If the surrounding request reaches a separate **ask** action, Jarvis waits for
approval and does not run that action after a denial or approval timeout.
Jarvis cannot reliably infer the real-world consequence of every on-screen
button from its label. Treat Computer Use as supervised control, not as a safe
way to approve purchases, delete data, send messages, or publish content.

## Stop and Recover a Run

- During a voice-started run, say **hang up** or use the close control on the
  Jarvis Bar. This cancels every active Computer Use run without cancelling a
  separate Jarvis-Agent mission.
- A text-started run currently has no dedicated **Cancel Computer Use** button.
  If the Jarvis Bar cannot end it and you need an immediate stop, exit Personal
  Jarvis. Reopen it only after checking what already changed.
- Moving the pointer into a screen corner is not a supported stop control in
  the current engine.
- Cancellation prevents later steps after the stop is observed; it cannot undo
  a click, keystroke, or submission that already finished. Inspect the app,
  undo the change manually when possible, then retry with a narrower goal.

## How It Fits Together

1. A chat, voice request, workflow, or [Skill](skills) describes the goal. A
   simple app launch or shortcut can use a fast local action; a visual or
   ambiguous target goes to Computer Use.
2. Computer Use captures the target window and asks the active, image-capable
   Tool Model for the next action. If that provider is unavailable, Jarvis can
   try another configured provider family that can see images. Learn how those
   choices are separated in [Providers and API Keys](providers-and-api-keys).
3. [App Permissions](permissions) decide whether Jarvis can capture the screen
   and send input. [Safety and Approvals](safety-and-approvals) evaluates each
   proposed action before the operating system receives it.
4. Jarvis performs the action, captures a fresh view, and verifies the visible
   effect. A recorded voice turn can show a **Computer-Use** badge and action
   evidence in [Sessions and Run Inspector](sessions-and-run-inspector).
5. A [Jarvis-Agent](jarvis-agents) handles longer work in an isolated project
   workspace; it does not replace Computer Use for the live desktop. Prefer an
   [MCP connection](mcp-connections) or [CLI connection](cli-connections) when
   a service offers a structured action that avoids visual clicking.
6. A file saved through a desktop app remains an ordinary file in the place
   that app chose. It does not become an [Outputs](outputs-and-files) card
   unless a Jarvis-Agent mission created and archived it.

## Check That It Works

1. Use a non-private desktop with no login prompts open.
2. Ask Jarvis to open the system calculator, enter 25 times 4, and stop when
   100 is visible.
3. Watch for the calculator to open and show **100** without taking control of
   the keyboard or pointer during the run.
4. Confirm that Jarvis sends a completion message after the visible result.
   If you started by voice and session recording is active, open **Run
   Inspector** and look for the **Computer-Use** badge on that turn.

Computer Use is working when the requested visible state is correct and the
completion message arrives after that state appears, not before it.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Jarvis says Computer Use is not active | The feature is still off, or its screen engine could not start | Have the installation manager enable Computer Use, restart Jarvis, and repeat the harmless calculator check |
| Jarvis says no model can see the screen | The Tool Model is missing, text-only, out of credit, rate limited, or unavailable across all compatible fallbacks | Open **API Keys & Providers > Tool Model**, test a connected image-capable model, and activate a healthy option |
| Jarvis cannot see or control the screen on macOS | Screen Recording, Accessibility, or Input Control is missing, belongs to an older app identity, or needs a restart | Open **Settings > Privacy permissions > macOS permissions**, grant each listed item, use **Restart now** when shown, then try again |
| Linux reports Wayland or no display | The session blocks synthetic input, or Jarvis is running without a desktop | Sign in to an X11 desktop session; use structured connections instead on a headless server |
| The pointer moves but the app does not react | Focus changed, the display layout changed, the target is an elevated Windows window, or the control did not expose a usable effect | Bring a normal non-administrator instance of the app forward, keep the display layout stable, and retry one clearly named control |
| Jarvis stops at a login or verification page | It detected a password, two-factor, or human-verification step | Complete the sensitive step yourself, return to the intended app, and send a new request without the credential |
| Jarvis reports no progress, too many steps, or an action failure | The goal is too broad, the interface changed, or repeated actions did not produce a verifiable result | Check the screen manually and retry one shorter goal that names the visible control and expected result |

For a failure that affects other app areas as well, use the main
[Troubleshooting](troubleshooting) guide instead of repeatedly retrying the
same desktop action.

## Next Steps

- Review [App Permissions](permissions) to grant only the screen and input
  access required on your platform.
- Check [Platform Support](platform-support) for the verified operating-system
  paths and the desktop checks that still depend on the current host.
- Read [Safety and Approvals](safety-and-approvals) to understand monitored,
  confirmed, and blocked actions before relying on automation.
- Learn when to use [Jarvis-Agents](jarvis-agents) for longer work that should
  run outside the live desktop.
- Use [Troubleshooting](troubleshooting) when provider health, app startup, or
  several features fail together.
