"""Skill-System: Markdown-basierte User-Erweiterungen für Jarvis.

Skills sind SKILL.md-Dateien mit YAML-Frontmatter + Body. Sie werden aus dem
Skill-Root-Verzeichnis (`user_skills_dir()`, i.d.R. `%LOCALAPPDATA%\Jarvis\skills`)
geladen, validiert, im `SkillRegistry` gehalten und vom `SkillRunner` ausgeführt.

Im Gegensatz zu Plugins (entry_points) sind Skills **Files** — kein
`pip install` nötig, Hot-Reload via watchdog, Voice-/Hotkey-/Cron-Trigger.
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
