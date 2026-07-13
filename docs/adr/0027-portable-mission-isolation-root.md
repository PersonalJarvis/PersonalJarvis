---
title: "ADR-0027: Portable Mission Isolation Root"
slug: adr-0027-portable-mission-isolation-root
diataxis: adr
status: active
owner: project-maintainers
last_reviewed: 2026-07-13
phase: 6
audience: developer
---

# ADR-0027 — Portable Mission Isolation Root

**Status:** Accepted (2026-07-13)
**Phase:** 6 — Self-Healing Jarvis-Agents Orchestrator
**Reference:** ADR-0009, ADR-0025, ADR-0026, AP-10, AP-23

## Context

Desktop checkouts place mission worktrees beside the repository under
`<repo-parent>/jarvis-agent-outputs`. The same derivation is invalid for the
official non-root container: the application root is `/app`, its parent is
`/`, and only `/app/data` is writable. Mission bootstrap therefore raised
`PermissionError` while creating `/jarvis-agent-outputs`. The fast health
endpoint was already serving, which concealed the failed mission stack from a
superficial boot probe.

The distribution image also omitted the `git` executable. It could import the
mission modules but could not create the standalone repositories used by lean
Jarvis-Agent tasks. Its declared user home was `/home/jarvis`, but that
directory did not exist. Skill, document, CLI-tool, and board registries then
failed independently while trying to create their per-user state, shrinking
the tool surface even though the web server continued to answer health checks.

## Decision

Mission output-root precedence is:

1. `JARVIS_ISOLATION_ROOT`, when explicitly set;
2. `<JARVIS_DATA_DIR>/jarvis-agent-outputs` on portable/headless installs;
3. the existing checkout rule under `<repo-parent>`, including the legacy
   `sub-agents-outputs` read fallback.

The root resolver is shared by bootstrap and `WorktreeManager`, so callers do
not re-derive different paths. It returns a path but does not create it; the
existing bootstrap remains responsible for creation and error reporting.

The official runtime image installs `git`. A distribution image without host
checkout history can therefore run lean artifact tasks in an isolated
standalone repository. Tasks that require the Personal Jarvis source checkout
still require a real repository and must fail honestly when it is absent.

The container sets `HOME=/app/data/home` and creates that directory before
dropping privileges. Per-user skills, CLI metadata, documents, and related
registries therefore share the declared writable data volume rather than
trying to create an undeclared `/home/jarvis` tree.

## Consequences

- The mission stack boots under the non-root container user without writing to
  the filesystem root.
- Mission artifacts persist in the same writable data volume as the headless
  runtime state.
- Headless skill and CLI registries retain a writable per-user home and no
  longer disappear behind caught `PermissionError` exceptions.
- Desktop checkout layouts and legacy output discovery remain unchanged when
  neither environment override is present.
- The container image grows by the system `git` package and its runtime
  dependencies.
- Fast health remains useful, but headless verification must also inspect the
  mission stack or exercise workspace creation so a background bootstrap
  failure cannot be mistaken for full readiness.

## Alternatives considered

- **Make `/jarvis-agent-outputs` writable:** rejected because it adds a second
  mutable container path and bypasses the declared data volume.
- **Run the image as root:** rejected because it weakens the container security
  boundary to hide a path bug.
- **Disable missions in headless mode:** rejected because Jarvis-Agents are a
  core provider-agnostic capability, not a desktop-only feature.
- **Keep `git` optional in the official image:** rejected because lean
  workspaces cannot satisfy the isolation and diff-capture contract without it.

## Follow-up items

- Keep a non-root container smoke test that verifies the resolved output root,
  creates a lean workspace, and reaches the health endpoint.
- Keep checkout-mode tests for the repo-parent and legacy output paths.
- Surface an explicit capability error for source-dependent tasks when the
  installed runtime has no host repository history.
