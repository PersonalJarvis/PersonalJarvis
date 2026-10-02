# JARVIS Lab integration matrix

This document is the working contract for the `jarvis-lab` branch. The goal is
not to vendor eleven agent frameworks into one process. The goal is to keep
PersonalJarvis as the product/core and absorb the strongest compatible patterns
behind its existing protocols, policy engine, event bus, Mission Manager and
plugin boundaries.

No third-party source code is copied merely because a project is listed here.
Before any direct code reuse, its license and dependency impact must be checked.
Prefer original implementations of the architectural pattern when that keeps the
runtime smaller and the safety boundary clearer.

## Source projects

| Source | Capability worth carrying forward | PersonalJarvis baseline | JARVIS Lab action |
|---|---|---|---|
| PersonalJarvis/PersonalJarvis | Supervisor, Mission Manager, workers, plugins/MCP, Computer-Use v2, agent society, browser workers, voice, approvals | Product base | Extend in place; never create a parallel orchestrator |
| PB-Builds-creator/Jarvis-For-Mac | macOS-native, local-first desktop control; explicit safety around sensitive actions | AX observation and macOS platform adapters already exist | Accessibility-first actuation + hardware-input handoff; qualify on a real Mac |
| browser-use/browser-use | Persistent browser agent, DOM-aware web automation, browser session isolation | Society agents already have browser-use-backed browser sessions | Keep as the web-specialist path; deterministic native-chrome/cross-app handoff to Computer-Use |
| mem0ai/mem0 | Selective durable facts, ranked recall, provenance-aware memory | SocietyMemory already has persistent notebooks, staging, recall and FTS/provenance | Reuse existing store; improve consolidation/retrieval only where measurements show a gap |
| letta-ai/letta | Long-lived stateful agents, checkpoint/resume and bounded context | Society agents, checkpoints, notebooks and persistent conversations already cover the core pattern | Strengthen state continuity rather than adding a second agent runtime |
| openclaw/openclaw | Skills/tools/plugins, MCP, scheduled intents and modular agent harnesses | Plugins, skills, MCP, routines/automations and agent society already exist | Reuse current extension system; no second plugin or scheduler stack |
| BatmanOnTop/jarvis | Fast local commands, wake/STT/TTS, learned shortcuts, handoff of heavy coding work | Voice, command routing and coding-agent/IDE paths already exist | Audit latency and local fast paths after desktop-control work |
| simular-ai/Agent-S | Visual grounding, specialist/generalist desktop agents, action experience and evaluation | Computer-Use v2 already implements perceive-act-verify, grounding, ledger and visual verification | Add macOS semantic grounding and focused evaluation; preserve prompt-injection defenses |
| onixhdz/computer-use-mcp | Accessibility-first semantic element actions with pixel fallback | AX tree was observation-only for `click_element` | AXPress + AXFocused before verified pointer fallback, fail-closed on identity drift |
| microsoft/UFO | Host-agent/app-agent decomposition, shared task state and cross-app orchestration | Supervisor -> Mission Manager -> capability workers plus society blackboard already implement this shape | Improve capability handoff/receipts; do not add another HostAgent |

## Architectural rules

1. **One orchestrator.** Supervisor and Mission Manager remain the only top-level
   task orchestration path.
2. **One safety boundary.** Tools still execute through PersonalJarvis policy,
   approvals, grants, target guards and cancellation. New integrations do not
   bypass them.
3. **Semantic before pixels on macOS.** Preferred order for a labelled desktop
   target is API/native app integration -> Accessibility action -> verified
   keyboard/pointer -> screenshot/vision coordinates.
4. **Browser is a specialist.** Website work stays in the browser-use/CDP path
   when possible; general desktop Computer-Use handles browser chrome or
   cross-app transitions.
5. **One durable memory system.** Existing SocietyMemory/Obsidian/SQLite stores
   remain authoritative. Mem0/Letta ideas may improve ranking, consolidation or
   state continuity but must not create a competing source of truth.
6. **Fail closed on identity drift.** If the active app, window or semantic
   element no longer matches the observed target, re-perceive instead of
   falling back to stale coordinates.
7. **Human input wins.** On macOS, recent physical HID activity makes automated
   input yield rather than fight the user's mouse or keyboard. Synthetic Jarvis
   input is excluded by reading Quartz's hardware event-source state.
8. **No secret capture.** Accessibility helpers never read secure text-field
   values and Computer-Use keeps the existing login/2FA/CAPTCHA handoff.
9. **No paid probes.** Tests use fakes/local paths unless a live provider test is
   explicitly requested and approved.

## Workstream status

### A. macOS Accessibility-first actuation — implemented, live qualification pending

Implemented on `jarvis-lab`:

