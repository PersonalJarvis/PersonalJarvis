# .agents/ — tool-neutral twin of .claude/

This directory is the cross-tool mirror of the versioned agent knowledge in
`.claude/`: the `agents/`, `commands/` and `skills/` subtrees are kept in sync
pair-for-pair (see CLAUDE.md / AGENTS.md §0, the binding mirror rule).

- **Audience:** every coding agent working in this repo — Codex, Gemini CLI,
  Claude Code, or anything else. Nothing in here is Claude-only; read the
  subagent definitions, command templates and skills as generally applicable.
- **Canonical side:** `.claude/` (tie-breaker on pre-existing drift), but edits
  on either side propagate to the other — including deletions.
- **Sync engine:** `scripts/ci/sync_agents_dir.py`, run from the Claude Code
  `PostToolUse` hook (live), `.githooks/pre-commit --stage` (hard guarantee on
  every commit), and `--check` for CI/manual verification.
- **Privacy:** gitignored entries (e.g. `skills/security-github/`, a local-only
  maintainer tool) are excluded from the mirror on both sides and must never be
  committed.

Do not hand-copy files between the two trees; edit one side and let the sync
engine (or the pre-commit hook) do the rest.
