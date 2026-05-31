---
title: "ADR-0011: Pure Dispatcher (4 Tools)"
slug: adr-0011-router-pure-dispatcher
diataxis: adr
status: active
owner: harald
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0011 — Main Jarvis is a Pure Dispatcher with EXACTLY four tools

**Status:** Accepted (2026-04-25)
**Phase:** Persona refactor §3 — routing fix

## Context

The probe-drift inventory (`docs/persona-research.md` section 1) showed that 6 of 10 smalltalk inputs to `voice_e2e_probe` produced empty outputs — the main-Jarvis brain (Grok-4.1-fast or Gemini-2.5-flash as the router tier) reflexively issued `spawn_sub_jarvis` as a tool call. Cause: ROUTER_TOOLS in `factory.py` contained **eleven** tools (open-app, type-text, run-shell, search-web, screen-snapshot, remember, whoami, multi-spawn, spawn-sub-jarvis, plus hotkey/click from earlier work), while the ROUTER system prompt (`router.py:9`) explicitly promised only **four** (`run_shell`, `screenshot`, `multi_spawn`, `spawn_sub_jarvis`).

That was the source of the drift:
- The LLM had too many direct-action options.
- The ROUTER prompt commanded "when in doubt, SPAWN" — so the brain chose the spawn reflex even for trivial smalltalk.
- The force-spawn heuristic (`_FORCE_SPAWN_RE`) covered only repair verbs (`umsetz`/`reparier`/`fix`/`implementier`/`refactor`/`debug`) — **not** `lies`, `baue`, `installiere`, `öffne`, `mach`, `zeig`.

Master plan §22 specifies: four router tools (`screen_snapshot`, `multi_spawn`, `spawn_sub_jarvis` + the existing `run_shell`). The code reality (11 tools) contradicts the spec.

## Decision

**Main Jarvis = Pure Dispatcher.** Reduce ROUTER_TOOLS to exactly four, load the deterministic force-spawn heuristic from config, and add a ROUTER-DISCIPLINE prompt section.

### 1. ROUTER_TOOLS reduction

```python
# jarvis/brain/factory.py
ROUTER_TOOLS = frozenset({
    "run-shell",
    "screen-snapshot",
    "multi-spawn",
    "spawn-sub-jarvis",
})
```

Direct actions outside these four (`open_app`, `type_text`, `search_web`, `remember`, `whoami`) belong in the Sub-Jarvis tier — the router delegates them via `spawn_sub_jarvis`.

### 2. Deterministic force-spawn heuristic

`BrainManager._should_force_sub_jarvis(text)` runs in `generate()` BEFORE the LLM tool-use loop. Three regex patterns from `BrainRoutingConfig`:

- `spawn_verbs`: 30+ action verbs (DE+EN). Match → spawn (except the smalltalk allowlist).
- `external_system_markers`: `pr`/`prs`/`issue`/`repo`/`github`/`gitlab`/`branch`. Match → spawn (except the smalltalk allowlist).
- `smalltalk_allowlist`: Wins over verb/marker. `hallo`/`danke`/`wie geht`/`hauptstadt`/etc. → NEVER spawn.

Configurable via `[brain.routing]` in `jarvis.toml`; defaults from `BrainRoutingConfig` (Pydantic).

### 3. ROUTER-DISCIPLINE prompt

Directly after SCREEN-CONTEXT in `router.py:SYSTEM_PROMPT`:

