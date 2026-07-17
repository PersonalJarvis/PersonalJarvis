"""Built-in skills for Jarvis.

Each subdirectory contains exactly one `SKILL.md` with YAML frontmatter
(see `jarvis.skills.schema.SkillFrontmatter`). On first run these skills are
copied into `user_skills_dir()` by
`jarvis.skills.bootstrap.ensure_user_skills_dir()` (Windows:
`%LOCALAPPDATA%\Jarvis\skills`) — the `SkillRegistry` reads from and watches
that location.

List of bundled skills:
  - morning-routine   calendar / mail / weather morning briefing
  - deep-work-mode    DND + focus timer via hotkey/voice
  - memory-save       "remember that ..." saved to memory-MCP
  - skill-creator     meta-skill for building further skills
  - plugin-<id>       Paired skills for marketplace plugins (2026-06-07): each
                      teaches the router which voice/chat intents reach the
                      connected plugin's tools (gmail, github, stripe, ...).
"""
from __future__ import annotations

from pathlib import Path

BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent

# Plugin<->skill pairing (2026-06-07): these must be listed here, or
# ensure_user_skills_dir() never copies them into the user skills dir and the
# SkillRegistry never loads them — the paired capabilities would never register
# and the connected plugins would stay unreachable in a real boot.
_PLUGIN_PAIRED_SKILLS: tuple[str, ...] = (
    "plugin-gmail",
    "plugin-github",
    "plugin-stripe",
    "plugin-notion",
    "plugin-slack",
    "plugin-linear",
    "plugin-discord",
    "plugin-asana",
    "plugin-supabase",
    "plugin-cloudflare",
    "plugin-google_calendar",
    "plugin-google_drive",
    "plugin-vercel",
)

BUILTIN_SKILL_NAMES: tuple[str, ...] = (
    "morning-routine",
    "deep-work-mode",
    "memory-save",
    "skill-creator",
    # control-api (2026-06-08): documentation skill that teaches a local coding
    # agent (Codex CLI, Claude Code) how to drive the Jarvis Control API to
    # change settings/providers/language. category=meta, no voice triggers.
    "control-api",
    # cli-gcloud (2026-06-17): guidance skill teaching the brain to drive Google
    # Cloud via the cli_gcloud tool instead of the browser console. category=meta,
    # no voice triggers (a trigger would make the router pick run_skill over the
    # cli_gcloud tool — see control-api). Gated by requires_tools=[cli_gcloud].
    "cli-gcloud",
) + _PLUGIN_PAIRED_SKILLS


def builtin_skill_path(name: str) -> Path:
    """Returns the path to a built-in skill's SKILL.md."""
    return BUILTIN_SKILLS_DIR / name / "SKILL.md"


__all__ = ["BUILTIN_SKILLS_DIR", "BUILTIN_SKILL_NAMES", "builtin_skill_path"]
