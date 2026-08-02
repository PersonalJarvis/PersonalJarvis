"""A writer that dies mid-call costs the WRITER, not the brief.

Resolving a writer and surviving one are different questions. The first has
guards in ``test_writer_resolution.py``; this file covers what actually broke
the feature on the dev box (2026-08-02): the resolved writer was a key whose
prepayment credits were gone, it answered 429 on every call, and the composer
handed the agent the raw spoken sentence for days while a signed-in
subscription sat unused. One dead credential must not brick the feature when
another provider is right there (AP-22).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.agentic_ide import file_index, prompt_composer

BRIEF = "## Task\nMake the wake path faster.\n\n## Done when\n- It is faster."


@dataclass
class _Profile:
    lines: list[str] = field(default_factory=lambda: ["Folder: /work/project"])

    def summary_lines(self) -> list[str]:
        return self.lines


@dataclass
class _Session:
    folder: str
    profile: _Profile = field(default_factory=_Profile)


class _Writer:
    """A resolved Brain. Which one it is only matters through its name."""

    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture()
def workspace(tmp_path: Path) -> _Session:
    (tmp_path / "jarvis").mkdir(parents=True)
    (tmp_path / "jarvis" / "wake.py").write_text("x")
    file_index.reset_cache()
    file_index._CACHE[str(tmp_path)] = file_index.build_index(tmp_path)  # noqa: SLF001
    return _Session(folder=str(tmp_path))


def _writers(
    monkeypatch: pytest.MonkeyPatch, first: str, rescue: tuple[object, str]
) -> list[str]:
    """Wire a first writer and what the rescue offers. Returns the calls made."""
    used: list[str] = []
    monkeypatch.setattr(
        prompt_composer, "_resolve_writer", lambda: (_Writer(first), f"{first}-rung")
    )
    monkeypatch.setattr(prompt_composer, "_rescue_writer", lambda tried: rescue)
    return used


async def test_a_depleted_writer_hands_the_brief_to_the_next_one(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact live failure, end to end: 429 on the first writer must produce
    a real brief from the second, not the raw sentence."""
    seen: list[str] = []

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        name = getattr(brain, "name", "?")
        seen.append(name)
        if name == "depleted":
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return BRIEF

    _writers(monkeypatch, "depleted", (_Writer("working"), "tool_model:working"))
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert seen == ["depleted", "working"]
    assert result.composed_by == "llm"
    assert "## Task" in result.text


async def test_the_user_is_told_which_writer_took_over(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A composition that silently takes twice as long looks wedged, and which
    model wrote a brief is not a log-file-only question."""
    notices: list[prompt_composer.ComposeNotice] = []

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        if getattr(brain, "name", "") == "depleted":
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return BRIEF

    _writers(monkeypatch, "depleted", (_Writer("working"), "tool_model:working"))
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    await prompt_composer.compose(
        "make the wake path faster",
        session=workspace,
        terminal_name="Kai",
        on_progress=notices.append,
    )

    retries = [n for n in notices if n.stage == prompt_composer.STAGE_RETRY]
    assert len(retries) == 1
    assert "working" in retries[0].message
    assert retries[0].terminal == "Kai"


async def test_debris_from_a_cli_also_crosses_over(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CLI that prints its own flag error to stdout "answered" without writing
    a brief. That is the provider failing, so the next one gets a turn."""

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        if getattr(brain, "name", "") == "broken-cli":
            return 'Error: invalid model selection (--model "x" --effort "")'
        return BRIEF

    _writers(monkeypatch, "broken-cli", (_Writer("working"), "api"))
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "llm"
    assert "## Task" in result.text


async def test_the_dead_rung_is_named_so_it_is_not_retried(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-probing the tier that just died spends the same dead credential."""
    excluded: list[tuple[str, ...]] = []

    async def _boom(**_kwargs: object) -> str:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(
        prompt_composer, "_resolve_writer", lambda: (_Writer("depleted"), "api")
    )
    monkeypatch.setattr(
        prompt_composer,
        "_rescue_writer",
        lambda tried: excluded.append(tuple(tried)) or (None, ""),
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _boom)

    await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert excluded == [("api",)]


async def test_nothing_left_still_delivers_the_deterministic_prompt(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract that outranks everything: the instruction always arrives."""

    async def _boom(**_kwargs: object) -> str:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    _writers(monkeypatch, "depleted", (None, ""))
    monkeypatch.setattr(prompt_composer, "_llm_compose", _boom)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "fallback"
    assert "make the wake path faster" in result.text


async def test_a_caller_pinned_model_is_never_substituted(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``brain=`` is the caller deciding. A fan-out reusing one resolved writer
    across five panes must not have each pane wander off to its own provider."""

    async def _boom(**_kwargs: object) -> str:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(
        prompt_composer, "_rescue_writer", lambda tried: pytest.fail("substituted a pin")
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _boom)

    result = await prompt_composer.compose(
        "make the wake path faster",
        session=workspace,
        terminal_name="Kai",
        brain=_Writer("pinned"),
    )

    assert result.composed_by == "fallback"


async def test_the_rescue_shares_one_budget_with_the_first_attempt(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three writers each burning a full timeout is not a rescue — it is the
    same fallback, minutes later. Once the budget is spent, no one else starts."""
    started: list[str] = []

    async def _hang(*, brain: object, **_kwargs: object) -> str:
        started.append(getattr(brain, "name", "?"))
        raise TimeoutError

    monkeypatch.setattr(prompt_composer, "COMPOSE_TIMEOUT_S", 0.0)
    _writers(monkeypatch, "slow", (_Writer("also-slow"), "api"))
    monkeypatch.setattr(prompt_composer, "_llm_compose", _hang)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert started == []
    assert result.composed_by == "fallback"
