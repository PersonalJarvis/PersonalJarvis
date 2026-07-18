# Subagent Index

**Purpose:** an overview of all subagents in `.claude/agents/`, their role, domain, and a decision tree for when each one is spawned. This file is the central reference point for the main agent (me, in the Claude Code CLI) and for the user.

---

## 1. Role × Domain matrix

Every subagent is primarily classified along two axes: **role** (what it does) and **domain** (what it has knowledge for).

### Five roles

| Role | What it does | Default tool surface |
|---|---|---|
| **Researcher** | Read-only, answers where/how questions with cited evidence. Writes NO code. | Read, Grep, Glob, Bash (read-only) |
| **Worker** | Writes code, modifies files, builds features in the worktree | Read, Write, Edit, Bash, Grep, Glob |
| **Reviewer** | Evaluates an existing diff/code/plan, renders a PASS/FAIL verdict | Read, Grep, Glob |
| **Verifier** | Checks acceptance criteria against the implementation, with `file:line` evidence | Read, Grep, Glob, Bash (test-run) |
| **Test-Runner** | pytest-specialized, compact failure reports without PASS spam | Bash, Read, Grep |

### Three domain classes

- **generic** — phase-agnostic, broadly applicable
- **phase-specific** — bound to a plan/phase/doc (P0-5, P6, P7, awareness-A0-A5, jarvis-agents-bridge)
- **specialist** — domain-expert (Win32, Audio, Critic-Design)

---

## 2. Current occupancy of the matrix

| Role | Generic | Phase-specific | Specialist |
|---|---|---|---|
| Researcher | — | `jarvis-architect-explorer` (P0–7) | — |
| Worker | — | `jarvis-worker` (P6, more generically usable), `jarvis-agents-bridge-builder` (jarvis-agent) | `win32-specialist` |
| Reviewer | `code-reviewer`, `docs-privacy-reviewer` | `jarvis-reviewer` (P6 Adversarial), `jarvis-agents-bridge-reviewer` (jarvis-agent), `phase7-selfmod-auditor` (P7) | `jarvis-critic-design-reviewer` (P6 Critic-Loop) |
| Verifier | `plan-verifier` (Awareness + general) | `awareness-a3-a5-verifier` (A3-A5) | — |
| Test-Runner | `test-runner` | `jarvis-test-runner` (P6) | — |

---

## 3. Decision tree — when to spawn which subagent

### When implementing

| Task | Subagent | Justification |
|---|---|---|
| Understand a new Phase-0-to-5 component before coding | `jarvis-architect-explorer` | Phase 0-5 architecture knowledge, read-only |
| Extend a Phase-6 mission/critic/worktree | `jarvis-worker` | Heavy worker, knows the Phase-6 internals |
| Implement Jarvis-Agent-bridge Wave 2/3 | `jarvis-agents-bridge-builder` | Knows the spike findings, bridge doc §6 |
| Touch Win32/UIA/DPI/SetWinEventHook | `win32-specialist` | Lifecycle obligations, lazy-import patterns |
| Write generic code (no phase knowledge needed) | directly in the main agent or `general-purpose` | Subagent overhead not worth it |

### When reviewing

| Task | Subagent | Justification |
|---|---|---|
| Diff after every substantial change | `code-reviewer` | Senior review against AGENTS.md |
| New/changed file under `docs/` (privacy pass before it could ship) | `docs-privacy-reviewer` | Reads the doc for the maintainer's name/email/handle, personal paths, machine ids, private life details, and real secrets — the semantic half of the docs privacy gate (`scripts/ci/docs_privacy_scan.py` is the deterministic half, run by a PostToolUse hook) |
| Adversarially check jarvis-worker output (build phase) | `jarvis-reviewer` | JSON verdict, during the build before handoff to the user. NOT for Jarvis-Agent production output (use the Phase-6 critic for that) |
| Phase-6 Critic-Loop design (prompts, verdict schema) | `jarvis-critic-design-reviewer` | Sycophancy risks, reflexion pattern |
| Jarvis-Agent-bridge code against AP-OC1..OC13 | `jarvis-agents-bridge-reviewer` | Bridge doc §5 anti-patterns |
| Phase-7 Self-Mod code against AP-SM1..SM14 | `phase7-selfmod-auditor` | Allowlist + pre-validate + confirmation |

### When verifying

| Task | Subagent | Justification |
|---|---|---|
| Acceptance criteria of an Awareness phase A0-A2 | `plan-verifier` | Awareness-plan-specific |
| Acceptance criteria A3-A5 (FTS5, Working-Set, Probes) | `awareness-a3-a5-verifier` | A3-A5-specific |
| Acceptance criteria of any phase with a plan doc | `plan-verifier` | Generic enough for any phase with an AC table |

### When testing

| Task | Subagent | Justification |
|---|---|---|
| pytest against Phase 0-5 or Awareness | `test-runner` | Generic, Haiku, compact |
| pytest against Phase 6 (`tests/missions/`) | `jarvis-test-runner` | Phase-6 path conventions, JSON body output |
| pytest against the Jarvis-Agent bridge | `test-runner` | Generic is enough, the Jarvis-Agent paths are not Phase-6 |

---

## 4. Frontmatter standard

All subagent files (`*.md` except `INDEX.md`) carry the following frontmatter:

```yaml
---
name: <agent-name>                    # Required — Claude Code parses this
description: <description>            # Required — quoted in the spawn prompt
tools: <comma-list>                   # Required — tool allowlist
model: <haiku|sonnet|opus>            # Required
role: <researcher|worker|reviewer|verifier|test-runner>   # Doc field (not a Claude Code standard)
domain: <generic|phase-N|specialist>  # Doc field
phase: <optional, e.g. "0-5", "6", "7", "awareness-A3-A5", "jarvis-agents-bridge">
must_read:                            # Doc field, list of mandatory reading
  - AGENTS.md
  - <more>
when_to_use: <decision-tree entry>    # Doc field, one sentence
---
```

**Important:** the `role`/`domain`/`phase`/`must_read`/`when_to_use` fields are not parsed by Claude Code, but they are consumable as YAML frontmatter and serve as documentation for the subagents themselves and for this INDEX.md.

---

## 5. Tool-surface convention

**Per-role default** (see the table in §1) is the upper bound. Individual subagents may reduce downward when less suffices. Never upward — a reviewer with write rights is a violation of least privilege.

| Role | Allowed tools | Never |
|---|---|---|
| Researcher | Read, Grep, Glob, Bash | Write, Edit |
| Worker | Read, Write, Edit, Bash, Grep, Glob | — (full access) |
| Reviewer | Read, Grep, Glob | Write, Edit, Bash |
| Verifier | Read, Grep, Glob, Bash | Write, Edit |
| Test-Runner | Bash, Read, Grep | Write, Edit, Glob (rarely needed) |

---

## 6. Maintenance

- **New subagent:** create it in `.claude/agents/<name>.md` with the complete frontmatter, then extend this INDEX.md in the appropriate slot of the matrix.
- **Subagent refactor:** frontmatter update + INDEX-entry update. If the role/domain changes: update the decision tree in §3.
- **Retiring a subagent:** delete the file + remove the INDEX line. Never empty out or comment out a file — the git history is the documentation.

---

**Last update:** 2026-05-06 (initial creation as part of the `.claude/` restructuring)