- `jarvis/cu/macos_semantic.py`
  - resolve the AX element at the observed control centre;
  - re-identify by AXIdentifier/name/role, climbing a bounded ancestor chain;
  - execute native `AXPress` when supported;
  - focus canonical `Edit` controls through `AXFocused` when press is not the
    right semantic action;
  - distinguish unsupported focus from a failed/rejected focus and only allow
    pixel fallback for the former;
  - check the captured-window identity immediately before the native action;
  - refuse stale/unresolvable targets rather than clicking old pixels;
  - never inspect `AXValue` for secure text fields;
  - lazy PyObjC imports preserve Windows/Linux/headless imports.
- `click_element` prefers those semantic actions on macOS and uses the existing
  verified pointer actuator only for unsupported semantic operations.
- `jarvis/cu/human_activity.py` reads Quartz HID-only activity and yields to
  recent physical mouse/keyboard use without mistaking Jarvis synthetic input
  for the user.
- human takeover is enforced twice on macOS: at the shared actuator facade and
  again at the Quartz dispatch boundary immediately before synthetic events.
  Late takeover therefore remains typed as `HumanInputTakeover` instead of
  degrading into a generic actuation failure.
- `AXPress` and `AXFocused` receive the same ownership guard at their native
  mutation boundary, after semantic lookup/probing and with a final foreground
  window re-check.
- tool results preserve takeover as the structured `human_takeover` outcome;
  the existing Computer-Use loop yields control, polls the side-effect-free HID
  state, honours cancellation, then re-observes the desktop before resuming.
  Unknown/unreadable ownership stays fail-closed.
- even the reduced-context legacy drag fallback routes through the protected
  macOS actuator; there is no direct pyautogui bypass on macOS.
- unit/integration coverage exercises AXPress, AXFocused, stale-target refusal,
  missing permission, secure-field handling, late-dispatch takeover,
  pause/resume, cancellation and guarded drag cleanup.

Still requires real-Mac qualification: Accessibility permission, AXPress on a
native button, AXFocused on a native/search field, human takeover, then verified
pointer fallback.

### B. Browser/desktop handoff — implemented

The per-agent browser-use session remains the owner of webpage/DOM work. A
language-aware deterministic boundary (English, German and Italian) refuses
explicit browser-chrome/native-desktop tasks such as the address bar, extension
buttons, OS file pickers, moving/resizing the browser window and cross-app drag
or switching, returning a `core:computer-use` handoff instead. Ordinary URL,
page, form and CDP tab work stays with browser-use.

Native full-Chrome-window capture on macOS remains an upstream parity gap. It is
not falsely advertised as implemented; page-only CDP streaming remains the
macOS behavior until a separate ScreenCaptureKit/AX capability is qualified.

### C. Memory/state continuity — audit complete; no second store

Existing code already supplies the useful Mem0/Letta patterns: per-agent
notebooks, ranked recall, staging/provenance, approval-gated shared knowledge,
checkpoints, persistent conversations and a `consolidation_recommended` signal
when a notebook exceeds its prompt budget. Adding a second memory database now
would create two sources of truth without evidence of better recall.

Future entity links or embeddings remain optional and local-first, with FTS and
the current deterministic ranker as dependency-free fallbacks.

### D. Local voice/fast commands — queued

After desktop control is qualified, profile wake -> route -> local command
latency. Commands that can be answered deterministically should not pay an LLM
round trip. Keep STT language, reply language and interface language as separate
settings. Italian UI is handled as its own source package; runtime reply/wake
support must be expanded only through the backend source-of-truth lists.

### E. Evaluation / MacAgentBench — phase zero implemented

`jarvis/cu/macos_readiness.py` performs a side-effect-free native readiness
report: Screen Recording, Accessibility, Input Control, AX-tree observation,
actuator construction and hardware-input handoff. It never clicks, types,
launches applications or prompts for permission. Recent human input is reported
as an active handoff state, not as a missing capability.

`jarvis/cu/macos_bench.py` now defines the first deterministic receipt
contract, `physical-user-takeover`. CI can evaluate safety evidence without
posting input: takeover detected, zero synthetic events after detection, pause
until HID idle, re-observation before the next action and cancellation with no
later action. The scenario is explicitly marked `live_required=True`; a fake
receipt passing in CI is not native qualification.

Live MacAgentBench remains the release gate for observable receipts: semantic
target hit, stale-target refusal, focus/type landing, human takeover,
cross-window handoff, browser-to-desktop transition, cancellation, permission
degradation and prompt-injection resistance.

## Release gate

A feature is not considered complete merely because portable tests pass. macOS
native behavior must be labelled **unqualified** until it has been exercised on
a real macOS desktop with the relevant system permission. The `jarvis-lab`
branch remains an integration branch until those receipts are captured.
