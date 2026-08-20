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

from collections.abc import Sequence

from jarvis.skills.registry import SkillRegistry

# Per-entry cap on the rendered description+when_to_use text (mirrors the
# 1536-char listing cap in Claude Code's skill listing, AD-S2).
_PER_ENTRY_CHAR_CAP = 1536

# How many folded skill NAMES the overflow tail enumerates. The tail exists so
# a folded skill stays *callable* — run-skill resolves by exact name, and a
# bare "… and 5 more" hides exactly the name the model would need. Live
# forensic 2026-08-12: with 25 active skills and the old cap of 20, the five
# alphabetically-last skills (including `skill-creator`) were invisible to the
# model, and model-initiated run-skill calls measured ZERO over 14 days.
_OVERFLOW_TAIL_NAME_CAP = 15


def _skill_mtime(skill: object) -> float:
    """Last-modified time used for budget eviction; 0.0 when unknown."""
    try:
        return skill.path.stat().st_mtime  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        try:
            return float(getattr(skill, "mtime", 0.0))
        except Exception:  # noqa: BLE001
            return 0.0


def _is_builtin(skill: object) -> bool:
    """True when this skill ships with Jarvis (a bootstrap copy of a builtin).

    User-authored skills are the ones the user deliberately added, so when the
    listing must shrink, shipped defaults fold before the user's own work.
    Fails toward "user" on purpose: an import fault must never demote a user
    skill to fold-first status.
    """
    try:
        from jarvis.skills.builtin import BUILTIN_SKILL_NAMES

        return str(getattr(skill, "name", "")) in BUILTIN_SKILL_NAMES
    except Exception:  # noqa: BLE001 — import fault must not demote a user skill
        return False


def render_available_skills_section(
    registry: SkillRegistry,
    *,
    max_skills: int = 48,
    total_char_budget: int = 8000,
) -> str | None:
    """Render the AVAILABLE SKILLS markdown section for the system prompt.

    Returns ``None`` when no active skills exist (callers should skip
    appending an empty section).

    Ordering and folding policy (2026-08-12, "skills never fire" rework):
    user-authored skills render BEFORE builtins, and when the listing must
    shrink (cap or char budget) builtins fold first. The default cap covers
    the full realistic install (25 builtins + user growth) because a skill
    missing from this list can effectively never be chosen by the model —
    the measured model-initiated run-skill rate with folded skills was zero.

    Args:
        registry: The live ``SkillRegistry``. Only ACTIVE/VALIDATED skills
            are considered (``registry.list_active()``).
        max_skills: Hard cap on the number of bullets rendered. Skills
            beyond the cap fold into a tail bullet that still NAMES them
            (run-skill resolves by exact name), so the prompt stays bounded
            without making any skill uncallable.
        total_char_budget: Overall character budget for the bullet block
            (AD-S2 L1, mirrors Claude Code's listing budget). When exceeded,
            builtins are evicted before user skills, least-recently-modified
            first within each group.
    """
    active = registry.list_active()
    if not active:
        return None

    # (bullet, mtime, is_builtin, name) per renderable skill.
    entries: list[tuple[str, float, bool, str]] = []
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
        name = str(skill.name)
        entries.append(
            (
                f"- `{name}` — {description}",
                _skill_mtime(skill),
                _is_builtin(skill),
                name,
            )
        )

    if not entries:
        return None

    # User-authored skills first (stable within each group): the user added
    # them deliberately, and position in a long listing is attention.
    entries.sort(key=lambda e: e[2])

    folded_names: list[str] = []
    overflow = max(0, len(entries) - max_skills)
    if overflow:
        folded_names.extend(name for _, _, _, name in entries[max_skills:])
        entries = entries[:max_skills]

    # Total budget eviction (AD-S2): builtins before user skills, oldest
    # first within each group, preserving display order of the survivors.
    def _block_len(items: list[tuple[str, float, bool, str]]) -> int:
        return sum(len(b) + 1 for b, _, _, _ in items)

    while len(entries) > 1 and _block_len(entries) > total_char_budget:
        evict_idx = min(
            range(len(entries)),
            # is_builtin=True sorts as 0 → builtins evict first.
            key=lambda i: (not entries[i][2], entries[i][1]),
        )
        folded_names.append(entries[evict_idx][3])
        entries.pop(evict_idx)
        overflow += 1

    bullets = [b for b, _, _, _ in entries]
    if overflow:
        named = ", ".join(f"`{n}`" for n in folded_names[:_OVERFLOW_TAIL_NAME_CAP])
        if len(folded_names) > _OVERFLOW_TAIL_NAME_CAP:
            named += ", …"
        # The names keep folded skills callable: run-skill takes the exact name.
        bullets.append(
            f"- … and {overflow} more (no description shown; still callable "
            f"via `run-skill` by exact name): {named}"
            if named
            else f"- … and {overflow} more"
        )

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
        "Draft/disabled skills are rejected by the tool automatically. "
        "When the user asks you to CREATE a new skill "
        "(\"erstell mir einen Skill, der …\", "  # i18n-allow: quoted phrase
        "\"create a skill that …\"), call the `create-skill` tool with their "
        "full description — never `run-skill`, and never a worker."
    )
    return f"{header}\n{intro}\n{body}{outro}"


