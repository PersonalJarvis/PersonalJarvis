"""Guards for turning ONE order into N distinct assignments.

The feature the maintainer asked for on 2026-07-26: "spawn five deep-dive
agents, split the analysis across areas, and say exactly what each has to do".
Handing all five the same sentence is not a fan-out — it is five agents racing
on the same file with five conflicting opinions, and it burns five subscriptions
to do one agent's work.

What is pinned here:

* **Every agent gets a different slice.** Overlap is the failure mode, so the
  areas are checked for distinctness, not just for existing.
* **The count is exact.** Asking for five and briefing three is the partial
  delivery this whole change set exists to stop.
* **It works with no API key at all.** The deterministic split by top-level
  directory is not a nicety: a downloader whose only credential is for some
  other provider must still be able to run a fleet (§3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.agentic_ide import file_index, work_split


@dataclass
class _Profile:
    lines: list[str] = field(default_factory=lambda: ["Python project"])

    def summary_lines(self) -> list[str]:
        return self.lines


@dataclass
class _Session:
    folder: str
    profile: _Profile = field(default_factory=_Profile)


class _Splitter:
    """Stands in for a resolved quality-tier Brain."""


@pytest.fixture(autouse=True)
def _splitter_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quality-tier model is reachable unless a test says otherwise."""
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: (_Splitter(), "test"))


@pytest.fixture()
def workspace(tmp_path: Path) -> _Session:
    for sub in ("jarvis/plugins/wake", "jarvis/ui/web", "docs", "tests/unit"):
        (tmp_path / sub).mkdir(parents=True)
        (tmp_path / sub / "module.py").write_text("x", encoding="utf-8")
    file_index.reset_cache()
    return _Session(folder=str(tmp_path))


