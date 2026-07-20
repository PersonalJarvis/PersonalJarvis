"""Unit tests for ``AXTreeSource`` (Wave 2.1) — driven by ``fake_ax_api``.

All tests here run on any OS: the real pyobjc traversal and permission probe are
replaced by dependency-injected fakes. A test needing a *real* AX grant would be
marked ``skip_ci`` + ``pytest.importorskip("Quartz")``; none is needed for the
logic coverage below.
"""

from __future__ import annotations

import sys
import types

import pytest

from jarvis.core.protocols import Observation, VisionSource
from jarvis.vision.ax_tree import AXTreeSource, _ax_flatten, _ax_point, _ax_size
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


def test_axvalue_manual_wrapper_contract_decodes_position_and_size(monkeypatch) -> None:
    class _GeneratedStruct:
        """PyObjC-style sequence slots without Sequence ABC registration."""

        def __init__(self, first: float, second: float) -> None:
            self._items = (first, second)

        def __len__(self) -> int:
            return 2

        def __getitem__(self, index: int) -> float:
            return self._items[index]

    position = object()
    size = object()
    module = types.SimpleNamespace(
        kAXValueCGPointType=1,
        kAXValueCGSizeType=2,
        AXValueGetType=lambda value: 1 if value is position else 2,
        AXValueGetValue=lambda value, kind, _out: (
            (True, _GeneratedStruct(120.5, -40.25)) if value is position and kind == 1
            else (True, _GeneratedStruct(800.0, 600.0)) if value is size and kind == 2
            else (False, None)
        ),
    )
    monkeypatch.setitem(sys.modules, "HIServices", module)

    assert _ax_point(position) == (120, -40)
    assert _ax_size(size) == (800, 600)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS PyObjC")
def test_real_pyobjc_axvalue_round_trip_without_tcc() -> None:
    """Exercise the actual manual wrapper on both macOS CI architectures."""
    import HIServices  # type: ignore[import-not-found]

    from jarvis.platform.macos_ax import decode_ax_point, decode_ax_size

    point_type = HIServices.kAXValueCGPointType
    size_type = HIServices.kAXValueCGSizeType
    position = HIServices.AXValueCreate(point_type, (120.5, -40.25))
    size = HIServices.AXValueCreate(size_type, (800.0, 600.0))

    point_ok, point = HIServices.AXValueGetValue(position, point_type, None)
    size_ok, dimensions = HIServices.AXValueGetValue(size, size_type, None)

    assert point_ok is True
    assert point == (120.5, -40.25)
    assert size_ok is True
    assert dimensions == (800.0, 600.0)
    assert decode_ax_point(position) == (120.5, -40.25)
    assert decode_ax_size(size) == (800.0, 600.0)
    assert _ax_point(position) == (120, -40)
    assert _ax_size(size) == (800, 600)


def test_ax_permission_uses_unified_runtime_identity_gate(monkeypatch) -> None:
    from jarvis.platform.permissions import PermissionId

    seen = []
    port = types.SimpleNamespace(
        runtime_access_granted=lambda permission: seen.append(permission) or False,
    )
    monkeypatch.setattr(
        "jarvis.platform.permissions.get_system_permission_port",
        lambda: port,
    )

    assert AXTreeSource._ax_is_process_trusted() is False
    assert seen == [PermissionId.ACCESSIBILITY]


def test_ax_permission_reprobes_live_revocation(monkeypatch) -> None:
    outcomes = iter((True, False))
    port = types.SimpleNamespace(
        runtime_access_granted=lambda _permission: next(outcomes),
    )
    monkeypatch.setattr(
        "jarvis.platform.permissions.get_system_permission_port",
        lambda: port,
    )

    assert AXTreeSource._ax_is_process_trusted() is True
    assert AXTreeSource._ax_is_process_trusted() is False


