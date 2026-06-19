"""CLI-source capabilities: registrable, object-required matching (AD-CLI2)."""
from jarvis.core.capabilities import Capability, CapabilityRegistry


def _cli_cap() -> Capability:
    return Capability(
        id="cli.gh",
        source="cli",
        verbs=("zeig", "list", "show"),
        objects=("pull request", "issue", "issues", "repo"),
        description="GitHub repos, PRs and issues via gh.",
        risk_tier="monitor",
        requires_evidence=True,
    )


def test_cli_source_registers_and_resolves_with_object():
    reg = CapabilityRegistry()
    reg.register(_cli_cap())
    cap = reg.resolve_intent("zeig mir die offenen Issues")
    assert cap is not None and cap.id == "cli.gh"


def test_cli_source_requires_object_match():
    # A bare generic verb must NOT resolve to a CLI capability — same
    # domain-specific rule as paired skills (prevents verb hijacking).
    reg = CapabilityRegistry()
    reg.register(_cli_cap())
    assert reg.resolve_intent("zeig mal her") is None


def test_paired_skill_beats_cli_on_tie():
    reg = CapabilityRegistry()
    reg.register(_cli_cap())
    reg.register(Capability(
        id="skill.paired.github",
        source="skill",
        verbs=("zeig",),
        objects=("issue", "issues"),
        description="Paired GitHub plugin skill.",
        risk_tier="ask",
        requires_evidence=True,
    ))
    cap = reg.resolve_intent("zeig mir die Issues")
    assert cap is not None and cap.id == "skill.paired.github"
