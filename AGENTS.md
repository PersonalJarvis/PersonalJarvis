# AGENTS.md

Contract for **any** coding agent (Claude Code, Codex, Cursor, GitHub Copilot,
Gemini CLI, …) working in this repository. Claude Code reads `CLAUDE.md`; this
file mirrors the load-bearing rules for every other agent. On any conflict,
`CLAUDE.md` is the fuller source of truth.

## Repository identity

This repository is **`personal-jarvis/PersonalJarvis`** — the clean,
public-bound distribution build of Personal Jarvis, and the single repository we
develop in. The historical repo `personal-jarvis/personal-jarvis` is archived; do
not push there. **This repo will become public — treat every commit as if it were
already public.**

## Privacy contract (BINDING — never violate)

The OpenClaw model: ship CODE, never the operator's data. A user clones this repo
and supplies their OWN keys and config via `python -m jarvis --wizard`.

**NEVER commit, stage, or push:**

- **Secrets:** API keys, tokens, `.env` (only `.env.example` ships),
  `apikeys-snapshot.md`, `*.key`, private `*.pem`.
- **Real config:** `jarvis.toml` (only `jarvis.toml.example` with `CHANGEME`
  placeholders ships); same for `mcp.json` → `mcp.json.example`.
- **Memory / knowledge data:** `wiki/obsidian-vault/sessions/**`,
  `wiki/obsidian-vault/_archive/**`, the operator's personal notes.
- **Runtime / user state:** `data/**`, `*.db` / `*.sqlite`, chat history,
  profile, avatar.
- **Personal identifiers:** the maintainer's real name, email, or home path. Use
  neutral placeholders — `the maintainer`, `you@example.com`, `<your-home>`.

This is enforced by `.gitignore` and the CI guard
`tests/unit/test_no_personal_state_tracked.py`. Do not weaken either.

## Output language

Every committed artifact is **English** — code, comments, docstrings, Markdown,
commit messages, PR titles/bodies, test names. (The assistant's spoken/chat reply
to the user may be in the user's language; committed files may not.) See
`CLAUDE.md` → Output Language Policy.

## Before you push

1. `git diff --cached --stat` — confirm no personal/secret paths are staged.
2. `pytest tests/unit/test_no_personal_state_tracked.py` — must pass.
3. Commit with **explicit paths** (never a blind `git add .` in a shared tree).

Full conventions — architecture, plugin system, anti-patterns, testing — live in
`CLAUDE.md`. Read it before non-trivial work.
