"""Every coding harness the workspace can open is counted by the cost index.

The registry (``jarvis/workspace/agents.py``) decides what the "Open beside"
menu offers; the index (``jarvis/costs/cli_usage_index.py``) decides whose
spend the Costs section shows. Nothing forced them to agree, and the drift is
silent: a harness that opens, runs and bills — and never appears in a report.
This binds the two: a new harness fails the build until its transcripts are
read, or until it is listed with the reason they cannot be.
"""

from __future__ import annotations

from jarvis.costs import cli_usage_index as index
from jarvis.workspace import agents as workspace_agents


def _builtin_coding_harnesses() -> list[str]:
    names = set(workspace_agents.builtin_names())
    return [a.name for a in workspace_agents.coding_agents() if a.name in names]


def test_every_builtin_harness_has_a_cost_reader_or_a_stated_reason() -> None:
    harnesses = _builtin_coding_harnesses()
    assert harnesses, "the registry offers no coding harness at all"
    for name in harnesses:
        counted = name in index.COST_READER_FOR_HARNESS
        excused = name in index.HARNESSES_WITHOUT_LOCAL_TRANSCRIPT
        assert counted or excused, (
            f"{name!r} can be opened from the workspace but the Costs section "
            "will never see its spend. Add a reader to jarvis/costs/cli_usage_index.py "
            "and map it in COST_READER_FOR_HARNESS, or state in "
            "HARNESSES_WITHOUT_LOCAL_TRANSCRIPT why its usage cannot be read from disk."
        )
        assert not (counted and excused), f"{name!r} is both counted and excused"


def test_every_mapped_reader_is_a_real_agent() -> None:
    for harness, agent in index.COST_READER_FOR_HARNESS.items():
        assert agent in index.AGENTS, (harness, agent)


def test_no_stale_harness_in_the_cost_map() -> None:
    """A harness removed from the registry must leave the map too, or the map
    stops meaning 'what the menu offers'."""
    registered = set(workspace_agents.coding_agent_names())
    for name in (*index.COST_READER_FOR_HARNESS, *index.HARNESSES_WITHOUT_LOCAL_TRANSCRIPT):
        assert name in registered, f"{name!r} is mapped but no longer registered"
