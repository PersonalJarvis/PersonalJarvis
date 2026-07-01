"""Contract tests — every VisionSource implementation satisfies the protocol.

Phase 5 starts with only `FakeVisionSource`; production sources
(ScreenshotSource, UIATreeSource, CompositeVisionEngine) are delivered by
Task 5.1-A and get registered in the `SOURCES` list here.
"""
from __future__ import annotations

import contextlib
import inspect

import pytest

from jarvis.core.protocols import Observation, VisionSource
from tests.fixtures.vision.fake_vision import FakeVisionSource


def _get_sources() -> list[VisionSource]:
    sources: list[VisionSource] = [FakeVisionSource()]
    # production sources are optional until 5.1-A has run
    with contextlib.suppress(Exception):
        from jarvis.vision.screenshot import ScreenshotSource  # type: ignore[attr-defined]
        sources.append(ScreenshotSource())
    with contextlib.suppress(Exception):
        from jarvis.vision.uia_tree import UIATreeSource  # type: ignore[attr-defined]
        sources.append(UIATreeSource())
    return sources


@pytest.mark.parametrize("source", _get_sources(), ids=lambda s: s.name)
def test_vision_source_has_required_attrs(source):
    assert isinstance(source.name, str) and source.name
    assert source.kind in ("screenshot", "ui_tree", "composite")
    assert inspect.iscoroutinefunction(source.observe)
    assert inspect.iscoroutinefunction(source.close)


@pytest.mark.parametrize("source", _get_sources(), ids=lambda s: s.name)
def test_vision_source_structurally_matches_protocol(source):
    assert isinstance(source, VisionSource), (
        f"{source.name} does not satisfy the VisionSource protocol"
    )


@pytest.mark.asyncio
async def test_fake_vision_source_returns_observation():
    src = FakeVisionSource(default_window_title="Notepad")
    obs = await src.observe()
    assert isinstance(obs, Observation)
    assert obs.window_title == "Notepad"
    assert obs.screenshot_hash  # hash ist gesetzt
    assert obs.source in ("full", "screenshot_only", "ui_tree_only")


@pytest.mark.asyncio
async def test_fake_vision_source_respects_window_filter():
    src = FakeVisionSource()
    obs = await src.observe(window_title_filter="Outlook")
    assert obs.window_title == "Outlook"
