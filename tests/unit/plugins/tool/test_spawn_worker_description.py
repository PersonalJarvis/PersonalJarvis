"""Policy pins for the spawn_worker tool description (LLM tool-choice surface).

LLMs select tools primarily by their description (the lesson from the
2026-05-29 computer-use amendment in ADR-0011). The old description sold
spawn_worker for generic "Recherche", so the router model delegated plain
news/knowledge questions to a multi-minute worker mission — the 2026-06-10
user complaint. These pins keep the description honest: heavy-only, with an
explicit negative boundary for simple/medium requests.
"""
from __future__ import annotations

from jarvis.plugins.tool.spawn_worker import SpawnWorkerTool


def test_description_reserves_tool_for_heavy_tasks() -> None:
    desc = SpawnWorkerTool.description
    assert "HEAVY" in desc, "description must shout the heavy-only contract"
    assert "ONLY" in desc, "description must restrict usage, not invite it"


def test_description_names_the_real_cost() -> None:
    """The model must see that a mission runs minutes, not seconds —
    otherwise delegation looks like the cheap option."""
    assert "minutes" in SpawnWorkerTool.description.lower()


def test_description_excludes_simple_and_medium_requests() -> None:
    desc = SpawnWorkerTool.description
    assert "NEVER" in desc, (
        "description needs an explicit negative boundary (questions, news, "
        "single lookups) or the model keeps over-delegating"
    )
    assert "news" in desc.lower()


def test_description_no_longer_sells_generic_research() -> None:
    """The old German description listed 'mehrstufige Recherche' as a normal
    use case — that phrasing is what pulled single-lookup questions into
    worker missions. Deep multi-step research with a deliverable is still
    legitimate, but the generic sales pitch must be gone."""
    assert "mehrstufige Recherche" not in SpawnWorkerTool.description
