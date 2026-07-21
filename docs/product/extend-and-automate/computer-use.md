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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [computer-use, desktop, vision, permissions, safety, providers]
related: [permissions, safety-and-approvals, jarvis-agents, platform-support]
---

Computer Use lets Jarvis inspect and operate the graphical desktop on the
computer where Jarvis is running. It can open or switch apps, click controls,
type into fields, press keys, scroll, and drag when an app does not offer a
direct connection.

Jarvis works in a loop: it captures the target window or monitor, chooses an
action, performs it, and checks the visible result. This makes a run more
careful than a blind sequence of clicks, but it does not make the result
infallible. Watch the screen and start with a small, reversible task.

## Before You Start

- Use a real desktop session. Computer Use has Windows, macOS, and Linux X11
  backends, but availability still depends on the display, installed desktop
  components, and operating-system permissions. Wayland and servers without a
  display refuse screen control. Automated tests cover the platform seams, but
  they are not a dated live sign-off for every macOS or Linux desktop. Test one
  harmless action on the computer you plan to use, and read [Platform
  Support](platform-support) for the current verification record.
- Open **API Keys & Providers > Tool Model**. Connect a provider, choose a
  model that understands images, select **Set active**, and run the card's
  test. The Tool Model is separate from the model that writes chat replies.
- On macOS, open **Settings > Privacy permissions > macOS permissions** and
  grant **Screen Recording**, **Accessibility**, and **Input Control**. Restart
  the app when the permission view asks you to do so.
- On Linux X11, keep `xdotool` installed when you need to type accented text,
  emoji, or non-Latin characters. The standard installer tries to provide it;
  without it, the fallback input backend can drop non-ASCII characters.
- Check the provider's usage terms and billing. A multi-step desktop task can
  make several model calls with screenshots.

> [!note] Fresh installations enable Computer Use. An existing installation
> can retain an older disabled setting, and the desktop app still has no
> on/off control. If Jarvis says the feature is off, run `jarvis config set
> computer_use.enabled true`, restart Jarvis, and repeat a harmless check.

> [!warning] A captured window can contain private information. Computer Use
> stores captured frames in the local flight recorder and sends the working
> screenshot to a compatible Tool Model. If the selected provider is
> unavailable, a configured compatible fallback may receive it instead. Close
> unrelated private windows and never include a password, API key, token, or
> recovery code in the request.

## What Computer Use Can See and Do

| Part | Current behavior | Boundary |
|---|---|---|
| **View** | Captures the foreground target window by default and reads available accessibility labels; falls back to the selected monitor when it cannot isolate the window | Hidden windows and off-screen content are not visible until Jarvis brings them forward or scrolls |
| **Pointer** | Clicks, double-clicks, scrolls, and drags | A display or foreground-window change makes Jarvis discard stale coordinates and look again |
| **Keyboard** | Types text and sends keys or shortcuts to the focused control | Text can land in the wrong place if focus changes; Jarvis checks editable fields when the operating system exposes them |
| **Apps** | Opens an installed app or switches to an existing window | It cannot operate an app that is absent, blocked, or running with incompatible system privileges |
| **Verification** | Looks again after actions and stops after repeated misses, no progress, timeout, or the step limit | A visible change is evidence, not proof that an external transaction was correct |
| **Sensitive screens** | Stops when it detects a password field, two-factor prompt, or human-verification challenge | Complete that step yourself, then give Jarvis a new request |

For a read-only request, say what you want Jarvis to inspect and explicitly
say not to click or type. The loop can finish from visible evidence without
sending input, but the screenshot still goes to the Tool Model.

Visual tasks are slower than direct app launches and shortcuts. Computer Use
waits briefly for the interface to settle, sends a reduced-size view to the
Tool Model, and verifies the effect of each state-changing step. After a
verified missed click, it may inspect a close-up crop and retry once. This
close-up is internal; Computer Use does not enlarge or rearrange your windows
by default.

A browser is treated like any other desktop app. Computer Use reads its pixels
and available accessibility labels; it does not provide a separate headless
browser or Document Object Model automation mode. If you open Jarvis from
another device, Computer Use still controls the desktop on the Jarvis host,
not the device running the browser.

## Run a Bounded Desktop Task

1. **Prepare the target.** Close unrelated private windows and bring the app
   you want to use to the foreground. Keep login, payment, deletion, sending,
   and publishing steps for yourself.

2. **State one observable goal in chat or by voice.** Name the app, the action,
   and the result that should be visible. For example: "Open the system
   calculator, enter 25 times 4, and stop when 100 is visible." Do not include
   information that should not appear in a screenshot, transcript, or provider
   request.

3. **Wait for the acknowledgement.** Jarvis confirms that it will handle the
   task on screen, then continues in the background. Only one run controls the
   physical mouse and keyboard at a time. A second run waits for the desktop or
   reaches its own timeout; it does not race the active run.

4. **Watch the screen indicator.** On a supported desktop build, a pulsing gold
   border appears while Jarvis can send input, with an **Esc to cancel** hint
   when the global shortcut is available. The capture path tries to keep the
   border out of Computer Use's own screenshots. If the overlay component or
   shortcut backend is unavailable, the run can continue without the border or
   hint.

5. **Avoid changing the target.** Jarvis may open an app, capture a fresh view,
   perform one or a few related actions, and capture again. Avoid switching
   windows, moving the target between displays, or typing into the same app
   while a step is in progress. A changed foreground window or display layout
   makes Jarvis discard stale coordinates and capture again.

