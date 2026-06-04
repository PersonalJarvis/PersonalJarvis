"""Render the AVAILABLE SKILLS markdown section for the BrainManager system prompt.

Skills-Brain-Integration (Track B): the BrainManager renders an
``## AVAILABLE SKILLS`` section listing every active skill known to the
``SkillRegistry``, so the LLM can pick a matching skill via the
``run_skill`` tool instead of falling back to ``spawn_sub_jarvis`` for
something the User has already installed.

Design constraints:

* Plain Markdown — the existing prompt mixes English structural headings
  with German body text (see ``SUB_JARVIS_SYSTEM_PROMPT``); this module
  follows that established convention.
* No imports from ``jarvis.brain.*`` — the renderer is consumed by
  BrainManager but must not import from it (circular-import guard).
* Tolerant of broken skills — a registry entry with ``frontmatter is
  None`` (e.g. parse error parked as DRAFT) is silently skipped instead
  of crashing the prompt build.
"""
from __future__ import annotations

from jarvis.skills.registry import SkillRegistry


def render_available_skills_section(
    registry: SkillRegistry,
    *,
    max_skills: int = 20,
) -> str | None:
    """Render the AVAILABLE SKILLS markdown section for the system prompt.

    Returns ``None`` when no active skills exist (callers should skip
    appending an empty section).

    Args:
        registry: The live ``SkillRegistry``. Only ACTIVE/VALIDATED skills
            are considered (``registry.list_active()``).
        max_skills: Hard cap on the number of bullets rendered. Skills
            beyond the cap are folded into a single ``… und N weitere``
            tail bullet so the prompt does not grow unbounded.
    """
    active = registry.list_active()
    if not active:
        return None

    bullets: list[str] = []
    skipped_no_frontmatter = 0
    for skill in active:
        fm = skill.frontmatter
        if fm is None:
            # Broken/draft-with-no-frontmatter — silently skip.
            skipped_no_frontmatter += 1
            continue
        description = (fm.description or "").strip()
        if not description:
            description = "(no description)"
        bullets.append(f"- `{skill.name}` — {description}")

    if not bullets:
        return None

    overflow = max(0, len(bullets) - max_skills)
    if overflow:
        bullets = bullets[:max_skills]
        bullets.append(f"- … und {overflow} weitere")

    header = "## AVAILABLE SKILLS\n"
    intro = (
        "Du kannst die folgenden vom User installierten Skills via "
        "`run_skill`-Tool aufrufen. Wähle einen Skill nur dann, wenn "
        "die User-Anfrage klar zur Skill-Description passt:\n"
    )
    body = "\n".join(bullets)
    outro = (
        "\n\nBei Mehrdeutigkeit: kurz nachfragen statt raten. "
        "Drafts/Disabled-Skills werden vom Tool automatisch abgelehnt — "
        "versuch sie nicht zu callen."
    )
    return f"{header}\n{intro}\n{body}{outro}"
