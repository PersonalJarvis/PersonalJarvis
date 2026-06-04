"""Pydantic schema for all whitelisted admin operations.

The schema is the sole entry point for commands to the elevated helper —
no ``shell=True``, no free passthrough of shell strings (see mandate §Safety).

Destructive operations are listed in ``DESTRUCTIVE_OPS`` and require a
per-call user prompt, even at autonomy level ``trusted`` (mandate §6.2).

ADR-0001 describes the IPC wrapper (HMAC + nonce); this module covers only
the vocabulary.
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------

class _AdminOpBase(BaseModel):
    """Common fields shared by all admin ops."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    op_id: UUID = Field(default_factory=uuid4)
    # `type` is redefined as a Literal in subclasses — discriminator field.


# ---------------------------------------------------------------------
# Winget (install / uninstall software)
# ---------------------------------------------------------------------

class InstallWingetOp(_AdminOpBase):
    type: Literal["install_winget"] = "install_winget"
    package_id: str = Field(min_length=1, max_length=256,
                            pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,254}[A-Za-z0-9]$")
    version: str | None = None
    silent: bool = True


class UninstallWingetOp(_AdminOpBase):
    type: Literal["uninstall_winget"] = "uninstall_winget"
    package_id: str = Field(min_length=1, max_length=256,
                            pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,254}[A-Za-z0-9]$")


# ---------------------------------------------------------------------
# Services (start / stop / remove)
# ---------------------------------------------------------------------

_SERVICE_NAME = Field(min_length=1, max_length=256,
                      pattern=r"^[A-Za-z0-9_\-]{1,256}$")


class StartServiceOp(_AdminOpBase):
    type: Literal["start_service"] = "start_service"
    service: str = _SERVICE_NAME


class StopServiceOp(_AdminOpBase):
    type: Literal["stop_service"] = "stop_service"
    service: str = _SERVICE_NAME


class RemoveServiceOp(_AdminOpBase):
    type: Literal["remove_service"] = "remove_service"
    service: str = _SERVICE_NAME


# ---------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------

class AddFirewallRuleOp(_AdminOpBase):
    type: Literal["add_firewall_rule"] = "add_firewall_rule"
    # H6-Fix: rule name without spaces or quotes so that netsh does not
    # reinterpret the following `key=value` args as additional directives.
    name: str = Field(min_length=1, max_length=128,
                      pattern=r"^[A-Za-z0-9_\-.]+$")
    direction: Literal["inbound", "outbound"] = "inbound"
    action: Literal["allow", "block"] = "allow"
    protocol: Literal["TCP", "UDP", "any"] = "TCP"
    local_port: int | None = Field(default=None, ge=1, le=65535)
    remote_address: str | None = Field(
        default=None, max_length=64,
        pattern=r"^(?:[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?:/[0-9]{1,2})?"
                r"|[0-9A-Fa-f:]{2,39}(?:/[0-9]{1,3})?|any)$",
    )
    # H6-Fix: program path must be absolute, end with .exe, contain no spaces
    # or quotes. Pragmatically sufficient — anyone needing firewall rules for
    # paths with spaces must do so manually via schtasks/GUI.
    program: str | None = Field(
        default=None, max_length=260,
        pattern=r"^[A-Za-z]:\\(?:[A-Za-z0-9_\-.]+\\)*[A-Za-z0-9_\-.]+\.exe$",
    )


class RemoveFirewallRuleOp(_AdminOpBase):
    type: Literal["remove_firewall_rule"] = "remove_firewall_rule"
    name: str = Field(min_length=1, max_length=128,
                      pattern=r"^[A-Za-z0-9_\-.]+$")


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------

_RegistryHive = Literal["HKCU", "HKLM", "HKCR", "HKU", "HKCC"]
_RegistryValueType = Literal["REG_SZ", "REG_DWORD", "REG_QWORD",
                             "REG_EXPAND_SZ", "REG_MULTI_SZ", "REG_BINARY"]


class ReadRegistryOp(_AdminOpBase):
    type: Literal["read_registry"] = "read_registry"
    hive: _RegistryHive = "HKCU"
    key_path: str = Field(min_length=1, max_length=512)
    value_name: str | None = None               # None = default value


class WriteRegistryHkcuOp(_AdminOpBase):
    type: Literal["write_registry_hkcu"] = "write_registry_hkcu"
    key_path: str = Field(min_length=1, max_length=512)
    value_name: str
    value_type: _RegistryValueType = "REG_SZ"
    value_data: str | int | list[str] = ""


class WriteRegistryHklmOp(_AdminOpBase):
    """HKLM write is destructive (system-wide). User prompt is mandatory."""
    type: Literal["write_registry_hklm"] = "write_registry_hklm"
    key_path: str = Field(min_length=1, max_length=512)
    value_name: str
    value_type: _RegistryValueType = "REG_SZ"
    value_data: str | int | list[str] = ""


# ---------------------------------------------------------------------
# Scheduled Tasks
# ---------------------------------------------------------------------