```
ROUTER DISCIPLINE (Haiku-Tier — Persona-Mandat Phase 3)
Du bist der Dispatcher. Du planst nicht, paraphrasierst nicht, zerlegst nicht.
- Bei Smalltalk, einfachen Fakten oder allem in 1-2 Saetzen Beantwortbaren:
  antworte DIREKT ohne Tool-Call.
- Bei allem, was Datei-Zugriff, Code-Ausfuehrung, Computer-Use, Multi-Step-Planung
  oder externe Recherche erfordert: rufe spawn_sub_jarvis mit der User-Utterance
  VERBATIM auf (nicht zusammenfassen, nicht umformulieren).
SPAWN-CRITERIA — rufe spawn_sub_jarvis auf, WENN:
  • Verb deutet auf Datei-/Code-/System-Aktion
  • Request erwaehnt eine Datei, ein Projekt oder ein externes System
  • Multi-Step-Anweisung
  • Recherche, Analyse, Vergleich
DO-NOT-SPAWN — antworte direkt, WENN:
  • Greeting, Smalltalk, Faktenfrage aus dem Gedaechtnis
  • Klarfrage an den User
  • Status-Bestaetigung
```

### 4. D9 recursion guard (unchanged, now explicitly tested)

`spawn-sub-jarvis` must NEVER land in `SUB_TOOLS` — a Sub-Jarvis cannot recursively spawn new Sub-Jarvis instances. Master plan §6 D9. Test: `test_spawn_sub_jarvis_only_in_router_tools`.

## Consequences

+ **Smalltalk latency back under 1 s first token.** 5 smalltalk turns spawn 0 subprocesses (deterministically verified by tests: `test_smalltalk_dispatches_zero_spawn_calls` × 5).
+ **Spawn behavior is configurable.** The user can adjust the verb list, markers, and allowlist in `jarvis.toml` without a code edit.
+ **Code matches master plan §22.** Router tool set 1:1 as specified.
+ **Master-plan conflict resolved.** Before: the plan says 4 tools, the code loads 11. Now consistent.
- **Known verb gaps** (e.g. rare DE conjugations like `läufst`, or technical-jargon verbs like `migriere`/`provisioniere`) → are caught by the LLM tool choice, not by the deterministic heuristic. Acceptable.
- **The brain decides on ambiguity.** When neither the allowlist nor a verb/marker match fires, the brain falls back on its own tool choice. With the ROUTER-DISCIPLINE prompt section the LLM is appropriately instructed, but not 100 % deterministic.

## Alternatives Considered

- **A third tier between `router` and `sub_jarvis`** (e.g. "router-light" for direct actions): the complexity cost (three tier configs, three tool sets, cross-tier promotion logic) without a clear gain — two tiers suffice when the router is a pure dispatcher. **Rejected.**
- **Force-spawn heuristic as an LLM routing call** (Sonnet-4.6 as a pre-classifier): an extra 200 ms latency per turn, non-deterministic. JARVIS_REFACTOR_PLAN.md had proposed this — see `docs/persona-research.md` section 5.2. **Rejected** in favor of the faster regex heuristic.
- **No tool set on the router at all** (only `spawn_sub_jarvis` as the sole tool): kills direct actions like `screen_snapshot` ("What do you see on my screen?") that the router itself should be able to answer. **Rejected.**

## Subsequent Phase-7/8 Extensions

**Status:** Amendment 2026-04-28
**Rationale:** The code has evolved past the 2026-04-25 accepted state; ROUTER_TOOLS has two additional entries plus three dynamically loaded self-mod tools. This section documents the evolution so future reviewers do not read the appearance of drift as a violation.

### What changed since 2026-04-25

```python
# jarvis/brain/factory.py (Stand 2026-04-28)
ROUTER_TOOLS = frozenset({
    # Mandat-Phase-3 Baseline (Master-Plan §22, 4 Tools)
    "run-shell",
    "screen-snapshot",
    "multi-spawn",
    "spawn-sub-jarvis",
    # Phase-5-Endstand (re-introduziert):
    "dispatch-to-harness",
    # Phase 8.4 (Quality-Gate-Pipeline):
    "dispatch-with-review",
})

# Plus drei nicht-entry_points-Tools, direkt im _load_tools_for_tier
# registriert (Phase 7.3 / Plan §AD-2 — Hauptjarvis-only Self-Mod):
SELF_MOD_TOOL_NAMES_ROUTER = frozenset({
    "list_mutable_settings",
    "get_config_value",
    "set_config_value",
})
```

