"""MacAgentBench phase-zero readiness probe tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.cu.macos_readiness import probe_macos_readiness
from jarvis.platform.permissions import PermissionId, PermissionState


class _Port:
    def __init__(self, allowed: set[PermissionId]) -> None:
        self.allowed = allowed

    def runtime_access_granted(self, permission_id: PermissionId) -> bool:
        return permission_id in self.allowed

    def state(self, permission_id: PermissionId) -> PermissionState:
        return (
            PermissionState.GRANTED
            if permission_id in self.allowed
            else PermissionState.NOT_GRANTED
        )


class _Tree:
    async def observe(self):
        return SimpleNamespace(nodes=(object(), object()))


@pytest.mark.asyncio
async def test_ready_report_requires_all_native_layers() -> None:
    permissions = {
        PermissionId.SCREEN_RECORDING,
        PermissionId.ACCESSIBILITY,
        PermissionId.EVENT_POSTING,
    }
    report = await probe_macos_readiness(
        permission_port=_Port(permissions),
        tree_source=_Tree(),
        actuator_factory=lambda: SimpleNamespace(name="fake-macos"),
        platform="darwin",
    )

    assert report.ready is True
    by_id = {check.id: check for check in report.checks}
    assert by_id["semantic:ax-tree"].ok is True
    assert "2 nodes" in by_id["semantic:ax-tree"].detail
    assert by_id["actuation:backend"].ok is True


@pytest.mark.asyncio
async def test_missing_accessibility_skips_semantic_probe() -> None:
    permissions = {
        PermissionId.SCREEN_RECORDING,
        PermissionId.EVENT_POSTING,
    }

    class _MustNotObserve:
        async def observe(self):
            pytest.fail("AX tree must not be touched without Accessibility permission")

    report = await probe_macos_readiness(
        permission_port=_Port(permissions),
        tree_source=_MustNotObserve(),
        actuator_factory=lambda: SimpleNamespace(name="fake-macos"),
        platform="darwin",
    )

    assert report.ready is False
    by_id = {check.id: check for check in report.checks}
    assert by_id[f"permission:{PermissionId.ACCESSIBILITY}"].ok is False
    assert by_id["semantic:ax-tree"].ok is False


@pytest.mark.asyncio
async def test_missing_event_posting_skips_actuator_probe() -> None:
    permissions = {
        PermissionId.SCREEN_RECORDING,
        PermissionId.ACCESSIBILITY,
    }
    report = await probe_macos_readiness(
        permission_port=_Port(permissions),
        tree_source=_Tree(),
        actuator_factory=lambda: pytest.fail("actuator must not be created without Input Control"),
        platform="darwin",
    )

    assert report.ready is False
    by_id = {check.id: check for check in report.checks}
    assert by_id["actuation:backend"].ok is False


@pytest.mark.asyncio
async def test_non_macos_is_explicitly_not_applicable() -> None:
    report = await probe_macos_readiness(platform="linux")

    assert report.ready is False
    assert report.checks[0].id == "platform:macos"
    assert "only applicable on macOS" in report.checks[0].detail
