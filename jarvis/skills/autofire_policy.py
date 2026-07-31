"""Which skills may capture a turn on their own, and which never may.

A matched skill is not an addition to a turn — it is a takeover. Once
``BrainManager`` accepts a match it renders the skill's body into the turn
context, REMOVES ``run-skill`` from the tool set (so the model has no way to
signal "wrong skill"), stands the deterministic local-action fast path down,
stands the evidence gate down, and — for ``execution: mission`` — dispatches a
worker and returns before the model sees anything at all.

That asymmetry is the whole reason this module exists. A wrong suggestion is
free. A wrong takeover spends money, suppresses Computer-Use, and makes the
assistant confidently claim a capability it does not have.

This is not theoretical. ``cloud-debug`` ships as ``execution: mission`` with
``default_tier: monitor`` and **no triggers at all**: unreachable by matching
today, and a fuzzy layer makes it reachable. A paraphrase match would start an
unrequested Claude Code worker in a git worktree and hand the user an
optimistic acknowledgement, with no model and no confirmation anywhere on the
path.

So capture permission is DERIVED from what the skill actually does, never
merely declared:

* ``instruction``  — adds words to a turn. Blast radius: one turn's tone.
* ``tool_backed``  — will reach for an integration. Survivable, but only while
  the model can still refuse, so the stand-downs stay disarmed for it.
* ``dispatching``  — starts a process or is explicitly blocked. Never captures
  automatically; the model must ask for it via ``run-skill``, or the user must
  name it.

Pure and dependency-free (stdlib only, duck-typed skills), so the brain, the
REST layer and the offline eval all reach the same verdict.
"""
from __future__ import annotations

import re
from typing import Any, Literal

CLASS_INSTRUCTION = "instruction"
CLASS_TOOL_BACKED = "tool_backed"
CLASS_DISPATCHING = "dispatching"

#: Closed set — mirrored in TypeScript, pinned by a parity test.
AUTOFIRE_CLASSES: tuple[str, ...] = (
    CLASS_INSTRUCTION,
    CLASS_TOOL_BACKED,
    CLASS_DISPATCHING,
)

AutoFireClass = Literal["instruction", "tool_backed", "dispatching"]

#: Frontmatter override vocabulary. ``auto`` (the default, and what every
#: existing skill has by virtue of not carrying the field) means "the class
#: table decides".
AUTO_FIRE_AUTO = "auto"
AUTO_FIRE_ALWAYS = "always"
AUTO_FIRE_NEVER = "never"
AUTO_FIRE_MODES: tuple[str, ...] = (AUTO_FIRE_AUTO, AUTO_FIRE_ALWAYS, AUTO_FIRE_NEVER)

#: Risk tiers an ``instruction`` skill may carry. ``ask`` needs the voice
#: confirmation machinery that lives in the orchestrator, so it is excluded and
#: falls through to ``tool_backed``.
_INSTRUCTION_SAFE_TIERS = frozenset({"safe", "monitor", ""})

#: Legacy ``TOOL:`` macro lines in a skill body. A body containing them was
#: written for the retired macro executor and may name arbitrary tools, so it is
#: treated as dispatching regardless of what the frontmatter claims.
_TOOL_LINE_RE = re.compile(r"^\s*TOOL:\s*\S", re.MULTILINE)


def _risk_tier(skill: Any) -> str:
    frontmatter = getattr(skill, "frontmatter", None)
    if frontmatter is None:
        return ""
    policy = getattr(frontmatter, "risk_policy", None)
    tier = getattr(policy, "default_tier", None)
    return str(getattr(tier, "value", tier) or "").lower()


def _execution(skill: Any) -> str:
    frontmatter = getattr(skill, "frontmatter", None)
    if frontmatter is None:
        return "inline"
    return str(getattr(frontmatter, "execution", "inline") or "inline").lower()


def body_has_macro_lines(skill: Any) -> bool:
    """True when the body carries legacy ``TOOL:`` macro lines."""
    body = getattr(skill, "body", "") or ""
    return bool(_TOOL_LINE_RE.search(body))


