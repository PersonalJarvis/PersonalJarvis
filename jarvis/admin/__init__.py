"""Admin helper (Phase 5 Capability 3).

The main app stays `asInvoker`. Privileged operations are forwarded
over a named pipe (ADR-0001) to a separate, UAC-elevated helper process
that only accepts the fixed vocabulary defined in `schema.py`.

Implementations (AdminClient, named-pipe IPC, helper server) follow
in task 5.1-B.
"""
from __future__ import annotations

from .schema import (
    ADMIN_OPERATION_TYPES,
    DESTRUCTIVE_OPS,
    AddFirewallRuleOp,
    AddScheduledTaskOp,
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
    WriteRegistryHkcuOp,
    WriteRegistryHklmOp,
)

__all__ = [
    "AdminOperation",
    "AdminResponse",
    "InstallWingetOp",
    "UninstallWingetOp",
    "StartServiceOp",
    "StopServiceOp",
    "RemoveServiceOp",
    "AddFirewallRuleOp",
    "RemoveFirewallRuleOp",
    "ReadRegistryOp",
    "WriteRegistryHkcuOp",
    "WriteRegistryHklmOp",
    "AddScheduledTaskOp",
    "RemoveScheduledTaskOp",
    "WriteProtectedPathOp",
    "DESTRUCTIVE_OPS",
    "ADMIN_OPERATION_TYPES",
]