def _answer(monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    """Pin what the model returns for the one splitting call."""

    async def fake_llm_split(**_kwargs: object) -> str:
        return payload

    monkeypatch.setattr(work_split, "_llm_split", fake_llm_split)


VALID_THREE = """
[
  {"area": "wake pipeline", "task": "audit the wake detectors end to end",
   "files": ["jarvis/plugins/wake/module.py"], "done_when": "every path traced"},
  {"area": "web UI", "task": "audit the web surface for cross-platform gaps",
   "files": ["jarvis/ui/web/module.py"], "done_when": "all three OSes checked"},
  {"area": "docs", "task": "check the docs against the code",
   "files": ["docs/module.py"], "done_when": "drift listed"}
]
"""


async def test_one_agent_gets_the_whole_task_without_a_model_call(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Splitting one way is not splitting — it must not cost a provider call."""

    async def explode(**_kwargs: object) -> str:
        raise AssertionError("the model must not be called for a single agent")

    monkeypatch.setattr(work_split, "_llm_split", explode)

    result = await work_split.split(
        "analyse the whole codebase", session=workspace, count=1
    )
    assert len(result.assignments) == 1
    assert "analyse the whole codebase" in result.assignments[0].task


async def test_the_model_split_yields_one_assignment_per_agent(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, VALID_THREE)
    result = await work_split.split(
        "analyse the whole codebase", session=workspace, count=3
    )
    assert result.split_by == "llm"
    assert len(result.assignments) == 3
    assert [a.area for a in result.assignments] == ["wake pipeline", "web UI", "docs"]
    assert result.assignments[0].done_when == "every path traced"


async def test_areas_must_be_distinct(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two agents on the same area is the exact waste this feature avoids."""
    _answer(
        monkeypatch,
        '[{"area": "wake", "task": "audit wake"},'
        ' {"area": "wake", "task": "audit wake again"}]',
    )
    result = await work_split.split("analyse it", session=workspace, count=2)
    assert result.split_by == "fallback"


async def test_too_few_assignments_fall_back(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three agents asked for, two briefed, is a silent partial."""
    _answer(
        monkeypatch,
        '[{"area": "a", "task": "audit a"}, {"area": "b", "task": "audit b"}]',
    )
    result = await work_split.split("analyse it", session=workspace, count=3)
    assert result.split_by == "fallback"
    assert len(result.assignments) == 3


async def test_malformed_json_falls_back(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, "sure! here are the areas: wake, ui, docs")
    result = await work_split.split("analyse it", session=workspace, count=3)
    assert result.split_by == "fallback"
    assert len(result.assignments) == 3


async def test_an_empty_task_falls_back(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(
        monkeypatch,
        '[{"area": "a", "task": ""}, {"area": "b", "task": "audit b"}]',
    )
    result = await work_split.split("analyse it", session=workspace, count=2)
    assert result.split_by == "fallback"


async def test_a_fenced_answer_is_still_read(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Models wrap JSON in a code fence far too often to treat as a failure."""
    _answer(monkeypatch, f"```json\n{VALID_THREE}\n```")
    result = await work_split.split("analyse it", session=workspace, count=3)
    assert result.split_by == "llm"
    assert len(result.assignments) == 3


async def test_a_provider_failure_falls_back(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**_kwargs: object) -> str:
        raise RuntimeError("provider down")

    monkeypatch.setattr(work_split, "_llm_split", boom)
    result = await work_split.split("analyse it", session=workspace, count=3)
    assert result.split_by == "fallback"
    assert len(result.assignments) == 3


async def test_no_quality_provider_still_splits(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A downloader with no API key must still be able to run a fleet (§3)."""
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: (None, ""))
    result = await work_split.split("analyse it", session=workspace, count=3)
    assert result.split_by == "fallback"
    assert len(result.assignments) == 3
    assert result.note


async def test_the_deterministic_split_uses_real_directories(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: (None, ""))
    result = await work_split.split("analyse it", session=workspace, count=3)
    areas = " ".join(a.area for a in result.assignments)
    # The fixture workspace has jarvis/, docs/ and tests/ — the split must name
    # parts of THIS repository, never invented ones.
    assert "jarvis" in areas or "docs" in areas or "tests" in areas


async def test_the_deterministic_split_never_repeats_an_area(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: (None, ""))
    result = await work_split.split("analyse it", session=workspace, count=3)
    areas = [a.area for a in result.assignments]
    assert len(set(areas)) == len(areas)


async def test_every_assignment_carries_the_original_order(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slice without the goal is an agent doing something else entirely."""
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: (None, ""))
    result = await work_split.split(
        "check cross-platform support", session=workspace, count=3
    )
    for assignment in result.assignments:
        assert "cross-platform" in assignment.task


async def test_more_agents_than_directories_still_gets_everyone_a_slice(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight agents on a four-directory repo: nobody may be left idle."""
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: (None, ""))
    result = await work_split.split("analyse it", session=workspace, count=8)
    assert len(result.assignments) == 8
    assert all(a.task.strip() for a in result.assignments)
    assert len({a.area for a in result.assignments}) == 8


async def test_an_empty_folder_still_produces_one_slice_per_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No readable structure is not a reason to brief nobody."""
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: (None, ""))
    file_index.reset_cache()
    result = await work_split.split(
        "analyse it", session=_Session(folder=str(tmp_path)), count=3
    )
    assert len(result.assignments) == 3
    assert all(a.task.strip() for a in result.assignments)


async def test_zero_or_negative_counts_are_refused_not_guessed(
    workspace: _Session,
) -> None:
    result = await work_split.split("analyse it", session=workspace, count=0)
    assert result.assignments == ()


async def test_a_dead_planner_hands_the_plan_to_the_next_provider(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composer's failure mode applies here too: a depleted key answering
    429 must cost the provider, not drop the fleet onto the crude by-directory
    split while a working provider sits idle (AP-22)."""
    calls: list[int] = []

    async def fake_llm_split(**_kwargs: object) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return VALID_THREE

    monkeypatch.setattr(work_split, "_llm_split", fake_llm_split)
    monkeypatch.setattr(
        work_split, "_rescue_splitter", lambda tried: (_Splitter(), "tool_model:working")
    )

    result = await work_split.split("analyse it", session=workspace, count=3)

    assert result.split_by == "llm"
    assert len(result.assignments) == 3
    assert len(calls) == 2


async def test_the_rung_that_died_is_named_for_the_rescue(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-probing the tier that just failed spends the same dead credential."""
    excluded: list[tuple[str, ...]] = []

    async def fake_llm_split(**_kwargs: object) -> str:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(work_split, "_llm_split", fake_llm_split)
    monkeypatch.setattr(
        work_split, "_resolve_splitter", lambda: (_Splitter(), "api")
    )
    monkeypatch.setattr(
        work_split,
        "_rescue_splitter",
        lambda tried: excluded.append(tuple(tried)) or (None, ""),
    )

    result = await work_split.split("analyse it", session=workspace, count=3)

    assert excluded == [("api",)]
    assert result.split_by == "fallback"
    assert len(result.assignments) == 3


async def test_a_pinned_planner_is_never_substituted(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``brain=`` is the caller deciding which model plans this fleet."""

    async def fake_llm_split(**_kwargs: object) -> str:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(work_split, "_llm_split", fake_llm_split)
    monkeypatch.setattr(
        work_split, "_rescue_splitter", lambda tried: pytest.fail("substituted a pin")
    )

    result = await work_split.split(
        "analyse it", session=workspace, count=3, brain=_Splitter()
    )

    assert result.split_by == "fallback"