### Rationale per extension

| Tool | Phase | Rationale | Recursion guard? |
|---|---|---|---|
| `dispatch-to-harness` | 5 (final state) | Main Jarvis needs the direct harness path without Sub-Jarvis spawn latency. Use case: the user asks "What do you see on the screen?" → Main Jarvis calls `screen-snapshot` and immediately `dispatch-to-harness` with the observation output to a coding harness, without triggering the 30-min-hard-cap Sub-Jarvis spawn. | n/a — the tool is not self-recursive. |
| `dispatch-with-review` | 8.4 (Plan §6.4 quality-gate pipeline) | Main Jarvis calls the review pipeline explicitly. The pipeline worker IS itself a Sub-Jarvis-equivalent construct; if a Sub-Jarvis could in turn call `dispatch-with-review`, it would spawn the pipeline worker, which is again a Sub-Jarvis → a recursion vector analogous to `spawn-sub-jarvis`. | **Yes** — `SUB_TOOLS` does **not** contain the tool. Test: `test_recursive_tools_only_in_router` (`tests/unit/brain/test_routing.py`). |
| `list_mutable_settings`, `get_config_value`, `set_config_value` | 7.3 (Plan §AD-2 self-mod) | Setting mutation may only be triggered by the main-Jarvis tier. A Sub-Jarvis worker (Opus 4.7) would carry too high a privilege-escalation potential. The tools are registered directly in the loader (not via entry_points), so an accidental sub-tier activation through entry_points discovery is ruled out. | **Yes** — the tools are hardcoded main-Jarvis-only. |

### What does NOT break the mandate-spirit guarantees

- **Pure Dispatcher** is preserved: none of the Phase-7/8 tools is a direct action from the user's point of view (`open_app`, `type_text`, `search_web`, `remember`, `whoami` remain sub-tier-only). The two dispatch tools are meta-tools (they delegate to a worker), and the three self-mod tools are trivial state mutations.
- **The deterministic force-spawn heuristic** is unchanged: `_should_force_sub_jarvis` still triggers on verb/marker and respects the smalltalk allowlist.
- **The D9 recursion guard** was **extended** rather than weakened: two tools instead of one (`spawn-sub-jarvis` + `dispatch-with-review`) are explicitly excluded from `SUB_TOOLS`.
- **5 smalltalk inputs spawn 0 subprocesses** — the test `test_smalltalk_dispatches_zero_spawn_calls × 5` is still green.

### When the appearance of drift becomes a real violation

- A **direct action** migrates into `ROUTER_TOOLS` (e.g. `open-app`). → Mandate violation; the ROUTER would again know direct actions, which we already had on 2026-04-23.
- A **recursion-vector** tool migrates into `SUB_TOOLS`. → D9 violation; a Sub-Jarvis could recursively spawn.
- `_should_force_sub_jarvis` is replaced by an LLM pre-classifier. → ADR-0011 "Alternatives Considered" explicitly rejected this; a code re-introduction would be worth a new ADR.

If none of these three points occurs, the extension of the tool set is by definition within the pure-dispatcher corridor.

## Amendment 2026-05-24 — CLI-Integration (`cli-tools`)

**Status:** Amendment 2026-05-24
**Phase:** CLI-Integration (`docs/superpowers/specs/2026-05-24-cli-integration-design.md`)

### What changed

`ROUTER_TOOLS` gains one entry: `cli-tools`.

```python
# jarvis/brain/factory.py (Stand 2026-05-24)
ROUTER_TOOLS = frozenset({
    ...,            # unchanged Phase-3/5/7/8/Awareness/B5 set
    "cli-tools",    # NEW — virtual CLI loader
})
```

