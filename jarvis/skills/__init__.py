r"""Skill system: Markdown-based user extensions for Jarvis.

Skills are SKILL.md files with a YAML frontmatter + body. They are
loaded from the skill root directory (`user_skills_dir()`, typically
`%LOCALAPPDATA%\Jarvis\skills`), validated, held in the `SkillRegistry`,
and executed by the `SkillRunner`.

Unlike plugins (entry_points), skills are **files** — no
`pip install` needed, hot-reload via watchdog, voice/hotkey/cron triggers.
"""
from __future__ import annotations

from .deduplicator import find_duplicates, jaccard
from .lifecycle import LifecycleManager
from .loader import discover_skills, parse_skill
from .registry import SkillRegistry
from .runner import SkillRunner
from .schema import (
    Skill,
    SkillCompleted,
    SkillFailed,
    SkillFrontmatter,
    SkillLifecycleState,
    SkillResult,
    SkillRiskPolicy,
    SkillStarted,
    SkillStepExecuted,
    SkillTrigger,
)
from .trigger_matcher import TriggerMatcher
from .validator import ValidationReport, validate_skill

__all__ = [
    "Skill",
    "SkillFrontmatter",
    "SkillLifecycleState",
    "SkillRiskPolicy",
    "SkillTrigger",
    "SkillStarted",
    "SkillStepExecuted",
    "SkillCompleted",
    "SkillFailed",
    "SkillResult",
    "SkillRegistry",
    "SkillRunner",
    "TriggerMatcher",
    "LifecycleManager",
    "ValidationReport",
    "validate_skill",
    "parse_skill",
    "discover_skills",
    "find_duplicates",
    "jaccard",
]
