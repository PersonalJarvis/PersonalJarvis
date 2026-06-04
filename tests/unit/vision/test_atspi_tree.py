"""Unit tests for ``AtspiTreeSource`` (Wave 2.2) — driven by ``fake_atspi``.

All tests here run on any OS: the real pyatspi traversal and bus probe are
replaced by dependency-injected fakes. A test needing the *real* AT-SPI bus
would be marked ``skip_ci`` + ``pytest.importorskip("pyatspi")``; none is needed
for the logic coverage below.
"""

# ``role_token`` is an AT-SPI role name, not a credential (S106 false positive).
# ruff: noqa: S106

from __future__ import annotations

import pytest

from jarvis.core.protocols import Observation, VisionSource
from jarvis.vision.atspi_tree import AtspiTreeSource
from tests.fakes.fake_atspi import (
    FakeAtspiAccessible,
    build_fake_atspi_traverser,
    make_canned_atspi_tree,
)


def test_protocol_conformance_on_any_os() -> None:
    assert isinstance(AtspiTreeSource(), VisionSource)
    src = AtspiTreeSource()
    assert src.name == "atspi-tree"
    assert src.kind == "ui_tree"


@pytest.mark.asyncio
async def test_module_imports_without_pyatspi() -> None:
    import jarvis.vision.atspi_tree as mod

    assert mod.AtspiTreeSource is AtspiTreeSource


@pytest.mark.asyncio
async def test_flatten_and_role_normalization() -> None:
    traverser = build_fake_atspi_traverser(make_canned_atspi_tree(), window_title="demo")
    src = AtspiTreeSource(
        traverser=traverser,
        bus_check=lambda: True,
        monitor_bounds=(0, 0, 1920, 1080),
    )
    obs = await src.observe()

    assert isinstance(obs, Observation)
    assert obs.source == "ui_tree_only"
    assert obs.window_title == "demo"

    roles = [n.role for n in obs.nodes]
    assert "Button" in roles
    assert "Edit" in roles
    assert "Hyperlink" in roles
    assert "CheckBox" in roles
    assert "Text" in roles  # ROLE_LABEL inside the dropped panel
    # No native ROLE_* token ever leaks into the Observation.
    assert not any(r.startswith("ROLE_") for r in roles)
    # prune_tree always keeps the root (depth=0, ROLE_FRAME dropped to "") to
    # preserve the parent hierarchy; that single empty-role root is permitted.
    assert roles.count("") <= 1
    interesting = [r for r in roles if r]
    assert all(r in {"Button", "Edit", "Hyperlink", "CheckBox", "Text"} for r in interesting)


@pytest.mark.asyncio
async def test_entry_bounds_and_enabled() -> None:
    traverser = build_fake_atspi_traverser(make_canned_atspi_tree())
    src = AtspiTreeSource(
        traverser=traverser,
        bus_check=lambda: True,
        monitor_bounds=(0, 0, 1920, 1080),
    )
    obs = await src.observe()
    edits = [n for n in obs.nodes if n.role == "Edit"]
    assert edits, "expected ROLE_ENTRY -> Edit"
    edit = edits[0]
    assert edit.name == "search"
    assert edit.bounds == (10, 50, 200, 24)
    assert edit.enabled is True


@pytest.mark.asyncio
async def test_bus_unreachable_degrades_to_empty_screenshot_only() -> None:
    # AD-13: bus unreachable -> empty nodes, source=screenshot_only, never raises.
    # The traverser must NOT be consulted.
    consulted = {"called": False}

    def _traverser(_depth, _filter):
        consulted["called"] = True
        return ("should-not-appear", 0, [])

    src = AtspiTreeSource(traverser=_traverser, bus_check=lambda: False)
    obs = await src.observe()

    assert obs.nodes == ()
    assert obs.source == "screenshot_only"
    assert obs.window_title == ""
    assert consulted["called"] is False


@pytest.mark.asyncio
async def test_disabled_node_marked_not_enabled() -> None:
    root = FakeAtspiAccessible(
        role_token="ROLE_FRAME",
        name="W",
        extents=(0, 0, 800, 600),
        states={"STATE_ACTIVE"},
        children=[
            FakeAtspiAccessible(
                role_token="ROLE_PUSH_BUTTON",
                name="Disabled",
                extents=(10, 10, 50, 20),
                states=set(),  # no STATE_ENABLED / STATE_SENSITIVE
            ),
        ],
    )
    src = AtspiTreeSource(
        traverser=build_fake_atspi_traverser(root),
        bus_check=lambda: True,
        monitor_bounds=(0, 0, 1920, 1080),
    )
    obs = await src.observe()
    btns = [n for n in obs.nodes if n.role == "Button"]
    assert btns
    assert btns[0].enabled is False


@pytest.mark.asyncio
async def test_close_blocks_observe() -> None:
    src = AtspiTreeSource(bus_check=lambda: True)
    await src.close()
    with pytest.raises(RuntimeError):
        await src.observe()


@pytest.mark.asyncio
async def test_real_pyatspi_capture() -> None:
    # Real AT-SPI capture needs the distro pyatspi package + a live bus.
    pytest.importorskip("pyatspi")
    pytest.skip("requires a live AT-SPI bus — Wave 4 live sign-off")