`cli-tools` is a **virtual loader** (`jarvis/clis/loader.py:CliToolLoader`,
`is_virtual_loader=True`). The `_load_tools_for_tier` expansion path
(`factory.py`) recognises it and replaces the single entry with one
`cli_<name>` tool per **connected & usable** CLI (`cli_gcloud`, `cli_gh`,
`cli_docker`, …). Only connected CLIs become tools, so the router's tool
surface stays small (typically 1-5), not all 20 catalogued CLIs.

### Why it belongs in `ROUTER_TOOLS` (and nowhere else)

The first integration attempt placed CLI tools only in the legacy full-brain's
`active_tools` set, reachable solely via `JARVIS_BRAIN=legacy`. The production
path (`build_default_brain` → `_phase2_full_brain("router")` →
`_load_tools_for_tier("router")`) filters entry-points against `ROUTER_TOOLS`,
which did not contain `cli-tools` — so the default voice/chat brain never saw a
single `cli_<name>` tool. Adding `cli-tools` to `ROUTER_TOOLS` is the fix.

| Tool | Phase | Rationale | Recursion guard? |
|---|---|---|---|
| `cli-tools` | CLI integration | Each expanded `cli_<name>` tool is a **direct, safety-gated action** (the user wants "list my GCP projects" answered, not delegated to a 30-min worker spawn). It is the MCP/plugin model for command-line tools. Per-CLI risk patterns (`spec.risk.{whitelist,blacklist}_patterns`) flow into the `RiskTierEvaluator` via `make_cli_patterns_fn`. | **n/a as a recursion vector** — a `cli_<name>` tool runs a subprocess, it cannot spawn the supervisor. It is router-tier only and never enters any worker tool-set (AP-5/AP-14); the deleted Sub-Jarvis tier / `SUB_TOOLS` cannot re-acquire it. |

### Pure-Dispatcher spirit is preserved

A `cli_<name>` tool is not a generic direct-action like `open_app`/`type_text`
(those stay off the router by design). It is a **gated external-system call**
with a declared binary, declared risk tier, and per-CLI allow/deny patterns —
architecturally the same shape as the existing `dispatch-to-harness`
meta-action: the router calls it directly instead of forcing a Sub-Jarvis spawn,
avoiding the spawn-latency tax for read-only CLI queries.

### Live-reload contract

Connecting/disconnecting a CLI in the UI publishes `BrainToolsChanged` (derived
from `CliStatusChanged` at the registry chokepoint), which the live
`BrainManager.refresh_tools()` consumes to re-expand `cli-tools` — no restart.
Both the brain (via `CliToolLoader.expand()`) and the safety layer (via
`make_cli_patterns_fn`) resolve the SAME shared registry
(`jarvis.clis.shared.get_active_registry`), eliminating the split-brain bug.

### Regression guards

- `tests/unit/brain/test_routing.py::test_cli_tools_in_router_tools`
- `tests/unit/brain/test_routing.py::test_router_tools_stays_frozenset`
- `tests/unit/brain/test_routing.py::test_no_spawn_tool_leaked_into_worker_set`
- `tests/unit/brain/test_routing.py::test_router_tools_is_pure_dispatcher_set`
  (exact-match set updated to include `cli-tools`)
- `tests/integration/test_cli_integration.py` (loader expand wired into a
  built router brain; live-reload re-expand on `BrainToolsChanged`)

## Amendment 2026-05-29 — Computer-Use Router Tool (`computer-use`)

**Status:** Amendment 2026-05-29
**Phase:** Computer-Use rework Wave 1 (`~/.claude/plans/goofy-singing-piglet.md`)

### What changed

`ROUTER_TOOLS` gains a thirteenth entry: `computer-use`. It is a first-class,
clearly-described tool the router calls to drive the user's **live desktop**
(open apps, click, type, scroll, operate any GUI). Implementation:
`jarvis/plugins/tool/computer_use_tool.py:ComputerUseTool` (LLM-facing name
`computer_use`), wired in `jarvis/brain/factory.py:_load_tools_for_tier` with the
shared `HarnessManager`. It delegates to the in-process computer-use harness
(`jarvis/plugins/harness/computer_use.py` → `screenshot_only_loop.py`).

