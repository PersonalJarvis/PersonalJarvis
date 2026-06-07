"""Deterministic generator: a paired SKILL.md -> a CapabilityRegistry entry.

The SKILL.md is the single source of truth for a plugin's (or standalone
skill's) intent vocabulary. This module turns its `intent_verbs` +
`intent_objects` frontmatter into a `Capability`, so `resolve_intent()` matches
the user's utterance and the connected plugin becomes reachable -- which
silences BOTH the UNSUPPORTED refusal (local_action_gate.py) AND the
force-spawn gate (manager.py) in one move.

No LLM, no IO -- pure transform (safe to call on the registry at boot and on the
plugin-connect/disconnect lifecycle). The intent vocabulary itself is authored
by a subagent in the SKILL.md; the wiring here is mechanical.
"""
from __future__ import annotations

import logging

from jarvis.core.capabilities import Capability, CapabilityRegistry
from jarvis.skills.schema import Skill, SkillLifecycleState

log = logging.getLogger(__name__)

#: All paired capabilities share this id prefix so register_paired_capabilities
#: can withdraw the whole set on reload without touching seeded/MCP caps.
PAIRED_CAP_PREFIX = "skill.paired."

# Only ACTIVE / VALIDATED skills contribute a capability (DRAFT/DISABLED must
# not silently grant reachability -- mirrors SkillRegistry.list_active).
_LIVE_STATES = (SkillLifecycleState.ACTIVE, SkillLifecycleState.VALIDATED)


def capability_from_skill(skill: Skill) -> Capability | None:
    """Return a Capability for a paired/intent-carrying skill, or None.

    None when: no frontmatter, not a live state, or no intent vocabulary
    (a skill with neither verbs nor objects cannot resolve anything).
    """
    fm = skill.frontmatter
    if fm is None or skill.state not in _LIVE_STATES:
        return None
    verbs = tuple(v.strip() for v in fm.intent_verbs if v and v.strip())
    objects = tuple(o.strip() for o in fm.intent_objects if o and o.strip())
    if not verbs or not objects:
        # resolve_intent needs at least one verb; an object-less cap can never
        # win specificity. Require both so the capability is meaningful.
        return None
    # Identity: prefer the plugin_id (one cap per plugin); fall back to the
    # skill name for standalone "skill without plugin".
    ident = (fm.plugin_id or fm.name).strip()
    return Capability(
        id=f"{PAIRED_CAP_PREFIX}{ident}",
        source="skill",
        verbs=verbs,
        objects=objects,
        description=fm.description or f"Paired skill capability for {ident}.",
        risk_tier=fm.risk_policy.default_tier,
        requires_evidence=True,
    )


def register_paired_capabilities(
    registry: CapabilityRegistry, skills: list[Skill]
) -> int:
    """Register a capability for every live paired/intent skill. Returns count.

    Idempotent: re-registration replaces the previous entry (capabilities.py).
    Caller is responsible for ordering (call after seed_registry so an explicit
    paired cap can override a weak MCP auto-cap for the same domain)."""
    n = 0
    for skill in skills:
        cap = capability_from_skill(skill)
        if cap is not None:
            registry.register(cap)
            n += 1
    log.info("plugin_coupling: registered %d paired capabilities", n)
    return n