6. **Handle any human-only screen.** When Jarvis recognizes a password,
   two-factor, or human-verification prompt, it stops. Complete that step
   manually, make the intended app active again, and send a smaller follow-up
   request.

7. **Check the completion message and the screen.** Jarvis reports success
   only after a final visual check. Confirm the actual app state yourself,
   especially when the result affects another person, an account, or money.

### Start and Inspect a Run from the CLI

Use the command line when you need a mission identifier, status polling, or a
stop control outside the desktop app:

```bash
jarvis computer-use start "Open the calculator and show 25 times 4" --yes
jarvis computer-use list
jarvis computer-use show <mission-id>
jarvis computer-use cancel <mission-id> --yes
```

The start command prints a mission identifier. `list` includes active and
recent runs, and `show` reports the selected run's status and result. Use
`jarvis computer-use cancel-all --yes` only when you intend to stop every
queued or active Computer Use run.

### Understand Confirmations

Every desktop action passes through Jarvis's safety layer, where a blocked
policy stops it. The current Computer Use mouse and keyboard actions are
classified as **safe** or **monitor**. Because the default approval policy asks
only for **ask** actions, a normal chat or voice run does not show a
confirmation before each click or typed value. The CLI's `--yes` confirms that
you want to start desktop control; it is not a preview of every later step.

If the surrounding request reaches a separate **ask** action, Jarvis waits for
approval and does not run that action after a denial or approval timeout.
Jarvis cannot reliably infer the real-world consequence of every on-screen
button from its label. Treat Computer Use as supervised control, not as a safe
way to approve purchases, delete data, send messages, or publish content.

## Stop and Recover a Run

- Press **Esc** while the gold control indicator shows **Esc to cancel**. This
  cancels every active Computer Use run through the global shortcut. If no
  hint is shown, do not assume that Escape is registered.
- During a voice session, say **hang up** or select the visible close control
  on the Jarvis Bar. This cancels every queued or active Computer Use run
  without cancelling a separate Jarvis-Agent mission.
- From a terminal, run `jarvis computer-use list`, then cancel one mission by
  identifier or use `jarvis computer-use cancel-all --yes`. The desktop app
  does not currently have a dedicated Computer Use run list or cancel button.
- If none of these controls is available and input continues, exit Personal
  Jarvis. Reopen it only after checking what already changed.
- Moving the pointer into a screen corner is not a supported stop control in
  the current engine.
- Cancellation prevents later steps after the stop is observed; it cannot undo
  a click, keystroke, or submission that already finished. Inspect the app,
  undo the change manually when possible, then retry with a narrower goal.

## How It Fits Together

1. A chat or voice request, command-line start, scheduled action, workflow, or
   [Skill](skills) describes the goal. A simple app launch or shortcut can use
   a fast local action; a visual or ambiguous target goes to Computer Use.
2. Computer Use captures the target window, or the selected monitor when a
   window capture is unavailable, and asks the active, image-capable Tool Model
   for the next action. If that provider is unavailable, Jarvis can try another
   configured provider family that can see images. Learn how those choices are
   separated in [Providers and API Keys](providers-and-api-keys).
3. [App Permissions](permissions) decide whether Jarvis can capture the screen
   and send input. [Safety and Approvals](safety-and-approvals) evaluates each
   proposed action before the operating system receives it.
4. Jarvis performs the action, captures a fresh view, and verifies the visible
   effect. Desktop runs are serialized so two goals do not drive the same
   pointer at once. A recorded turn can show a **Computer-Use** badge and action
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
   the keyboard or pointer during the run. On a supported desktop build, the
   gold border is visible only while Jarvis can control input.
4. Confirm that Jarvis sends a completion message after the visible result.
   If you started by voice and session recording is active, open **Run
   Inspector** and look for the **Computer-Use** badge on that turn.

Computer Use is working when the requested visible state is correct and the
completion message arrives after that state appears, not before it.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Jarvis says Computer Use is not active, or reports Wayland or no display | An older installation kept the feature off, desktop components are missing, or the session cannot provide global capture and input | Enable it with `jarvis config set computer_use.enabled true`; otherwise use the full desktop install in a Windows, macOS, or Linux X11 session, then restart Jarvis and repeat the harmless check |
| Jarvis says no model can see the screen | The Tool Model is missing, text-only, out of credit, rate limited, or unavailable across all compatible fallbacks | Open **API Keys & Providers > Tool Model**, test a connected image-capable model, and activate a healthy option |
| Jarvis cannot see or control the screen on macOS | Screen Recording, Accessibility, or Input Control is missing, belongs to an older app identity, or needs a restart | Open **Settings > Privacy permissions > macOS permissions**, grant each listed item, use **Restart now** when shown, then try again |
| The pointer, drag, or typed text is wrong | Focus or display layout changed, an elevated Windows window blocked input, or Linux lacks `xdotool` for non-ASCII typing | Bring a normal non-administrator app window forward, keep the layout stable, install `xdotool` on X11 when needed, and retry one named control |
| Jarvis stops at a sensitive screen, reports no progress, or rejects completion | It needs a human-only login step, the interface changed, or the visible result could not be verified | Complete sensitive input yourself; otherwise check the screen and retry a shorter goal with one visible result |

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
