# CLI Integration — Make Jarvis Drive Any CLI As Seamlessly As MCPs/Plugins

Date: 2026-05-24
Status: Design (autonomous goal-run; approval gate waived per explicit user autonomy mandate)
Author: Orchestrator (Opus 4.7)

## Goal

Jarvis must control arbitrary command-line tools (gcloud, aws, gh, docker, kubectl,
firebase, …) through natural language, exactly as seamlessly as it already calls MCP
servers and plugins. A dedicated section in the desktop app ("CLI Test Hub") lets the
user issue a plain-language instruction and watch Jarvis pick the right `cli_<name>`
tool, run a real command through the safety gate, and report the real result.

The subsystem `jarvis/clis/` already exists and is well-built (catalog of 20 CLIs,
prober, auth manager, usage log, REST API, ClisView UI) — but it was never correctly
wired into the production brain. This is the "we did it before but it didn't work well"
the user referenced.

## Root Cause Analysis (why the first attempt failed) — verified file:line

1. **CLI tools invisible to the production brain.**
   `cli-tools` (the virtual loader) is listed only in `_legacy_full_brain`'s
   `active_tools` set (`jarvis/brain/factory.py:876-877`), reachable solely via
   `JARVIS_BRAIN=legacy`. The production path is
   `build_default_brain()` → `_phase2_full_brain(tier="router")` →
   `_load_tools_for_tier("router")`, which filters entry-points against the
   `ROUTER_TOOLS` frozenset (`factory.py:40-77`). **`cli-tools` is not in `ROUTER_TOOLS`**,
   so the default voice/chat brain never sees any `cli_<name>` tool.

2. **Split-brain registry.**
   `CliToolLoader.__init__` constructs its **own** `CliToolRegistry()`
   (`jarvis/clis/loader.py:33`). The UI server constructs a **different** registry,
   bootstraps it, attaches the bus, and publishes it via `set_active_registry()`
   (`jarvis/ui/web/server.py:475-481`). The loader's private registry is never
   bootstrapped in the async server context, so `expand()` returns `[]`
   (`loader.py:36-51`). Meanwhile the safety layer reads patterns from the *UI*
   registry via `get_active_registry()` (`jarvis/clis/risk_integration.py:32-36`).
   Tools and safety patterns come from two different registry instances.

3. **Dead entry point.**
   `pyproject.toml:184` registers
   `spawn-cli-worker = "jarvis.plugins.tool.spawn_cli_worker:SpawnCliWorkerTool"` —
   the file does not exist.

4. **Zero test coverage.** No test imports `jarvis.clis` (grep confirmed).

5. **Live-reload half-wired.** `BrainManager.attach_to_bus()` subscribes to
   `BrainToolsChanged` → `refresh_tools()` (`jarvis/brain/manager.py:2472-2495`), but
   the CLI registry only publishes `CliStatusChanged` on connect/disconnect
   (`registry.py:131-166`). Connecting a CLI in the UI therefore does not refresh the
   live brain's tool set.

## Design

**Principle:** expose every *connected & usable* CLI to the production router brain as a
`cli_<name>` tool (the MCP/plugin model), backed by ONE shared, bus-connected registry.
Only connected CLIs become tools, so the router's tool surface stays small (typically
1–5), not all 20.

### Backend (Agent A — "feature")

- **A1 Shared registry.** `CliToolLoader.expand()` resolves the shared registry via
  `jarvis.clis.shared.get_active_registry()` first. Fallback to a lazily-bootstrapped
  private registry only when no active registry exists (headless `jarvis-ask` / voice
  without web). Brain and UI must see the same connected CLIs on the same bus.
- **A2 Wire into the router.** Add `"cli-tools"` to `ROUTER_TOOLS` (`factory.py:40`).
  The virtual-loader expansion path already handles it (`factory.py:206-213`). Amend
  ADR-0011 and extend `tests/unit/brain/test_routing.py` (CLAUDE.md mandate for any
  `ROUTER_TOOLS` change).
- **A3 Live-reload bridge.** Connect/disconnect must publish `BrainToolsChanged`
  (in addition to / derived from `CliStatusChanged`) so the live brain re-expands its
  tool set without a restart.