class AddScheduledTaskOp(_AdminOpBase):
    type: Literal["add_scheduled_task"] = "add_scheduled_task"
    task_name: str = Field(min_length=1, max_length=128)
    schedule_xml: str = Field(max_length=8192)   # Task Scheduler XML definition
    run_as: Literal["current_user", "system"] = "current_user"


class RemoveScheduledTaskOp(_AdminOpBase):
    type: Literal["remove_scheduled_task"] = "remove_scheduled_task"
    task_name: str = Field(min_length=1, max_length=128)


# ---------------------------------------------------------------------
# Protected Paths
# ---------------------------------------------------------------------

class WriteProtectedPathOp(_AdminOpBase):
    """Writes to a path that is normally only writable with admin privileges
    (e.g. ``C:\\Program Files\\...``, ``C:\\Windows\\System32\\...``).
    """
    type: Literal["write_protected_path"] = "write_protected_path"
    path: str = Field(min_length=3, max_length=512)
    content_b64: str = Field(max_length=10_485_760)  # max 10 MB
    overwrite: bool = False


# ---------------------------------------------------------------------
# Discriminated Union + Metadata
# ---------------------------------------------------------------------
#
# Cross-platform note (AD-12): ``AdminOperation`` is a *platform-superset*
# discriminated union — it carries the Windows ops AND the macOS/Linux ops
# (defined in ``jarvis.admin.schema_unix``). A single helper process can decode
# any op regardless of host OS; the executor (`jarvis.admin.executor`) dispatches
# each op to the per-OS argv builder and raises a typed "unsupported on this OS"
# response if an op is sent to the wrong platform. ``WriteProtectedPathOp`` is
# shared verbatim across every OS (only the validated path strings differ).
#
# ``schema_unix`` imports ``_AdminOpBase`` + ``WriteProtectedPathOp`` from this
# module, so the import is one-directional (``schema_unix`` → ``schema``) and the
# fold-in happens here, at the bottom of the module, after the base classes are
# defined — no circular import.
from .schema_unix import (  # noqa: E402  (deliberate bottom import — see above)
    UNIX_ADMIN_OPERATION_TYPES,
    UNIX_DESTRUCTIVE_OPS,
    AptInstallOp,
    AptRemoveOp,
    BrewInstallOp,
    BrewRemoveOp,
    LaunchctlOp,
    SystemctlOp,
    UfwRemoveOp,
    UfwRuleOp,
)

AdminOperation = Annotated[
    (
        # --- Windows ops ---
        InstallWingetOp
        | UninstallWingetOp
        | StartServiceOp
        | StopServiceOp
        | RemoveServiceOp
        | AddFirewallRuleOp
        | RemoveFirewallRuleOp
        | ReadRegistryOp
        | WriteRegistryHkcuOp
        | WriteRegistryHklmOp
        | AddScheduledTaskOp
        | RemoveScheduledTaskOp
        # --- shared (every OS) ---
        | WriteProtectedPathOp
        # --- Linux ops ---
        | AptInstallOp
        | AptRemoveOp
        | SystemctlOp
        | UfwRuleOp
        | UfwRemoveOp
        # --- macOS ops ---
        | BrewInstallOp
        | BrewRemoveOp
        | LaunchctlOp
    ),
    Field(discriminator="type"),
]


# Windows-native op type strings (the original Phase-5 vocabulary).
_WINDOWS_ADMIN_OPERATION_TYPES: tuple[str, ...] = (
    "install_winget",
    "uninstall_winget",
    "start_service",
    "stop_service",
    "remove_service",
    "add_firewall_rule",
    "remove_firewall_rule",
    "read_registry",
    "write_registry_hkcu",
    "write_registry_hklm",
    "add_scheduled_task",
    "remove_scheduled_task",
)

# Shared op type strings (valid on every OS).
_SHARED_ADMIN_OPERATION_TYPES: tuple[str, ...] = (
    "write_protected_path",
)

# Platform-superset of every known op type string. The executor validates per OS
# at dispatch time; this tuple is the union the helper/decoder accepts.
ADMIN_OPERATION_TYPES: tuple[str, ...] = (
    *_WINDOWS_ADMIN_OPERATION_TYPES,
    *_SHARED_ADMIN_OPERATION_TYPES,
    *UNIX_ADMIN_OPERATION_TYPES,
)


DESTRUCTIVE_OPS: frozenset[str] = frozenset({
    # Mandate §6.2: per-action prompt, even at autonomy level `trusted`.
    # --- Windows ---
    "uninstall_winget",
    "remove_service",
    "remove_firewall_rule",
    "write_registry_hklm",
    "remove_scheduled_task",
    # --- shared (every OS) ---
    "write_protected_path",
}) | UNIX_DESTRUCTIVE_OPS  # apt_remove, brew_remove, ufw_remove, systemctl, launchctl


class AdminResponse(BaseModel):
    """Response from the helper. No shell stdout passthrough — only structured
    fields, so that no ANSI codes or newlines can upset the IPC parser.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    op_id: UUID
    success: bool
    error_code: str | None = None               # e.g. "winget_not_found"
    error_message: str | None = None
    result: dict[str, str | int | bool | list[str]] = Field(default_factory=dict)
    duration_ms: int = 0
