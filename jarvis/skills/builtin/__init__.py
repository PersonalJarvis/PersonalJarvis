"""Built-in Skills für Jarvis.

Jedes Unterverzeichnis enthält genau eine `SKILL.md` mit YAML-Frontmatter
(siehe `jarvis.skills.schema.SkillFrontmatter`). Diese Skills werden beim
First-Run von `jarvis.skills.bootstrap.ensure_user_skills_dir()` in
`user_skills_dir()` kopiert (Windows: `%LOCALAPPDATA%\Jarvis\skills`) —
von dort aus liest die `SkillRegistry` und watcht via watchdog.

Liste der mitgelieferten Skills:
  - morning-routine   Kalender / Mail / Wetter Morgen-Briefing
  - deep-work-mode    DND + Fokus-Timer per Hotkey/Voice
  - memory-save       "merk dir ..." in Memory-MCP speichern
  - skill-creator     Meta-Skill zum Bauen weiterer Skills
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
    "plugin-google_drive",
    "plugin-vercel",
)

BUILTIN_SKILL_NAMES: tuple[str, ...] = (
    "morning-routine",
    "deep-work-mode",
    "memory-save",
    "skill-creator",
) + _PLUGIN_PAIRED_SKILLS


def builtin_skill_path(name: str) -> Path:
    """Gibt den Pfad zur SKILL.md eines Built-in-Skills zurück."""
    return BUILTIN_SKILLS_DIR / name / "SKILL.md"


__all__ = ["BUILTIN_SKILLS_DIR", "BUILTIN_SKILL_NAMES", "builtin_skill_path"]
