"""Tests for the UI-tree ``VisionSource`` factory (Wave 2.4, AD-6 + AD-7 + AD-10).

Strategy (this suite runs on the Windows dev box where pyobjc/pyatspi are NOT
installed): factory-selection tests monkeypatch ``detect_platform`` /
``detect_capabilities`` so each per-OS branch is exercised on every leg. The
sources lazy-import their native libs, so constructing ``AXTreeSource`` /
``AtspiTreeSource`` here needs no pyobjc/pyatspi.
"""

from __future__ import annotations

import pytest

import jarvis.vision.tree_factory as factory_mod
from jarvis.core.protocols import Observation, VisionSource
from jarvis.platform.capabilities import reset_capabilities_cache
from jarvis.vision.atspi_tree import AtspiTreeSource
from jarvis.vision.ax_tree import AXTreeSource
from jarvis.vision.tree_factory import NullUITreeSource, make_ui_tree_source
from jarvis.vision.uia_tree import UIATreeSource


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_capabilities_cache()
    factory_mod._warned_null = False  # reset the once-only degrade log
    yield
    reset_capabilities_cache()


def _force(monkeypatch, platform: str, *, has_ax_tree: bool = True) -> None:
    monkeypatch.setattr(factory_mod, "detect_platform", lambda: platform)

    class _Caps:
        pass

    caps = _Caps()
    caps.has_ax_tree = has_ax_tree  # type: ignore[attr-defined]
    monkeypatch.setattr(factory_mod, "detect_capabilities", lambda: caps)


# ---- Per-platform selection -----------------------------------------------


def test_win32_returns_uia_source(monkeypatch) -> None:
    _force(monkeypatch, "win32")
    src = make_ui_tree_source()
    assert isinstance(src, UIATreeSource)


def test_darwin_returns_ax_source(monkeypatch) -> None:
    _force(monkeypatch, "darwin")
    src = make_ui_tree_source()
    assert isinstance(src, AXTreeSource)


def test_linux_with_capability_returns_atspi_source(monkeypatch) -> None:
    _force(monkeypatch, "linux", has_ax_tree=True)
    src = make_ui_tree_source()
    assert isinstance(src, AtspiTreeSource)


def test_linux_without_capability_returns_null_source(monkeypatch) -> None:
    _force(monkeypatch, "linux", has_ax_tree=False)
    src = make_ui_tree_source()
    assert isinstance(src, NullUITreeSource)


# ---- Protocol conformance on every branch ----------------------------------


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_factory_result_satisfies_protocol(monkeypatch, platform) -> None:
    _force(monkeypatch, platform, has_ax_tree=True)
    assert isinstance(make_ui_tree_source(), VisionSource)


def test_null_source_satisfies_protocol() -> None:
    assert isinstance(NullUITreeSource(), VisionSource)


def test_default_factory_never_raises_and_is_a_source() -> None:
    # No monkeypatch: on the Windows dev box this returns the real UIATreeSource.
    src = make_ui_tree_source()
    assert isinstance(src, VisionSource)


# ---- Null-source degrade contract ------------------------------------------


@pytest.mark.asyncio
async def test_null_source_observe_yields_empty_screenshot_only() -> None:
    src = NullUITreeSource()
    obs = await src.observe()
    assert isinstance(obs, Observation)
    assert obs.nodes == ()
    assert obs.source == "screenshot_only"
    await src.close()  # no-op, must not raise


@pytest.mark.asyncio
async def test_linux_null_branch_degrades_end_to_end(monkeypatch) -> None:
    _force(monkeypatch, "linux", has_ax_tree=False)
    src = make_ui_tree_source()
    obs = await src.observe()
    assert obs.nodes == ()
    assert obs.source == "screenshot_only"
