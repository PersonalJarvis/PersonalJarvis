# Computer-Use And Local Desktop Routing

Status: 2026-05-14. ADR: [0008](adr/0008-computer-use-harness-in-process.md).

This page documents the current routing model for desktop actions. The main
goal is to keep simple local actions deterministic and fast, while preserving
POAV Computer-Use and OpenClaw for the work that actually needs them.

## Routing Model

Jarvis now has four distinct routes for commands that may touch the desktop.

| Route | Use for | Must not do |
| --- | --- | --- |
| Direct Fast Path | One local action such as open an app, type into the active window, send a hotkey, move/click explicit coordinates | Call a provider, collect vision, infer visual targets |
| Scripted Fast Path | Known deterministic multi-step local workflows such as opening several terminals or starting OpenClaw in a terminal | Invent coordinates, ask a provider to plan, run shell commands for vague UI goals |
| POAV Computer-Use | Visual or ambiguous UI navigation such as "click Send", "write this into the ChatGPT input", or "find the settings button" | Handle heavy coding/research delegation or long-running autonomous work |
| OpenClaw | Heavy code, repo, research, worker, or long-running delegated tasks | Execute simple desktop controls that the local fast path can do directly |

The fast paths run before normal provider routing. If the local action gate
matches, BrainManager executes hidden local tools through the existing
ToolExecutor instead of exposing those tools to the router model.

## Direct Fast Path

Direct Fast Path is for atomic commands with a clear target:

- `Mach Spotify auf`
- `Oeffne Windows Terminal`
- `Starte Notepad`
- Type text into the already active window
- Send a hotkey to the already active window

Latency target:

- It should not call a Brain provider.
- It should not collect screenshots or UIA vision.
- It should usually finish dispatch in under one second; app startup itself can
  take longer and is outside the dispatch budget.

Operational boundary:

- The command must already name the app or active-window operation.
- The route may use `open_app`, `type_text`, `hotkey`, `click`,
  `move_mouse`, `switch_window`, or `dispatch_to_harness` only when wired as
  hidden local-action tools.
- Visual target phrases such as "click the Send button" are not direct fast
  path commands because the system must observe the screen first.

Expected log evidence:

- A local-action fast-path match before provider turn startup.
- No provider turn before the direct tool execution.
- No Computer-Use planner vision attachment for direct open/type/hotkey work.

## Scripted Fast Path

Scripted Fast Path is still local and deterministic, but it contains more than
one step. Examples:

- Open three terminals.
- Open Windows Terminal, type `claude`, press Enter.
- Start OpenClaw and pass a bounded prompt in the terminal.

Latency target:

- It should not call a provider.
- It should not collect vision.
- It should dispatch quickly; total user-visible time may include short waits
  between terminal launch, typing, and hotkey steps.

Operational boundary:

- Scripts must be hard-coded patterns with bounded step counts.
- The route must not guess screen coordinates or visual state.
- If a workflow becomes ambiguous, visual, or app-state dependent, route it to
  POAV Computer-Use instead of expanding the script.

## POAV Computer-Use

POAV means Plan, Observe, Act, Verify. This route is implemented by the
in-process `computer-use` harness and is the right path when Jarvis needs to see
the UI before acting.

Use POAV Computer-Use for:

- Clicking a named UI element.
- Filling a field that must be found visually or via UIA.
- Navigating an application where the current state is not known.
- Recovering from a failed local script when observation is required.

Boundary:

- POAV may collect screenshots and UIA trees.
- POAV may ask the configured planner/step model for a plan or verification.
- POAV is slower than the fast path by design.
- POAV should stay bounded by `[computer_use]` step, replan, and timeout
  settings.

## OpenClaw

OpenClaw is for delegated agent work, not low-latency desktop control.

Use OpenClaw for:

- Codebase changes and reviews.
- Multi-file research or investigation.
- Long-running autonomous tasks.
- Work that needs external agent isolation, terminals, or repo-level context.

Boundary:

- Do not route simple "open app", "type this", or "press this hotkey" commands
  to OpenClaw.
- Do not route visual UI control to OpenClaw when the POAV Computer-Use harness
  is the intended desktop-control mechanism.

## Configuration

Fast-path behavior is controlled separately from POAV Computer-Use:

```toml
[local_action]
enabled = true
direct_timeout_s = 2.0
harness_timeout_s = 20.0
max_scripted_steps = 10

[computer_use]
enabled = true
max_steps = 100
max_replans = 2
per_step_timeout_s = 30.0
verify_after_each_step = true
step_budget = 100
```

`ROUTER_TOOLS` should remain a pure router-visible dispatcher set. Direct local
tools are loaded separately as hidden BrainManager tools so the model does not
need to decide among OS-control primitives for simple local commands.

## Safety

- Direct and scripted fast paths still execute through ToolExecutor and the
  existing tool risk tiers.
- The fast path must not use `run_shell` for implicit desktop actions.
- Destructive commands are out of scope for the smoke script and for this
  routing model.
- PyAutoGUI failsafe still applies where pyautogui is used: move the mouse to
  the upper-left corner to abort a running GUI action.

## Smoke Validation

Dry-run validation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/smoke_computer_use_fast_path.ps1 -WhatIf
```

The smoke script:

- Compiles and imports the local-action, app-resolver, and direct tool modules.
- Parses representative direct, scripted, and POAV-routing examples where the
  current gate supports them.
- Reports whether `claude` or `claude.cmd` is on PATH.
- In `-WhatIf`, describes the Notepad/type/hotkey checks without launching apps
  or sending input.
- Without `-WhatIf`, opens Notepad, types `jarvis-smoke`, selects it, deletes
  it, and exits non-zero only for these core checks.

Manual validation checklist:

1. `Mach Spotify auf`
   Expected: direct fast path, no provider call, no vision collection.
2. `Hey Jarvis, kannst du Spotify aufmachen?`
   Expected: direct fast path, not smalltalk.
3. `Wie kann ich Chrome oeffnen?`
   Expected: no app launch; normal answer path.
4. `Klick auf den Senden Button`
   Expected: POAV Computer-Use with the original prompt.
5. `Oeffne drei Terminals`
   Expected: scripted fast path if supported by the current gate; otherwise no
   provider-side coordinate guessing.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Direct command calls a provider | Local-action gate did not match or `[local_action].enabled` is false | Check local-action logs and gate patterns |
| Direct command collects vision | Command was classified as visual/ambiguous | Confirm the utterance names a direct app or active-window action |
| `claude` launch fails | Claude CLI is not on PATH | Install Claude CLI or add `claude.cmd` to PATH |
| Notepad smoke types into the wrong window | Focus changed after launch | Re-run smoke with no manual focus changes |
| POAV is slow | Expected for observe/plan/verify path | Use direct/scripted phrasing for deterministic local actions |