- **A4 Dead entry point.** Either implement a real `SpawnCliWorkerTool` that dispatches
  a heavy multi-step CLI task to a background mission/worker that has CLI access (must
  stay OUT of any worker tool-set per AP-5/AP-14), or remove the entry point. Do not
  leave it dead.
- **A5 Headless robustness.** `_phase2_full_brain` / `build_default_brain` must work
  when no UI server is running (shared registry is `None`).
- **A6 Test Hub endpoint (interface contract below).** Add `POST /api/clis/test-run`
  to `jarvis/ui/web/cli_routes.py`.
- **A7 Tests.** Unit (spec validation, all 10 prober parse strategies, CliTool
  binary-guard + truncation, registry `_is_usable`, risk-pattern prefixing),
  integration (loader expand with shared registry, `cli-tools ∈ ROUTER_TOOLS`,
  live-reload re-expand), plus `scripts/probe_cli_e2e.py` real-CLI smoke.
- **A8 Live E2E.** Provide a probe the orchestrator runs to drive real, authed CLIs
  (gcloud, gh, docker) through the NL→tool→command→result loop.

### Frontend (Agent B — "Test Hub")

- **B1** New nav section "CLI Test Hub" (`CliTestHubView.tsx`) registered in the
  desktop app navigation.
- **B2** UI: list connected CLIs (reuse `useClisList`); a natural-language prompt box;
  a Run button calling `POST /api/clis/test-run`; a result panel showing the chosen
  tool, the exact command (monospace), risk tier (severity-coded), exit code,
  stdout/stderr, duration, and Jarvis's summary.
- **B3** Show the safety tier of the resolved command (safe/monitor/ask/block).
- **B4** Logic review + hardening of the existing `ClisView` (the user explicitly wants
  bugs/logic issues found and fixed).
- **B5** Frontend tests (vitest) for the new view + hooks.
- **B6** Brand: charcoal `#0e0d0c` + gold `#e7c46e`, severity-coded 3px strokes,
  anti-generic (per user brand guidelines).

### Interface contract (lets A and B run in parallel)

`POST /api/clis/test-run`

Request:
```json
{ "instruction": "string (natural language)", "cli_hint": "optional cli name e.g. gcloud" }
```

Response (JSON):
```json
{
  "ok": true,
  "instruction": "list my google cloud projects",
  "tool_called": "cli_gcloud",
  "command": "gcloud projects list --format=json",
  "risk_tier": "safe",
  "exit_code": 0,
  "stdout": "…",
  "stderr": "",
  "duration_ms": 1234,
  "summary": "You have 3 Google Cloud projects: …",
  "error": null,
  "steps": [ { "tool": "cli_gcloud", "command": "…", "exit_code": 0 } ]
}
```

Existing `GET /api/clis` (already implemented) supplies the connected-CLI list.

### File ownership (no collisions)

- **Agent A:** `jarvis/clis/**`, `jarvis/brain/factory.py`, `jarvis/brain/manager.py`
  (only if needed), `jarvis/plugins/tool/spawn_cli_worker.py`, `pyproject.toml`,
  `jarvis/ui/web/cli_routes.py`, `jarvis/ui/web/server.py` (registry wiring only),
  `docs/adr/*`, `tests/**` (backend), `scripts/probe_cli_e2e.py`.
- **Agent B:** `jarvis/ui/web/frontend/src/**` only (+ vitest files). No Python edits.

## Acceptance criteria

1. With the desktop app running and a CLI connected, the production router brain
   exposes a `cli_<name>` tool and successfully runs a real command end-to-end.
2. Works for ≥3 different real CLIs (gcloud, gh, docker), not just gcloud.
3. Connecting/disconnecting a CLI in the UI updates the live brain's tool set without
   restart.
4. The CLI Test Hub section runs an NL instruction and renders the chosen tool, exact
   command, risk tier, exit code, output, and summary.
5. `pytest tests/` for new/changed backend tests is green; `npm run test` for new
   frontend tests is green; `ruff`/`mypy` clean on touched Python.
6. The dead `spawn-cli-worker` entry point is resolved (implemented or removed).
