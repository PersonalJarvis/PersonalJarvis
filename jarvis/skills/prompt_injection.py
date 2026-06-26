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

# Per-entry cap on the rendered description+when_to_use text (mirrors the
# 1536-char listing cap in Claude Code's skill listing, AD-S2).
_PER_ENTRY_CHAR_CAP = 1536


def _skill_mtime(skill: object) -> float:
    """Last-modified time used for budget eviction; 0.0 when unknown."""
    try:
        return skill.path.stat().st_mtime  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        try:
            return float(getattr(skill, "mtime", 0.0))
        except Exception:  # noqa: BLE001
            return 0.0


def render_available_skills_section(
    registry: SkillRegistry,
    *,
    max_skills: int = 20,
    total_char_budget: int = 8000,
) -> str | None:
    """Render the AVAILABLE SKILLS markdown section for the system prompt.

    Returns ``None`` when no active skills exist (callers should skip
    appending an empty section).

    Args:
        registry: The live ``SkillRegistry``. Only ACTIVE/VALIDATED skills
            are considered (``registry.list_active()``).
        max_skills: Hard cap on the number of bullets rendered. Skills
            beyond the cap are folded into a single ``… and N more``
            tail bullet so the prompt does not grow unbounded.
        total_char_budget: Overall character budget for the bullet block
            (AD-S2 L1, mirrors Claude Code's listing budget). When exceeded,
            the least-recently-modified skills are evicted first — names of
            fresh skills stay visible, stale ones fold into the tail bullet.
    """
    active = registry.list_active()
    if not active:
        return None

    entries: list[tuple[str, float]] = []
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
        # AD-S2 L1: when_to_use is appended to the description (Anthropic
        # Agent Skills listing convention) and the combined text is capped
        # per entry so one verbose skill cannot blow the prompt budget.
        when_to_use = (getattr(fm, "when_to_use", None) or "").strip()
        if when_to_use:
            description = f"{description} {when_to_use}"
        if len(description) > _PER_ENTRY_CHAR_CAP:
            description = description[: _PER_ENTRY_CHAR_CAP - 1] + "…"
        entries.append((f"- `{skill.name}` — {description}", _skill_mtime(skill)))

    if not entries:
        return None

    overflow = max(0, len(entries) - max_skills)
    if overflow:
        entries = entries[:max_skills]

    # Total budget eviction (AD-S2): drop least-recently-modified first
    # while preserving the display order of the survivors.
    def _block_len(items: list[tuple[str, float]]) -> int:
        return sum(len(b) + 1 for b, _ in items)

    while len(entries) > 1 and _block_len(entries) > total_char_budget:
        oldest_idx = min(range(len(entries)), key=lambda i: entries[i][1])
        entries.pop(oldest_idx)
        overflow += 1

    bullets = [b for b, _ in entries]
    if overflow:
        bullets.append(f"- … and {overflow} more")

    header = "## AVAILABLE SKILLS\n"
    intro = (
        "These are the user's installed skills — saved preferences for HOW "
        "recurring tasks should be done. BEFORE you answer from scratch or "
        "spawn a worker, check this list. If the request plausibly matches a "
        "skill's description / when-to-use — even loosely, even in new wording "
        "that is not the exact trigger phrase — you MUST call the `run-skill` "
        "tool with that skill's name FIRST, then follow the returned "
        "instructions with your other tools. A matched skill always beats "
        "answering on your own and always beats spawning a worker; that is "
        "exactly why the user installed it. When unsure whether a skill "
        "applies, prefer calling it — a wrong skill is cheap to skip, a missed "
        "skill defeats its purpose. Do NOT, however, fire a skill for a plain "
        "question that merely mentions its topic (\"what is Gmail?\" is not the "
        "gmail skill; \"read my new mail\" is):\n"
    )
    body = "\n".join(bullets)
    outro = (
        "\n\nIf several skills could match, pick the most specific one. "
        "Draft/disabled skills are rejected by the tool automatically."
    )
    return f"{header}\n{intro}\n{body}{outro}"
