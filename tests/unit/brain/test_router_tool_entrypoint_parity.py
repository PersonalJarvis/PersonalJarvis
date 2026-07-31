"""Every registered tool entry point must actually be reachable.

``jarvis/diagnostics/doctor.py`` already checks one direction: every name in
``ROUTER_TOOLS`` resolves to a registered tool. Nothing checked the REVERSE, and
that is where a phantom lives: ``spawn-skill-author`` sat in ``pyproject.toml``
for months while being absent from ``ROUTER_TOOLS``, so ``factory.py`` filtered
it out on every boot and it could never load.

The cost was not the dead code — it was the documentation. Three separate docs
concluded from the entry point that the tool was live, and
``docs/LLM-CONTEXT.md`` went further and "corrected" CLAUDE.md in the wrong
direction. Every agent session read that. A registration is a claim, and an
unreachable claim is worse than an absence.

This test makes the claim checkable.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from jarvis.brain.factory import ROUTER_TOOLS

REPO = Path(__file__).resolve().parents[3]

#: Tools that are registered on purpose WITHOUT being router-visible. Each entry
#: names why, because an unexplained exemption is how the next phantom hides.
INTERNAL_ONLY: dict[str, str] = {
    # Loaded into the hidden local-action tool dict by
    # jarvis/brain/factory.py::_load_local_action_tools — deterministic
    # execution only, never offered to the model (that is the whole point of the
    # local-action gate).
    "dispatch-to-harness": "local-action tool set, never model-visible",
    "open-app": "local-action tool set, never model-visible",
    "type-text": "local-action tool set, never model-visible",
    "hotkey": "local-action tool set, never model-visible",
    "reset-orb-position": "local-action tool set, never model-visible",
    # Attached by the router only when a turn is about the screen
    # (jarvis/brain/vision_gate.py), so it is not a static ROUTER_TOOLS member.
    "screenshot": "gated per-turn by the vision gate",
}


def _declared_tool_entry_points() -> dict[str, str]:
    """``{entry point name: target}`` from the [jarvis.tool] group in pyproject."""
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    group = (
        data.get("project", {})
        .get("entry-points", {})
        .get("jarvis.tool", {})
    )
    assert group, "no [project.entry-points.'jarvis.tool'] group found"
    return dict(group)


#: Entry points that no allow-set admits TODAY. Recorded rather than tolerated
#: silently: ``ROUTER_TOOLS`` is the only allow-set, so ``factory.py`` filters
#: every one of these out on every boot.
#:
#: This is a BACKLOG, not a permission list. Each needs one of three outcomes —
#: wire it, explain it in ``INTERNAL_ONLY``, or delete the entry point — and the
#: tests below let the count shrink but never grow. Auditing all sixteen was out
#: of scope for the change that added this gate; leaving them undeclared would
#: have meant a red gate nobody could act on, and dropping the gate would have
#: meant the next phantom goes unnoticed again.
KNOWN_UNREACHABLE: frozenset[str] = frozenset({
    # Computer-Use primitives — candidates for the local-action set or for the
    # `computer-use` umbrella that IS router-visible.
    "click",
    "click-element",
    "move-mouse",
    "read-visible-ui-state",
    "scroll",
    "switch-window",
    "wait-for-element",
    "wait-for-ui-state",
    # Mission / review vocabulary from earlier waves.
    "dispatch-to-admin",
    "dispatch-with-review",
    "multi-spawn",
    # Superseded by the wiki + awareness tools.
    "remember",
    "whoami",
    # Dev-loop helpers, plausibly worker-only by design.
    "start-preview-server",
    "verify-localhost",
    "verify-via-curl",
})


def test_no_new_unreachable_tool_entry_point_appears() -> None:
    """A registration that no allow-set admits is a phantom, not a feature.

    ``spawn-skill-author`` is why this exists: it sat registered for months,
    filtered out on every boot, while three docs concluded from the registration
    that it was live.
    """
    declared = _declared_tool_entry_points()
    unreachable = {
        name
        for name in declared
        if name not in ROUTER_TOOLS and name not in INTERNAL_ONLY
    }
    new = sorted(unreachable - KNOWN_UNREACHABLE)
    assert not new, (
        f"new unreachable tool entry point(s): {new}\n"
        "factory.py filters these out on every boot, so the registration is a "
        "claim nothing honours. Add the name to ROUTER_TOOLS (and confirm the "
        "constructor takes no required arguments), or to INTERNAL_ONLY with a "
        "reason, or remove the entry point."
    )


def test_the_unreachable_backlog_only_shrinks() -> None:
    """Wiring or deleting one must remove it from the list, not leave it stale.

    Without this the backlog rots into a permission list, which is exactly the
    state that let the original phantom survive.
    """
    declared = _declared_tool_entry_points()
    unreachable = {
        name
        for name in declared
        if name not in ROUTER_TOOLS and name not in INTERNAL_ONLY
    }
    resolved = sorted(KNOWN_UNREACHABLE - unreachable)
    assert not resolved, (
        f"these are no longer unreachable: {resolved} — remove them from "
        "KNOWN_UNREACHABLE so the list keeps meaning something."
    )


def test_the_spawn_skill_author_phantom_stays_removed() -> None:
    """Regression guard for the specific phantom that motivated this test.

    Re-registering it would ALSO crash: its __init__ requires `runner=`, which
    the entry-point loader cannot supply.
    """
    assert "spawn-skill-author" not in _declared_tool_entry_points()
    assert "spawn-skill-author" not in ROUTER_TOOLS
    assert "spawn_skill_author" not in ROUTER_TOOLS


def test_the_dead_tool_still_documents_why_it_is_unwired() -> None:
    """The class is kept for its runner tests — the docstring must say so.

    Without the warning the obvious "fix" is to add the name to ROUTER_TOOLS,
    which raises TypeError at tool load rather than working.
    """
    import jarvis.brain.tools.skill_authoring as module

    doc = module.__doc__ or ""
    assert "NOT WIRED" in doc
    assert "TypeError" in doc


def test_internal_only_exemptions_all_carry_a_reason() -> None:
    for name, reason in INTERNAL_ONLY.items():
        assert reason.strip(), f"{name} is exempt without a stated reason"
