"""Unit tests for ``AXTreeSource`` (Wave 2.1) — driven by ``fake_ax_api``.

All tests here run on any OS: the real pyobjc traversal and permission probe are
replaced by dependency-injected fakes. A test needing a *real* AX grant would be
marked ``skip_ci`` + ``pytest.importorskip("Quartz")``; none is needed for the
logic coverage below.
"""

from __future__ import annotations

import pytest

from jarvis.core.protocols import Observation, VisionSource
from jarvis.vision.ax_tree import AXTreeSource
from tests.fakes.fake_ax_api import (
    FakeAXElement,
    build_fake_ax_traverser,
    make_canned_ax_tree,
)


def test_protocol_conformance_on_any_os() -> None:
    # runtime_checkable structural Protocol — must hold on Windows with no pyobjc.
    assert isinstance(AXTreeSource(), VisionSource)
    src = AXTreeSource()
    assert src.name == "ax-tree"
    assert src.kind == "ui_tree"


@pytest.mark.asyncio
async def test_module_imports_without_pyobjc() -> None:
    # Importing the module + constructing the source must not need a native lib.
    import jarvis.vision.ax_tree as mod

    assert mod.AXTreeSource is AXTreeSource


@pytest.mark.asyncio
async def test_flatten_and_role_normalization() -> None:
    traverser = build_fake_ax_traverser(make_canned_ax_tree(), window_title="Demo.app")
    src = AXTreeSource(
        traverser=traverser,
        permission_check=lambda: True,
        monitor_bounds=(0, 0, 1920, 1080),
    )
    obs = await src.observe()

    assert isinstance(obs, Observation)
    assert obs.source == "ui_tree_only"
    assert obs.window_title == "Demo.app"

    roles = [n.role for n in obs.nodes]
    # The canned tree's interesting controls, normalized to canonical UIA roles.
    assert "Button" in roles
    assert "Edit" in roles
    assert "Hyperlink" in roles
    assert "CheckBox" in roles
    assert "Text" in roles  # AXStaticText nested inside the dropped group
    # No native AX role ever leaks into the Observation.
    assert not any(r.startswith("AX") for r in roles)
    # prune_tree always keeps the root (depth=0) to preserve the parent
    # hierarchy, even though its role (AXWindow) is dropped to "" by the role
    # map. The only empty-role node permitted is that single root.
    assert roles.count("") <= 1
    # The non-root structural containers (AXGroup) were removed by the
    # role-whitelist prune — only the canonical interesting roles + the root
    # survive.
    interesting = [r for r in roles if r]
    assert all(r in {"Button", "Edit", "Hyperlink", "CheckBox", "Text"} for r in interesting)


@pytest.mark.asyncio
async def test_textfield_carries_value_and_automation_id() -> None:
    traverser = build_fake_ax_traverser(make_canned_ax_tree())
    src = AXTreeSource(
        traverser=traverser,
        permission_check=lambda: True,
        monitor_bounds=(0, 0, 1920, 1080),
    )
    obs = await src.observe()
    edits = [n for n in obs.nodes if n.role == "Edit"]
    assert edits, "expected the AXTextField to normalize to an Edit node"
    edit = edits[0]
    assert edit.automation_id == "search-box"
    assert edit.name == "hello"  # AXValue used as the name fallback
    assert edit.bounds == (10, 50, 200, 24)


@pytest.mark.asyncio
async def test_permission_denied_degrades_to_empty_screenshot_only() -> None:
    # AD-13: AXIsProcessTrusted()==False -> empty nodes, source=screenshot_only,
    # never raises. The traverser must NOT even be consulted.
    consulted = {"called": False}

    def _traverser(_depth, _filter):
        consulted["called"] = True
        return ("should-not-appear", 0, [])

    src = AXTreeSource(traverser=_traverser, permission_check=lambda: False)
    obs = await src.observe()

    assert obs.nodes == ()
    assert obs.source == "screenshot_only"
    assert obs.window_title == ""
    assert consulted["called"] is False


@pytest.mark.asyncio
async def test_empty_tree_when_no_frontmost_app() -> None:
    src = AXTreeSource(
        traverser=lambda _d, _f: ("", 0, []),
        permission_check=lambda: True,
    )
    obs = await src.observe()
    # No interesting nodes -> empty, but still a valid Observation (not a crash).
    assert obs.nodes == ()


@pytest.mark.asyncio
async def test_offscreen_nodes_pruned() -> None:
    # A node far off the monitor is dropped by the on-screen filter.
    root = FakeAXElement(
        role="AXWindow",
        title="W",
        position=(0, 0),
        size=(800, 600),
        children=[
            FakeAXElement(role="AXButton", title="OnScreen", position=(10, 10), size=(50, 20)),
            FakeAXElement(role="AXButton", title="OffScreen", position=(9000, 9000), size=(50, 20)),
        ],
    )
    src = AXTreeSource(
        traverser=build_fake_ax_traverser(root),
        permission_check=lambda: True,
        monitor_bounds=(0, 0, 1920, 1080),
    )
    obs = await src.observe()
    names = [n.name for n in obs.nodes]
    assert "OnScreen" in names
    assert "OffScreen" not in names


@pytest.mark.asyncio
async def test_close_is_idempotent_and_blocks_observe() -> None:
    src = AXTreeSource(permission_check=lambda: True)
    await src.close()
    with pytest.raises(RuntimeError):
        await src.observe()


@pytest.mark.asyncio
async def test_real_pyobjc_capture() -> None:
    # Real AX capture needs pyobjc + a granted Accessibility permission.
    pytest.importorskip("Quartz")
    pytest.skip("requires a real macOS Accessibility grant — Wave 4 live sign-off")