### Why it was needed (the bug)

The router previously had **no honest path** for live-desktop actions:

- `spawn-openclaw` runs a worker in an isolated git worktree — it can edit code
  and research, but it can **never** touch the user's live desktop.
- The router prompt told the model to reach the computer-use harness through the
  two-level `dispatch_to_harness(harness="computer-use", …)` indirection — but
  that tool's schema description only mentions "OpenClaw, Codex, code-editing,
  research". LLMs select tools primarily by their **description**, so the model
  never picked it for desktop actions. For "Öffne ein neues Terminal für mich,
  starte darin Cloud-Konfiguration" Gemini invented a non-existent tool
  (`terminal_count`) and the anti-silence guard spoke a refusal
  (`tool_use_loop.py:407-423`; live log 2026-05-29 12:55:41).

A dedicated tool with an unambiguous name + description is the strongest signal
for LLM tool selection and removes the fragile indirection. The router prompt
(`router.py:SYSTEM_PROMPT`) was updated to route every on-screen/app/GUI action
to `computer_use` and to explicitly state that `spawn_openclaw` cannot touch the
desktop.

### Why it belongs in `ROUTER_TOOLS` (and nowhere else)

`computer-use` is a **direct, safety-gated action**: every individual click /
type / key inside the loop is gated through the `ToolExecutor` risk tiers
(ADR-0008). It is **not** a spawn — it cannot re-enter the supervisor, so it
carries no D9 recursion risk and never enters a worker tool-set (AP-5/AP-14).
It is router-tier only; the deleted Sub-Jarvis tier / `SUB_TOOLS` cannot
re-acquire it.

### Regression guards

- `tests/unit/brain/test_routing.py::test_computer_use_in_router_tools`
- `tests/unit/brain/test_routing.py::test_computer_use_tool_is_not_a_spawn_in_local_action_set`
- `tests/unit/brain/test_routing.py::test_router_tools_is_pure_dispatcher_set`
  (exact-match set updated to include `computer-use`)
- `tests/unit/plugins/tool/test_computer_use_tool.py` (identity, schema, dispatch
  to the `computer-use` harness, empty-goal rejection)

## Amendment: Profile-Write Router Tool (`update-profile`, 2026-05-30)

`ROUTER_TOOLS` gains `update-profile` — a deterministic, brain-driven writer
for the **structured** user profile (`data/workspace/USER.md`, the five clusters
`identity/communication/work_style/values/relationship`).

### Why a tool, and why router-tier

