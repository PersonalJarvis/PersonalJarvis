"""BINDING CONVENTION: every marketplace plugin ships a paired skill.

This is the anti-drift gate. A new plugin added to the catalog WITHOUT a
``plugin-<id>/SKILL.md`` (or with a mismatched ``plugin_id``) fails CI — which is
exactly what stops the Gmail-class regression (connected-but-unreachable plugin)
from recurring. See docs/superpowers/plans/2026-06-07-plugin-skill-pairing-reachability.md.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.marketplace.catalog_data import load_catalog
from jarvis.skills.loader import discover_skills

# Repo-root-relative resolution so the test passes regardless of the pytest cwd.
_BUILTIN = Path(__file__).resolve().parents[3] / "jarvis" / "skills" / "builtin"

# Plugins exempt from the convention with a written reason. Keep this list SHORT
# and justified; for a tool-backed plugin, shipping its skill is the fix.
_EXEMPT = {
    # Telegram is an INBOUND CHANNEL adapter (jarvis/channels/telegram.py), not an
    # actionable tool plugin: it is how the user talks TO Jarvis (a trace_id->
    # chat_id reply map), not a service Jarvis drives. A paired skill would point
    # at a tool that does not — and architecturally should not — exist. Revisit
    # only if an outbound "send to a resolved contact" tool is ever built.
    "telegram": "inbound channel adapter, not an actionable tool plugin",
}

# Coding verbs must never appear in a paired skill's intent_verbs: a paired-skill
# cap only matches when BOTH a verb AND a domain object hit (resolve_intent), so
# keeping coding verbs out is what makes a coding task that merely names a domain
# ("implement an Email-Validation") stay generic sub-agent work.
_CODING_VERBS = frozenset({
    "implementier", "implementiere", "baue", "bau", "schreib", "schreibe",
    "entwickel", "entwickle", "refactor", "debug", "code", "programmier",
})


def _paired_skills():
    return [
        s
        for s in discover_skills(_BUILTIN)
        if s.frontmatter is not None and s.frontmatter.plugin_id
    ]


def test_every_plugin_has_paired_skill():
    catalog = load_catalog()
    paired = {s.frontmatter.plugin_id for s in _paired_skills()}
    missing = [
        p.id for p in catalog.plugins if p.id not in paired and p.id not in _EXEMPT
    ]
    assert not missing, (
        f"plugins without a paired skill: {missing}. Add "
        f"jarvis/skills/builtin/plugin-<id>/SKILL.md with plugin_id=<id>, or add "
        f"a justified entry to _EXEMPT."
    )


def test_paired_skill_plugin_ids_are_real_catalog_ids():
    catalog_ids = {p.id for p in load_catalog().plugins}
    stray = [
        s.frontmatter.plugin_id
        for s in _paired_skills()
        if s.frontmatter.plugin_id not in catalog_ids
    ]
    assert not stray, (
        f"paired skills pointing at non-existent catalog plugin ids: {stray}."
    )


def test_paired_skills_exclude_coding_verbs():
    offenders = []
    for s in _paired_skills():
        verbs = {v.lower().strip() for v in s.frontmatter.intent_verbs}
        bad = verbs & _CODING_VERBS
        if bad:
            offenders.append((s.frontmatter.plugin_id, sorted(bad)))
    assert not offenders, (
        f"paired skills carrying coding verbs (would hijack coding tasks that "
        f"merely name the domain): {offenders}. Remove coding verbs from "
        f"intent_verbs — they belong to generic sub-agent work."
    )


def test_paired_skills_have_intent_vocabulary():
    """A paired skill with empty verbs or objects produces no capability and is
    therefore silently unreachable — fail loudly instead."""
    empty = [
        s.frontmatter.plugin_id
        for s in _paired_skills()
        if not s.frontmatter.intent_verbs or not s.frontmatter.intent_objects
    ]
    assert not empty, (
        f"paired skills with empty intent_verbs/intent_objects (no capability "
        f"generated, plugin stays unreachable): {empty}."
    )