def test_secure_ax_field_redacts_value_and_preserves_focus() -> None:
    class _SecureField:
        values = {
            "AXRole": "AXTextField",
            "AXSubrole": "AXSecureTextField",
            "AXTitle": "Password",
            "AXValue": "must-not-leak",
            "AXPosition": {"x": 20, "y": 30},
            "AXSize": {"w": 200, "h": 24},
            "AXEnabled": True,
            "AXFocused": True,
            "AXChildren": [],
        }

        def copy_attribute_value(self, attribute):
            return self.values.get(attribute)

    nodes = []
    _ax_flatten(
        _SecureField(), depth=0, max_depth=2, parent_index=-1, out=nodes,
    )

    assert nodes[0].is_password is True
    assert nodes[0].focused is True
    assert nodes[0].value == ""
    assert "must-not-leak" not in nodes[0].name


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


# ---------------------------------------------------------------------------
# Traversal bounds: single walk per observe, deadline, raw-node cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_observe_walks_the_native_tree_exactly_once_despite_ladder() -> None:
    # Overflowing trees used to be re-walked per ladder rung (depths 6, 5, 4)
    # — 3x the AX IPC exactly when the tree is huge. The ladder now shrinks
    # the depth at prune time over ONE walk.
    calls: list[int] = []
    real = build_fake_ax_traverser(make_canned_ax_tree())

    def counting_traverser(depth: int, flt: str | None):
        calls.append(depth)
        return real(depth, flt)

    src = AXTreeSource(traverser=counting_traverser, permission_check=lambda: True)
    await src.observe()
    assert calls == [6]


def test_flatten_deadline_returns_partial_tree(monkeypatch) -> None:
    root = make_canned_ax_tree()
    clock = iter([100.0, 100.0, 999.0, 999.0, 999.0, 999.0, 999.0, 999.0])
    monkeypatch.setattr(
        "jarvis.vision.ax_tree.time.monotonic", lambda: next(clock, 999.0),
    )
    nodes: list = []
    _ax_flatten(
        root, depth=0, max_depth=6, parent_index=-1, out=nodes, deadline=100.5,
    )
    # The walk stopped early but what was collected is structurally valid:
    # parents precede children and the root survived.
    assert 1 <= len(nodes) < 5
    assert nodes[0].bounds == (0, 0, 800, 600)  # the root, flattened first


def test_flatten_max_nodes_cap_stops_collection() -> None:
    root = make_canned_ax_tree()
    nodes: list = []
    _ax_flatten(root, depth=0, max_depth=6, parent_index=-1, out=nodes, max_nodes=2)
    assert len(nodes) == 2


def test_truncated_walk_keeps_shallow_siblings(monkeypatch) -> None:
    # Breadth-first guarantee: a huge deep subtree visited "first" must not
    # starve shallow siblings (toolbar/address bar) when the node budget
    # truncates the walk — the depth-first version lost exactly those.
    def _leaf(i: int) -> FakeAXElement:
        return FakeAXElement(role="AXStaticText", title=f"deep-{i}",
                             position=(0, 0), size=(10, 10))

    huge_web_area = FakeAXElement(
        role="AXGroup", title="web-content", position=(0, 60), size=(800, 500),
        children=[
            FakeAXElement(
                role="AXGroup", title=f"row-{i}", position=(0, 60),
                size=(800, 20), children=[_leaf(i)],
            )
            for i in range(50)
        ],
    )
    toolbar_button = FakeAXElement(
        role="AXButton", title="Reload", position=(700, 10), size=(40, 30),
    )
    root = FakeAXElement(
        role="AXWindow", title="Browser", position=(0, 0), size=(800, 600),
        children=[huge_web_area, toolbar_button],
    )
    nodes: list = []
    _ax_flatten(
        root, depth=0, max_depth=6, parent_index=-1, out=nodes, max_nodes=10,
    )
    assert len(nodes) == 10
    assert any(n.name == "Reload" for n in nodes)
