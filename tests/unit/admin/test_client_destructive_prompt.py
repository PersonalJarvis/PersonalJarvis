"""AdminClient: destructive ops trigger DestructiveRequiresApproval.

We use a FakePipeClient pattern — no real named-pipe traffic,
just the dispatch logic.
"""
from __future__ import annotations

import pytest

from jarvis.admin.client import AdminClient, DestructiveRequiresApproval
from jarvis.admin.schema import (
    ADMIN_OPERATION_TYPES,
    DESTRUCTIVE_OPS,
    AddFirewallRuleOp,
    AdminOperation,
    AdminResponse,
    InstallWingetOp,
    ReadRegistryOp,
    RemoveFirewallRuleOp,
    RemoveScheduledTaskOp,
    RemoveServiceOp,
    StartServiceOp,
    StopServiceOp,
    UninstallWingetOp,
    WriteProtectedPathOp,
    WriteRegistryHklmOp,
)
from jarvis.control.cancel import CancelToken
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AdminOperationCompleted,
    AdminOperationRejected,
    AdminOperationRequested,
)


class _FakePipeClient:
    """Minimal fake implementation. Responds with success=True."""

    def __init__(self) -> None:
        self.sends: list[AdminOperation] = []

    async def send(self, op: AdminOperation) -> AdminResponse:
        self.sends.append(op)
        return AdminResponse(op_id=op.op_id, success=True, duration_ms=10)


def _make_client(bus: EventBus | None = None,
                 cancel_token: CancelToken | None = None,
                 fake: _FakePipeClient | None = None) -> tuple[AdminClient, _FakePipeClient]:
    fake = fake or _FakePipeClient()
    client = AdminClient(bus=bus, cancel_token=cancel_token, pipe_client=fake)
    return client, fake


def _representative(op_type: str) -> AdminOperation:
    """Builds a valid op instance for the given op type."""
    match op_type:
        case "install_winget":
            return InstallWingetOp(package_id="7zip.7zip")
        case "uninstall_winget":
            return UninstallWingetOp(package_id="7zip.7zip")
        case "start_service":
            return StartServiceOp(service="Themes")
        case "stop_service":
            return StopServiceOp(service="Themes")
        case "remove_service":
            return RemoveServiceOp(service="Themes")
        case "add_firewall_rule":
            return AddFirewallRuleOp(name="TestRule", local_port=8080)
        case "remove_firewall_rule":
            return RemoveFirewallRuleOp(name="TestRule")
        case "read_registry":
            return ReadRegistryOp(hive="HKCU", key_path="Environment")
        case "write_registry_hkcu":
            from jarvis.admin.schema import WriteRegistryHkcuOp
            return WriteRegistryHkcuOp(
                key_path="Software\\Jarvis", value_name="Test",
                value_type="REG_SZ", value_data="x",
            )
        case "write_registry_hklm":
            return WriteRegistryHklmOp(
                key_path="SOFTWARE\\Jarvis", value_name="Test",
                value_type="REG_SZ", value_data="x",
            )
        case "add_scheduled_task":
            from jarvis.admin.schema import AddScheduledTaskOp
            return AddScheduledTaskOp(task_name="Foo", schedule_xml="<xml/>")
        case "remove_scheduled_task":
            return RemoveScheduledTaskOp(task_name="Foo")
        case "write_protected_path":
            return WriteProtectedPathOp(
                path="C:\\ProgramData\\jarvis\\a.txt", content_b64="YQ==",
            )
        # --- cross-platform (Linux) ops ---
        case "apt_install":
            from jarvis.admin.schema_unix import AptInstallOp
            return AptInstallOp(package="git")
        case "apt_remove":
            from jarvis.admin.schema_unix import AptRemoveOp
            return AptRemoveOp(package="git")
        case "systemctl":
            from jarvis.admin.schema_unix import SystemctlOp
            return SystemctlOp(action="restart", unit="nginx.service")
        case "ufw_rule":
            from jarvis.admin.schema_unix import UfwRuleOp
            return UfwRuleOp(action="allow", port=8080, proto="tcp")
        case "ufw_remove":
            from jarvis.admin.schema_unix import UfwRemoveOp
            return UfwRemoveOp(action="allow", port=8080, proto="tcp")
        # --- cross-platform (macOS) ops ---
        case "brew_install":
            from jarvis.admin.schema_unix import BrewInstallOp
            return BrewInstallOp(formula="wget")
        case "brew_remove":
            from jarvis.admin.schema_unix import BrewRemoveOp
            return BrewRemoveOp(formula="wget")
        case "launchctl":
            from jarvis.admin.schema_unix import LaunchctlOp
            return LaunchctlOp(action="unload", label="com.apple.Spotlight")
        case _:
            raise ValueError(f"Unknown op type: {op_type}")


@pytest.mark.asyncio
@pytest.mark.parametrize("op_type", sorted(DESTRUCTIVE_OPS))
async def test_destructive_ops_raise_without_approval(op_type):
    client, fake = _make_client()
    op = _representative(op_type)
    with pytest.raises(DestructiveRequiresApproval) as exc:
        await client.execute(op)
    assert exc.value.op_type == op_type
    # The fake pipe must NOT have been called.
    assert fake.sends == []


@pytest.mark.asyncio
@pytest.mark.parametrize("op_type", sorted(DESTRUCTIVE_OPS))
async def test_destructive_ops_pass_with_approval(op_type):
    client, fake = _make_client()
    op = _representative(op_type)
    resp = await client.execute(op, destructive_approved=True)
    assert resp.success is True
    assert len(fake.sends) == 1
    assert fake.sends[0] is op


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op_type", sorted(set(ADMIN_OPERATION_TYPES) - DESTRUCTIVE_OPS)
)
async def test_non_destructive_ops_pass_without_approval(op_type):
    client, fake = _make_client()
    op = _representative(op_type)
    resp = await client.execute(op)
    assert resp.success is True
    assert len(fake.sends) == 1


@pytest.mark.asyncio
async def test_cancelled_token_short_circuits():
    tok = CancelToken()
    tok.cancel("kill_switch")
    client, fake = _make_client(cancel_token=tok)
    resp = await client.execute(InstallWingetOp(package_id="7zip.7zip"))
    assert resp.success is False
    assert resp.error_code == "cancelled"
    assert fake.sends == []


@pytest.mark.asyncio
async def test_bus_events_requested_and_completed():
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe(AdminOperationRequested, _appender(seen))
    bus.subscribe(AdminOperationCompleted, _appender(seen))
    bus.subscribe(AdminOperationRejected, _appender(seen))

    client, _fake = _make_client(bus=bus)
    await client.execute(InstallWingetOp(package_id="7zip.7zip"))
    assert any(isinstance(e, AdminOperationRequested) for e in seen)
    assert any(isinstance(e, AdminOperationCompleted) for e in seen)


@pytest.mark.asyncio
async def test_bus_events_rejected_on_destructive():
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe(AdminOperationRejected, _appender(seen))

    client, _fake = _make_client(bus=bus)
    op = UninstallWingetOp(package_id="7zip.7zip")
    with pytest.raises(DestructiveRequiresApproval):
        await client.execute(op)
    assert any(
        isinstance(e, AdminOperationRejected)
        and e.reason == "destructive_requires_approval"
        for e in seen
    )


def _appender(sink: list[object]):
    async def _h(e):
        sink.append(e)
    return _h
