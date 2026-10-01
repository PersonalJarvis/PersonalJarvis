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
| PB-Builds-creator/Jarvis-For-Mac | macOS-native, local-first desktop control; explicit safety around sensitive actions | AX observation and macOS platform adapters already exist | Make actuation Accessibility-first, then qualify on a real Mac |
| browser-use/browser-use | Persistent browser agent, DOM-aware web automation, browser session isolation | Society agents already have browser-use-backed browser sessions | Keep as the web-specialist path; improve desktop/browser handoff instead of duplicating browser control |
| mem0ai/mem0 | Selective durable facts, ranked recall, provenance-aware memory | SocietyMemory already has persistent notebooks, staging, recall and FTS/provenance | Reuse existing store; improve consolidation/retrieval only where measurements show a gap |
| letta-ai/letta | Long-lived stateful agents, checkpoint/resume and bounded context | Society agents, checkpoints, notebooks and persistent conversations already cover the core pattern | Strengthen state continuity rather than adding a second agent runtime |
| openclaw/openclaw | Skills/tools/plugins, MCP, scheduled intents and modular agent harnesses | Plugins, skills, MCP, routines/automations and agent society already exist | Reuse current extension system; no second plugin or scheduler stack |
| BatmanOnTop/jarvis | Fast local commands, wake/STT/TTS, learned shortcuts, handoff of heavy coding work | Voice, command routing and coding-agent/IDE paths already exist | Audit latency and local fast paths after desktop-control work |
| simular-ai/Agent-S | Visual grounding, specialist/generalist desktop agents, action experience and evaluation | Computer-Use v2 already implements perceive-act-verify, grounding, ledger and visual verification | Add macOS semantic grounding and focused evaluation; preserve prompt-injection defenses |
| onixhdz/computer-use-mcp | Accessibility-first semantic element actions with pixel fallback | AX tree was observation-only for `click_element` | **In progress:** AXPress + AXFocused before verified pointer fallback |
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
7. **No secret capture.** Accessibility helpers never read secure text-field
   values and Computer-Use keeps the existing login/2FA/CAPTCHA handoff.
8. **No paid probes.** Tests use fakes/local paths unless a live provider test is
   explicitly requested and approved.

## Workstream status

### A. macOS Accessibility-first actuation — active

Implemented on `jarvis-lab`:

- `jarvis/cu/macos_semantic.py`
  - resolve the AX element at the observed control centre;
  - re-identify by AXIdentifier/name/role, climbing a bounded ancestor chain;
  - execute native `AXPress` when supported;
  - focus canonical `Edit` controls through `AXFocused` when press is not the
    right semantic action;
  - check the captured-window identity immediately before the native action;
  - refuse stale/unresolvable targets rather than clicking old pixels;
  - never inspect `AXValue` for secure text fields;
  - lazy PyObjC imports preserve Windows/Linux/headless imports.
- `click_element` now prefers those semantic actions on macOS and uses the
  existing verified pointer actuator only for unsupported semantic operations.
- unit/integration coverage exercises AXPress, AXFocused, stale-target refusal,
  missing permission and secure-field handling.

Still requires real-Mac qualification: Accessibility permission, AXPress on a
native button, AXFocused on a native/search field, then verified pointer fallback.

### B. Browser/desktop handoff — queued

Preserve the existing per-agent browser-use session. Add explicit routing rules
so page work stays DOM/CDP-native and only browser chrome/cross-app work falls to
Computer-Use. Native full-Chrome-window capture on macOS is currently an upstream
parity gap and should be isolated behind a capability probe, not assumed.

### C. Memory/state continuity — audit before change

Existing code already supplies most Mem0/Letta patterns: per-agent notebooks,
ranked recall, staging/provenance, approval-gated shared knowledge, checkpoints
and persistent conversations. Measure recall/consolidation quality before adding
new storage. Any future entity links or embeddings must be optional and local-
first, with FTS remaining a dependency-free fallback.

### D. Local voice/fast commands — queued

After desktop control is stable, profile wake -> route -> local command latency.
Commands that can be answered deterministically should not pay an LLM round trip.
Keep STT language, reply language and interface language as separate settings.

### E. Evaluation — queued

Build MacAgentBench around observable receipts rather than model self-report:
semantic target hit, stale-target refusal, focus/type landing, cross-window
handoff, browser-to-desktop transition, cancellation, permission degradation and
prompt-injection resistance.

## Release gate

A feature is not considered complete merely because portable tests pass. macOS
native behavior must be labelled **unqualified** until it has been exercised on
a real macOS desktop with the relevant system permission. The `jarvis-lab`
branch remains a draft integration branch until those receipts are captured.