The Desktop App "Knowledge matrix" and the per-turn system prompt both read the
structured profile (`UserProfile.render_for_prompt()` is injected in
`manager.py:_build_system_prompt` on every turn). The legacy background
`Curator` that used to auto-write those clusters is **soft-disabled** since
2026-05-17 (`[memory.legacy_curator] enabled = false`) to avoid the "two
diverging notebooks" drift with the WikiCurator. The active WikiCurator only
writes free-form wiki **prose** — it never touches the structured clusters.
Result: durable personal facts the user states ("call me Boss", "switch to
German") never reached the structured profile, so the matrix froze and the brain
stopped learning structured facts even though it still *loaded* the (stale)
profile each turn.

`update-profile` closes that gap **without resurrecting a parallel background
extractor** (which would re-introduce the drift + a per-turn LLM cost on the
€5-VPS baseline). The brain persists a fact only when it consciously calls the
tool — exactly the `wiki-ingest` precedent, one tier up the structure ladder.

### Why it belongs in `ROUTER_TOOLS` (and nowhere else)

It is a **direct, deterministic write** (no LLM, no spawn). It is tiered
`monitor`: a real side effect (writes USER.md) that runs without a confirmation
prompt (anti-confirmation-fatigue) but is logged for audit — identical reasoning
to `wiki-ingest`. It cannot re-enter the supervisor, so it carries no D9
recursion risk and never enters a worker tool-set (AP-5/AP-14). It mutates the
**same live `UserProfile` instance** the `BrainManager` renders from (the factory
passes one instance to both `_load_tools_for_tier` and the manager), so the next
turn's system prompt reflects the change with no cache-invalidation, and it emits
`ProfileUpdated` so the Desktop matrix live-updates.

The exact-match `ROUTER_TOOLS` assertion's "no open_app/whoami" rule is
unchanged: those are delegatable direct actions. `update-profile` is a
*sanctioned write tool with its own ADR entry*, the same exception class as
`wiki-ingest`.

### Regression guards

- `tests/unit/brain/test_routing.py::test_update_profile_in_router_tools`
- `tests/unit/brain/test_routing.py::test_factory_wires_update_profile_tool_into_router_set`
- `tests/unit/brain/test_routing.py::test_router_tools_is_pure_dispatcher_set`
  (exact-match set updated to include `update-profile`)
- `tests/unit/brain/test_routing.py::test_system_prompt_includes_profile_write_directive_when_tool_wired`
  + `…_omits_profile_directive_when_tool_absent`
- `tests/unit/plugins/tool/test_profile_update.py` (set/append/dedupe, canonical
  field allow-list, do-not-record privacy gate, ProfileUpdated emit, missing
  profile fallback)

## Verweise

`ROUTER_TOOLS` gains `update-profile` — a deterministic, brain-driven writer
for the **structured** user profile (`data/workspace/USER.md`, the five clusters
`identity/communication/work_style/values/relationship`).

### Why a tool, and why router-tier

The Desktop App "Knowledge matrix" and the per-turn system prompt both read the
structured profile (`UserProfile.render_for_prompt()` is injected in
`manager.py:_build_system_prompt` on every turn). The legacy background
`Curator` that used to auto-write those clusters is **soft-disabled** since
2026-05-17 (`[memory.legacy_curator] enabled = false`) to avoid the "two
diverging notebooks" drift with the WikiCurator. The active WikiCurator only
writes free-form wiki **prose** — it never touches the structured clusters.
Result: durable personal facts the user states ("call me Boss", "switch to
German") never reached the structured profile, so the matrix froze and the brain
stopped learning structured facts even though it still *loaded* the (stale)
profile each turn.

`update-profile` closes that gap **without resurrecting a parallel background
extractor** (which would re-introduce the drift + a per-turn LLM cost on the
€5-VPS baseline). The brain persists a fact only when it consciously calls the
tool — exactly the `wiki-ingest` precedent, one tier up the structure ladder.

### Why it belongs in `ROUTER_TOOLS` (and nowhere else)

It is a **direct, deterministic write** (no LLM, no spawn). It is tiered
`monitor`: a real side effect (writes USER.md) that runs without a confirmation
prompt (anti-confirmation-fatigue) but is logged for audit — identical reasoning
to `wiki-ingest`. It cannot re-enter the supervisor, so it carries no D9
recursion risk and never enters a worker tool-set (AP-5/AP-14). It mutates the
**same live `UserProfile` instance** the `BrainManager` renders from (the factory
passes one instance to both `_load_tools_for_tier` and the manager), so the next
turn's system prompt reflects the change with no cache-invalidation, and it emits
`ProfileUpdated` so the Desktop matrix live-updates.

The exact-match `ROUTER_TOOLS` assertion's "no open_app/whoami" rule is
unchanged: those are delegatable direct actions. `update-profile` is a
*sanctioned write tool with its own ADR entry*, the same exception class as
`wiki-ingest`.

### Regression guards

- `tests/unit/brain/test_routing.py::test_update_profile_in_router_tools`
- `tests/unit/brain/test_routing.py::test_factory_wires_update_profile_tool_into_router_set`
- `tests/unit/brain/test_routing.py::test_router_tools_is_pure_dispatcher_set`
  (exact-match set updated to include `update-profile`)
- `tests/unit/brain/test_routing.py::test_system_prompt_includes_profile_write_directive_when_tool_wired`
  + `…_omits_profile_directive_when_tool_absent`
- `tests/unit/plugins/tool/test_profile_update.py` (set/append/dedupe, canonical
  field allow-list, do-not-record privacy gate, ProfileUpdated emit, missing
  profile fallback)

## References

- Implementation: `jarvis/brain/factory.py:ROUTER_TOOLS` + `SELF_MOD_TOOL_NAMES_ROUTER`, `jarvis/brain/manager.py:_should_force_sub_jarvis`, `jarvis/brain/router.py:SYSTEM_PROMPT`.
- Config: `jarvis/core/config.py:BrainRoutingConfig`, `jarvis.toml:[brain.routing]`.
- Tests: `tests/unit/brain/test_routing.py` (26 cases: 5 smalltalk × 2 + 5 spawn × 2 + 3 PC-control + 4 consistency asserts including the new `test_recursive_tools_only_in_router`).
- Persona mandate: `Jarvis-Behavior/persona-delegation-mandate.md` §"Phase 3 — Routing-Fix".
- Research: `docs/persona-research.md` sections 2 (routing-bug repro) + 5.1 (master-plan conflicts).
- Master plan §22: `.claude/plans/also-er-muss-auch-lexical-pond.md` line 1610 ff.
- Before/after: `docs/persona-refactor-results.md` section 1+2.
- Phase 7.3 (self-mod): `docsplansphase-7-self-mod/PROJEKT_KONTEXT.md` §AD-2.
- Phase 8.4 (quality gate): commit `0aa8a49a feat(review): phase 8.4 — dispatch_with_review tool and policy`.


## Amendment (2026-05-31): App-Control Tools

Three tools join the router set so the brain has a complete grip on the Desktop
App configuration by voice/chat:

- `describe-app-settings` (safe, read-only) - a complete, secret-free snapshot:
  the active brain/TTS/STT/subagent provider (and which have a stored key), the
  key settings, and the configured MCP servers. The "full overview" capability.
- `switch-provider` (ask, echo-confirm) - switch *which* provider is active for a
  tier ("switch from Grok to Gemini"). Reuses the exact 3-layer persist +
  live-apply path the REST endpoints use, via `jarvis.core.runtime_refs`.
- `manage-mcp-server` (ask, echo-confirm) - add/remove/enable/disable an MCP
  server in `mcp.json` via `jarvis.mcp.state`. A newly added server starts
  **disabled** (review before activate - AP-15 spirit).

All three are direct safe/ask-gated actions, NEVER spawns - they do not enter any
worker tool set (AP-5/AP-14). Security boundary (binding): no raw secret *value*
is ever accepted. `switch-provider` only flips the active provider (the target
key must already exist in the Credential Manager); `manage-mcp-server` uses
`$SECRET_NAME` placeholders. Raw key writes stay UI-only (`/api/secrets/{key}`)
per AP-2 and the self-mod `FORBIDDEN_PATTERNS` doctrine. The shared credential
check lives in `jarvis.brain.app_control.is_credential_present` and is imported
back by `provider_routes` so the UI and the tool never drift (BUG-008 class).

Known follow-up: the deterministic pre-brain force-spawn gate
(`BrainManager._should_force_spawn`) can pre-empt utterances containing an
external-system marker (e.g. "enable the GitHub MCP") before the brain sees the
tool. The primary examples (switch Grok->Gemini, update settings, update the MCP
config) do not hit a marker. A settings-intent allowlist for the gate is deferred.

Spec: `docs/superpowers/specs/2026-05-31-app-control-tools-design.md`.
