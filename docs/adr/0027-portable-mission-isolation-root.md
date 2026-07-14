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
still require a real repository. Workspace classification is preserved across
the deterministic, no-brain, provider-error, and invalid-plan decomposition
paths; an unavailable decomposition model therefore cannot accidentally turn a
standalone task into a source task. Older model plans that omit `needs_repo`
are backfilled from deterministic source-affinity checks, while an explicit
model claim can never downgrade a clearly source-dependent prompt.

Before creating task scaffolding, the full-worktree path verifies that the
application root has a real `.git` marker and that `git rev-parse` resolves it
as the repository root. A copied, frozen, or container application tree raises
the distinct `source_checkout_unavailable` capability failure. It never falls
through to the read-only application tree and is not described as a broken ZIP
installation. The lean path deliberately skips this source probe and runs
entirely under the writable isolation root.

Boot cleanup uses the same distinction. When the application root has no
source checkout, it skips the nonexistent host worktree registry without a
warning and still age-sweeps leaked standalone run directories from the data
volume. A source-less runtime therefore does not accumulate abandoned lean
repositories merely because `git worktree list` is unavailable at `/app`.

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
- Repository-independent missions remain runnable when decomposition falls
  back because a planning provider is unavailable or returns invalid output.
- Source-dependent missions fail before worker startup with an explicit
  capability reason; no task directory or unisolated source edit is attempted.
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
- Keep decomposition tests that prove provider failures preserve standalone
  workspace classification and cannot downgrade a source task.
