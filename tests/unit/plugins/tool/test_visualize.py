"""Tests for the ``visualize`` router tool.

The tool is thin by design — validate, render, archive, announce — so what is
worth pinning is the seams between those four, and the failure behaviour of
each. Two of them matter more than the rest:

* a rejected spec must come back as a RETRYABLE error carrying the validator's
  own words, because that message is what the model reads before its second
  attempt;
* a bus fault must not turn a successfully drawn picture into a failed call.
  The file exists and the gallery lists it either way; reporting failure would
  make the model apologise for something that worked, or draw it twice.

``JARVIS_ISOLATION_ROOT`` redirects the archive so no test writes into the real
outputs tree — the documented override, not a monkeypatched internal.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.plugins.tool.visualize import VisualizeTool
from jarvis.visuals.spec import VISUAL_KINDS


class _FakeBus:
    """Records what was published; a fake, not a mock (project convention)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.published: list[Any] = []
        self._fail = fail

    async def publish(self, event: Any) -> None:
        if self._fail:
            raise RuntimeError("bus is down")
        self.published.append(event)


@pytest.fixture()
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JARVIS_ISOLATION_ROOT", str(tmp_path))
    return tmp_path


def _args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "title": "How a turn is answered",
        "kind": "flow",
        "items": [{"label": "Listen"}, {"label": "Decide"}, {"label": "Answer"}],
    }
    args.update(overrides)
    return args


class _Ctx:
    user_text = "visualisier mir den ablauf"  # i18n-allow: DE test vocabulary


def _read_archived_page(archive: Path, output: dict[str, Any]) -> str:
    """The page's text, reached exactly the way the gallery reaches it.

    Reading it back through ``slug`` + ``artifact_path`` (rather than by
    globbing) is what proves those two strings actually point at the file.
    Synchronous on purpose: filesystem calls do not belong in an async body.
    """
    page = archive.joinpath(output["slug"], *output["artifact_path"].split("/"))
    assert page.is_file(), page
    return page.read_text(encoding="utf-8")


def _archived_runs(archive: Path) -> list[str]:
    """Run directories in the archive. Synchronous, for the same reason."""
    return sorted(entry.name for entry in archive.iterdir())


# --- Contract ----------------------------------------------------------------


def test_the_tool_declares_itself_as_a_safe_router_tool():
    assert VisualizeTool.name == "visualize"
    assert VisualizeTool.risk_tier == "safe"


def test_the_schema_enum_matches_the_renderer_kinds():
    """Five-layer parity: schema, VISUAL_KINDS and the renderer branches agree.

    A kind advertised to the model but unknown to the renderer is a KeyError on
    the user's turn; one the renderer knows but the schema hides is dead code.
    """
    schema_kinds = VisualizeTool.schema["properties"]["kind"]["enum"]
    assert list(schema_kinds) == list(VISUAL_KINDS)


def test_the_description_sends_gallery_requests_to_navigate():
    """The two features answer to the same word; the tool has to disambiguate."""
    assert "navigate" in VisualizeTool.description


# --- Happy path --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_produces_a_page_and_opens_the_section(archive: Path):
    bus = _FakeBus()
    result = await VisualizeTool(bus=bus).execute(_args(), _Ctx())

    assert result.success is True
    body = _read_archived_page(archive, result.output)
    assert "How a turn is answered" in body
    assert "Listen" in body

    # The UI is moved to the section that shows it.
    assert len(bus.published) == 1
    assert getattr(bus.published[0], "section", None) == "visualization"


@pytest.mark.asyncio
async def test_the_summary_says_what_was_drawn(archive: Path):
    result = await VisualizeTool(bus=_FakeBus()).execute(_args(), _Ctx())
    summary = result.output["summary"]
    assert "How a turn is answered" in summary
    assert "flow" in summary


@pytest.mark.asyncio
async def test_the_utterance_labels_the_run(archive: Path):
    """The tile in the gallery reads as what was asked, not as a hex id."""
    result = await VisualizeTool(bus=_FakeBus()).execute(_args(), _Ctx())
    assert "visualisier" in result.output["slug"]


@pytest.mark.asyncio
async def test_a_missing_context_still_draws(archive: Path):
    """The utterance is decoration; no picture fails over a missing attribute."""
    result = await VisualizeTool(bus=_FakeBus()).execute(_args(), object())
    assert result.success is True


@pytest.mark.parametrize("kind", VISUAL_KINDS)
@pytest.mark.asyncio
async def test_every_advertised_kind_can_actually_be_drawn(kind: str, archive: Path):
    items = [{"label": "A", "value": 2}, {"label": "B", "value": 5}]
    result = await VisualizeTool(bus=_FakeBus()).execute(
        _args(kind=kind, items=items), _Ctx()
    )
    assert result.success is True, result.error


# --- Failure behaviour -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bad_spec_comes_back_retryable_with_the_reason(archive: Path):
    bus = _FakeBus()
    result = await VisualizeTool(bus=bus).execute(_args(kind="piechart"), _Ctx())

    assert result.success is False
    assert "piechart" in result.error
    for kind in VISUAL_KINDS:
        assert kind in result.error  # the model is told what to pick instead
    assert bus.published == []  # nothing was announced for a picture never drawn


@pytest.mark.asyncio
async def test_bars_without_numbers_are_refused_before_anything_is_written(archive: Path):
    result = await VisualizeTool(bus=_FakeBus()).execute(
        _args(kind="bars", items=[{"label": "A"}]), _Ctx()
    )
    assert result.success is False
    assert _archived_runs(archive) == []


@pytest.mark.asyncio
async def test_a_dead_bus_does_not_undo_a_drawn_picture(archive: Path):
    """The file exists and the gallery lists it — the call succeeded."""
    result = await VisualizeTool(bus=_FakeBus(fail=True)).execute(_args(), _Ctx())
    assert result.success is True
    assert "How a turn is answered" in _read_archived_page(archive, result.output)