def render_skill_candidate_hint(entries: Sequence[tuple[str, str]]) -> str:
    """The "these scored, you decide" block for a turn nothing captured.

    One home for this wording on purpose. The brain path has shipped it since
    the 2026-08-12 rework and the realtime path had nothing at all, so a NARROW
    match — the scorer found the right skill but not strongly enough to take
    the turn over — was simply discarded in a live call. Two renderers would
    drift, and a hint that says something different depending on which engine
    is running is worse than one that is merely imperfect.

    ``entries`` is ``(skill_name, blurb)``, best first and already truncated by
    the caller. Returns ``""`` for an empty list so callers can append blindly.

    The decision explicitly stays with the model: a wrong candidate is ignored,
    never executed. That is what makes this block safe to show on a merely
    plausible match, where an automatic capture would not be.
    """
    lines = [f"- `{name}` — {blurb}" if blurb else f"- `{name}`" for name, blurb in entries]
    if not lines:
        return ""
    return (
        "[Skill candidates] The user's request scored against these "
        "installed skills. If one genuinely fits, call the `run-skill` tool "
        "with that name FIRST and follow the returned instructions. If none "
        "fits, ignore this block entirely and answer normally — these are "
        "ranked suggestions, not a verdict.\n" + "\n".join(lines)
    )


#: Per-entry budget for the RICH realtime tier: description + when_to_use.
#: ``when_to_use`` is the field that says when a skill applies, in the words
#: people actually use — it is what lets a model match "wie sieht mein Tag aus"
#: to a skill triggered on "Morgenroutine". The first realtime roster shipped
#: description-only at 70 chars and cut exactly that text off, which left the
#: live model with a topic label instead of a matching rule.
_REALTIME_RICH_CHARS = 300

#: Per-entry budget for the SHORT tier — description only, no matching rule.
_REALTIME_DESC_CHARS = 70


def render_realtime_skills_directive(
    registry: SkillRegistry,
    *,
    compact: bool = False,
    budget_chars: int = 9000,
) -> str:
    """Render the installed-skill roster for a live voice session.

    The realtime engine was skill-blind in a way no listing test caught: the
    session instructions carried the ``run-skill`` tool but never one skill
    NAME, so the model had to invent the argument from the user's words. It
    invented "Morning Routine"; the registry key was ``morning-routine``; the
    turn died on ``Unknown skill`` (live 2026-08-20). Same shape as the
    workspace roster next to it — the model cannot route a name it has never
    heard of — and it binds every realtime transport alike, because they all
    share one instruction builder.

    Degrades in three steps, richest first, and never below the names:

    1. name + description + ``when_to_use`` — the matching rule, so the model
       can recognise a paraphrase the author's trigger regex never predicted.
       This is the tier that makes recognition work like Claude Code's;
    2. name + short description — the topic, without the matching rule;
    3. names only.

    Names outrank prose at every step: a described skill that is missing from
    the list is uncallable, while a bare name is still enough for ``run-skill``
    to resolve. ``compact`` (small self-hosted brains) goes straight to step 3.

    Returns ``""`` when no skill is invocable, so the caller can drop the
    block from the instruction assembly entirely.
    """
    try:
        active = registry.list_active()
    except Exception:  # noqa: BLE001 — a roster fault must never break a call
        return ""
    # (name, rich blurb, short blurb, is_builtin)
    entries: list[tuple[str, str, str, bool]] = []
    for skill in active:
        fm = getattr(skill, "frontmatter", None)
        if fm is None:
            continue
        name = str(skill.name)
        if not name:
            continue
        desc = " ".join((getattr(fm, "description", "") or "").split())
        when = " ".join((getattr(fm, "when_to_use", "") or "").split())
        rich = f"{desc} {when}".strip()
        if len(rich) > _REALTIME_RICH_CHARS:
            rich = rich[: _REALTIME_RICH_CHARS - 1].rstrip() + "…"
        short = desc
        if len(short) > _REALTIME_DESC_CHARS:
            short = short[: _REALTIME_DESC_CHARS - 1].rstrip() + "…"
        entries.append((name, rich, short, _is_builtin(skill)))
    if not entries:
        return ""

    # User-authored skills first — same attention argument as the brain listing.
    entries.sort(key=lambda e: (e[3], e[0]))

    header = "[Installed skills — the user's saved way of doing these tasks]\n"
    rule = (
        "\nEach line says what a skill is for and when it applies. Match on "
        "MEANING, not on wording: the user will not repeat these words, so a "
        "request that asks for the same outcome is a match even when it shares "
        "no vocabulary with the line. To run one, call the run-skill tool with "
        "the name EXACTLY as spelled above — copy it character for character, "
        "never re-word it and never invent one that is not listed. If the user "
        "names a skill that is not on this list, say so instead of guessing. A "
        "matching skill always beats answering from scratch. A plain question "
        "that merely mentions the topic is not a match."
    )

    if not compact:
        for index in (1, 2):  # 1 = rich blurb, 2 = short blurb
            listed = "\n".join(
                f"- {name} — {entry[index]}" if entry[index] else f"- {name}"
                for entry in entries
                for name in (entry[0],)
            )
            block = f"{header}{listed}{rule}"
            if len(block) <= budget_chars:
                return block
    names_only = ", ".join(name for name, _, _, _ in entries)
    return f"{header}{names_only}{rule}"