def classify(skill: Any) -> str:
    """Return this skill's auto-fire class. Never raises.

    A skill whose frontmatter failed to parse is ``dispatching``: unknown
    intent must not be granted capture rights.
    """
    frontmatter = getattr(skill, "frontmatter", None)
    if frontmatter is None:
        return CLASS_DISPATCHING

    tier = _risk_tier(skill)
    if tier == "block" or _execution(skill) == "mission":
        return CLASS_DISPATCHING
    if body_has_macro_lines(skill):
        return CLASS_DISPATCHING

    requires_tools = tuple(getattr(frontmatter, "requires_tools", ()) or ())
    plugin_id = getattr(frontmatter, "plugin_id", None)
    if requires_tools or plugin_id or tier == "ask":
        return CLASS_TOOL_BACKED

    if tier in _INSTRUCTION_SAFE_TIERS:
        return CLASS_INSTRUCTION
    # An unrecognised tier is not a licence — fail toward the safer class.
    return CLASS_TOOL_BACKED


def auto_fire_mode(skill: Any, *, override: str | None = None) -> str:
    """Resolve the skill's ``auto`` / ``always`` / ``never`` setting.

    ``override`` is the user's persisted choice from the prefs sidecar, which
    wins over frontmatter — builtin skills need an admin password to edit and
    are re-copied by bootstrap, so a user preference cannot live in their
    frontmatter.
    """
    for candidate in (override, getattr(getattr(skill, "frontmatter", None), "auto_fire", None)):
        value = str(candidate or "").strip().lower()
        if value in AUTO_FIRE_MODES:
            return value
    return AUTO_FIRE_AUTO


def may_capture(
    skill: Any,
    band: str,
    *,
    override: str | None = None,
    min_band: str = "fire",
) -> tuple[bool, str]:
    """May this skill take over the turn at ``band``?

    Returns ``(allowed, veto_reason)``; ``veto_reason`` is a member of
    :data:`jarvis.skills.guards.VETO_REASONS` when denied.

    Args:
        skill: The matched skill (duck-typed).
        band: The match band — ``"fire"`` or ``"narrow"``.
        override: The user's persisted per-skill auto-fire choice, if any.
        min_band: Configured floor. Defaults to ``fire``, i.e. a NARROW match
            never captures unless the deployment deliberately opens it up.
    """
    from jarvis.skills.guards import (
        VETO_AUTO_FIRE_NEVER,
        VETO_BAND_BELOW_FLOOR,
        VETO_DISPATCHING_CLASS,
    )
    from jarvis.skills.match_eval import BAND_FIRE, BAND_NARROW, band_at_least

    if band not in (BAND_FIRE, BAND_NARROW):
        return False, VETO_BAND_BELOW_FLOOR

    mode = auto_fire_mode(skill, override=override)
    if mode == AUTO_FIRE_NEVER:
        return False, VETO_AUTO_FIRE_NEVER

    kind = classify(skill)
    if kind == CLASS_DISPATCHING:
        # The one line no configuration may cross: a frontmatter field must
        # never authorize a deterministic matcher to start a process. `always`
        # is checked AFTER this on purpose.
        return False, VETO_DISPATCHING_CLASS

    if not band_at_least(band, min_band):
        # `always` promotes a tool-backed skill into the weaker band; it can
        # never promote a dispatching one (handled above).
        if mode != AUTO_FIRE_ALWAYS:
            return False, VETO_BAND_BELOW_FLOOR

    return True, ""


def stand_downs_allowed(skill: Any, band: str) -> bool:
    """May a captured turn suppress the other deterministic fast paths?

    Only an ``instruction`` skill at FIRE gets the historical full stand-down.
    Everything weaker keeps ``run-skill`` visible and leaves the local-action
    gate and the evidence gate armed, which turns a wrong match from a hijack
    into a suggestion the model can ignore.

    Live precedent for why this matters (2026-06-21): ``plugin-discord``
    keyword-matched the bare word "Discord", suppressed the local-action fast
    path, and a tool-less deep brain then told the user it had no Discord
    access.
    """
    from jarvis.skills.match_eval import BAND_FIRE

    return band == BAND_FIRE and classify(skill) == CLASS_INSTRUCTION


__all__ = [
    "AUTOFIRE_CLASSES",
    "AUTO_FIRE_ALWAYS",
    "AUTO_FIRE_AUTO",
    "AUTO_FIRE_MODES",
    "AUTO_FIRE_NEVER",
    "CLASS_DISPATCHING",
    "CLASS_INSTRUCTION",
    "CLASS_TOOL_BACKED",
    "AutoFireClass",
    "auto_fire_mode",
    "body_has_macro_lines",
    "classify",
    "may_capture",
    "stand_downs_allowed",
]
