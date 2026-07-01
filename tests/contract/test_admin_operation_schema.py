"""Contract tests for the AdminOperation Pydantic schema.

The schema is the only entry door into the elevated helper —
if a test breaks here, the IPC pipeline is compromised.
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from jarvis.admin import (
    ADMIN_OPERATION_TYPES,
    DESTRUCTIVE_OPS,
    AdminOperation,
    AdminResponse,
    InstallWingetOp,
    RemoveFirewallRuleOp,
    RemoveScheduledTaskOp,
    UninstallWingetOp,
    WriteRegistryHklmOp,
)

_ADAPTER: TypeAdapter[AdminOperation] = TypeAdapter(AdminOperation)


# The original Windows op vocabulary (13 ops).
_WINDOWS_OPS = {
    "install_winget", "uninstall_winget",
    "start_service", "stop_service", "remove_service",
    "add_firewall_rule", "remove_firewall_rule",
    "read_registry", "write_registry_hkcu", "write_registry_hklm",
    "add_scheduled_task", "remove_scheduled_task",
    "write_protected_path",
}
# The macOS + Linux op vocabulary added by the cross-platform port (Wave 3,
# AD-12). schema.py is now a platform superset that the executor dispatches
# per-OS; this stays a drift guard — any accidental add/typo still breaks it.
_UNIX_OPS = {
    "apt_install", "apt_remove", "ufw_rule", "ufw_remove", "systemctl",
    "brew_install", "brew_remove", "launchctl",
}


def test_all_op_types_registered():
    assert set(ADMIN_OPERATION_TYPES) == _WINDOWS_OPS | _UNIX_OPS
    assert len(ADMIN_OPERATION_TYPES) == 21


def test_windows_op_vocabulary_intact():
    # AD-7: the original Windows ops must remain present and unchanged.
    assert _WINDOWS_OPS <= set(ADMIN_OPERATION_TYPES)


def test_destructive_ops_cover_mandate_list():
    # Mandat §6.2 listet diese fuenf explizit als destruktiv-mit-Prompt:
    expected = {"uninstall_winget", "remove_service", "remove_firewall_rule",
                "write_registry_hklm", "write_protected_path"}
    assert expected <= DESTRUCTIVE_OPS


def test_destructive_ops_subset_of_registered():
    assert DESTRUCTIVE_OPS <= set(ADMIN_OPERATION_TYPES)


def test_install_winget_happy_path():
    op = _ADAPTER.validate_python({
        "type": "install_winget", "package_id": "7zip.7zip",
    })
    assert isinstance(op, InstallWingetOp)
    assert op.package_id == "7zip.7zip"
    assert op.silent is True       # Default


def test_install_winget_rejects_shell_injection_in_package_id():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({
            "type": "install_winget",
            "package_id": "7zip; rm -rf /",
        })


def test_unknown_op_type_rejected():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"type": "format_drive", "drive": "C:"})


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({
            "type": "install_winget",
            "package_id": "7zip.7zip",
            "evil_payload": "...",
        })


def test_destructive_subclasses_destructive_flag():
    """Sanity: every DESTRUCTIVE_OPS subclass has an existing op type,
    so a typo can't bypass the prompt requirement.
    """
    for op_type in DESTRUCTIVE_OPS:
        assert op_type in ADMIN_OPERATION_TYPES


def test_write_registry_hklm_requires_all_fields():
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({
            "type": "write_registry_hklm",
            "key_path": "SOFTWARE\\Example",
            # value_name fehlt
        })


def test_uninstall_winget_round_trip():
    src = {"type": "uninstall_winget", "package_id": "7zip.7zip"}
    op = _ADAPTER.validate_python(src)
    assert isinstance(op, UninstallWingetOp)
    dumped = op.model_dump(mode="json")
    assert dumped["type"] == "uninstall_winget"
    assert dumped["package_id"] == "7zip.7zip"


def test_write_protected_path_size_limit():
    # 10 MB upper bound for content_b64
    huge = "A" * (10_485_761)
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({
            "type": "write_protected_path",
            "path": "C:\\ProgramData\\jarvis\\test.txt",
            "content_b64": huge,
        })


def test_remove_scheduled_task_and_remove_firewall_rule_minimal():
    op1 = _ADAPTER.validate_python({
        "type": "remove_scheduled_task", "task_name": "MorningReport",
    })
    op2 = _ADAPTER.validate_python({
        "type": "remove_firewall_rule", "name": "Block-Zoom-UDP",
    })
    assert isinstance(op1, RemoveScheduledTaskOp)
    assert isinstance(op2, RemoveFirewallRuleOp)


def test_admin_response_roundtrip():
    resp = AdminResponse(
        op_id=UninstallWingetOp(package_id="7zip.7zip").op_id,
        success=True, duration_ms=4500,
        result={"uninstalled": "7zip.7zip"},
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["success"] is True
    assert dumped["duration_ms"] == 4500


def test_write_registry_hklm_flagged_destructive():
    op = _ADAPTER.validate_python({
        "type": "write_registry_hklm",
        "key_path": "SOFTWARE\\Policies\\Example",
        "value_name": "Enabled",
        "value_type": "REG_DWORD",
        "value_data": 1,
    })
    assert isinstance(op, WriteRegistryHklmOp)
    assert op.type in DESTRUCTIVE_OPS
