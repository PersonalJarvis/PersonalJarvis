"""The published index says which frontmatter a skill carries.

``flavor: "portable"`` marks an entry written for the open Agent Skills format
rather than for Jarvis — the same file `npx skills add` installs into other
agents. The reader has to stay tolerant about it: the field arrives from a
registry that may be newer than this client, and an unknown value must cost
the word, never the entry.
"""

from __future__ import annotations

from jarvis.marketplace.community_source import CommunityIndex, CommunitySkillEntry


def _skill(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "three-point-check",
        "description": "Three bullets",
        "raw_url": "https://raw.example/skills/three-point-check/SKILL.md",
    }
    base.update(extra)
    return base


def test_portable_flavor_survives() -> None:
    entry = CommunitySkillEntry.model_validate(_skill(flavor="portable"))

    assert entry.flavor == "portable"
    assert entry.is_portable is True


def test_missing_flavor_is_not_portable() -> None:
    # Every entry published before the split was a Jarvis skill, so absence
    # means "jarvis" — not "unknown, refuse to show".
    entry = CommunitySkillEntry.model_validate(_skill())

    assert entry.flavor is None
    assert entry.is_portable is False


def test_an_unknown_flavor_costs_the_word_not_the_entry() -> None:
    entry = CommunitySkillEntry.model_validate(_skill(flavor="quantum"))

    assert entry.flavor is None
    assert entry.name == "three-point-check"
    assert entry.raw_url is not None


def test_flavor_case_and_padding_are_forgiven() -> None:
    assert CommunitySkillEntry.model_validate(_skill(flavor=" Portable ")).is_portable


def test_compatible_agents_are_bounded() -> None:
    # Publisher-written free text that lands in the store UI: a hundred names
    # of a thousand characters each is a layout attack, not a fact.
    entry = CommunitySkillEntry.model_validate(
        _skill(
            compatible_agents=[
                "Claude Code",
                "Claude Code",  # duplicate
                "  Cursor  ",
                "x" * 200,
                42,  # not a string
                *[f"agent-{i}" for i in range(20)],
            ]
        )
    )

    assert len(entry.compatible_agents) <= 8
    assert entry.compatible_agents[0] == "Claude Code"
    assert entry.compatible_agents[1] == "Cursor"
    assert all(len(name) <= 32 for name in entry.compatible_agents)
    assert len(set(entry.compatible_agents)) == len(entry.compatible_agents)


def test_a_garbage_agent_list_degrades_to_empty() -> None:
    entry = CommunitySkillEntry.model_validate(_skill(compatible_agents="Cursor"))

    assert entry.compatible_agents == []


def test_a_portable_entry_still_passes_the_name_gate() -> None:
    # The flavor changes nothing about the security boundary: the name becomes
    # a directory under the user's skills folder either way.
    index = CommunityIndex.model_validate(
        {
            "skills": [
                _skill(name="../../evil", flavor="portable"),
                _skill(flavor="portable"),
            ]
        }
    )

    assert [entry.name for entry in index.skills] == ["three-point-check"]
