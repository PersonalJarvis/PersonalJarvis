---
title: "ADR-0026: Native Windows Codex Worker Sandbox"
slug: adr-0026-native-windows-codex-worker-sandbox
diataxis: adr
status: active
owner: project-maintainers
last_reviewed: 2026-07-13
phase: 6
audience: developer
---

# ADR-0026 — Native Windows Codex Worker Sandbox

**Status:** Accepted (2026-07-13)
**Phase:** 6 — Self-Healing Jarvis-Agents Orchestrator
**Reference:** ADR-0009, ADR-0025, AP-7, AP-10, AP-20, AP-23

## Context

`CodexDirectWorker` deliberately passes `--ignore-user-config` so a mission
cannot inherit arbitrary user plugins, MCP servers, or execution policy. On
native Windows this also removes the user's `[windows].sandbox` selection.
With Codex CLI 0.144.3, the resulting partial sandbox allowed reads but made
the file-change tool report the mission worktree as read-only. Codex then
emitted `turn.completed`, and the worker translated that transport-level event
into mission success even though the requested file did not exist.

The native Windows file-change path also remained unreliable after selecting
the fallback sandbox, while PowerShell commands inside the same ACL-bounded
worktree could write successfully. Windows PowerShell 5.1 adds a UTF-8 byte
order mark for common text-writing commands, which can corrupt TOML and other
strict text formats.

## Decision

Mission workers continue to ignore machine-global Codex configuration. On
native Windows, Jarvis explicitly selects Codex's `unelevated` sandbox while
retaining `workspace-write` and `approval_policy=never`. The worker sets the
mission worktree as the primary working directory and does not use full-access
or sandbox-bypass mode.

The Windows worker prompt carries a bounded recovery instruction: when Codex's
file-change tool reports the worktree as read-only, it may use a PowerShell
command inside the current worktree. Text writes must use BOM-free UTF-8, and
the worker must never write outside the worktree.

`turn.completed` remains a transport event, not sufficient proof of task
success. A sandbox write rejection with no later successful file or command
tool event is translated into an error result. Failed tool events do not count
as delivered work and cannot suppress cross-family recovery.

POSIX workers retain the normal `workspace-write` sandbox and receive no
Windows-specific prompt guidance.

## Consequences

- A fresh Windows install does not require administrator-approved sandbox
  setup before Codex mission workers can create artifacts.
- Native Windows workers remain bounded by the mission worktree and ACL-based
  sandbox rather than receiving full filesystem access.
- BOM-free UTF-8 is explicit on the PowerShell recovery path.
- A model turn that merely describes a rejected write can no longer be marked
  successful.
- The recovery path depends on Codex emitting command events accurately; the
  worker therefore keeps regression tests for failed patch, successful shell
  recovery, and false-success classification.

## Alternatives considered

- **Load the user's full Codex config:** rejected because it can import
  unrelated MCP servers, plugins, and policies into an unattended worker.
- **Use `danger-full-access` or bypass the sandbox:** rejected because a git
  worktree is not an operating-system security boundary.
- **Require the elevated Windows sandbox:** rejected as the universal default
  because it needs administrator-approved machine setup and can be blocked by
  enterprise policy.
- **Accept `turn.completed` as success:** rejected because it describes model
  turn completion, not successful side effects.
- **Allow PowerShell's default UTF-8 encoding:** rejected because the emitted
  byte-order mark can break strict configuration and protocol files.

## Follow-up items

- Re-probe the file-change tool when upgrading Codex CLI and remove the prompt
  recovery only after a real native Windows write smoke test passes.
- Keep Phase 6 worker smoke coverage for a real file written through the
  Claude-to-Codex cross-family fallback.
- Keep Linux/headless tests on the standard `workspace-write` path.
