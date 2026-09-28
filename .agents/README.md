# .agents/ — shared source of agent knowledge

This directory is the source for the versioned agent definitions and skills.
`AGENTS.md` is the source for repository-wide instructions.

- **Audience:** every coding agent working in this repo — Codex, Gemini CLI,
  Claude Code, or anything else. Nothing in here is Claude-only; read the
  subagent definitions, command templates and skills as generally applicable.
- **Compatibility copies:** `scripts/ci/sync_agents_dir.py` copies `agents/`,
  `commands/`, and `skills/` into `.claude/`; `sync_codex_agents.py` projects
  `agents/*.md` into `.codex/agents/*.toml`. Both are checked in CI and run
  before commits. Claude Code and Codex still need these tool-specific paths
  to discover project subagents; Claude Code also needs `.claude/skills/`.
- **Privacy:** gitignored entries (e.g. `skills/security-github/`, a local-only
  maintainer tool) are excluded from the mirror on both sides and must never be
  committed.

Edit this tree only. Run the sync scripts after editing or let the pre-commit
hook create the compatibility copies.
