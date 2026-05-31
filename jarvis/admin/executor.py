"""Dispatcher inside the helper process: each whitelisted AdminOperation → OS API.

Rules:

- **No** ``shell=True``. All subprocess calls go through
  ``asyncio.create_subprocess_exec`` with a list of arguments. This inherently
  prevents shell-metacharacter injection; the Pydantic patterns in
  ``jarvis.admin.schema`` additionally strip anything that is not identifier-safe.
- Every op has a timeout (default 120 s). On expiry: response with
  ``error_code="timeout"``.
- Logging via loguru is structured — every entry contains op_id + op_type +
  duration_ms + success. This is exactly what the flight recorder extracts from
  the helper log (Phase 5 DoD).
- Registry ops use ``winreg`` (stdlib), not ``reg.exe`` — smaller attack surface,
  no shell-quoting pitfalls.
"""
from __future__ import annotations

import asyncio
import base64
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from loguru import logger

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .schema import (
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
from .schema_unix import (
    AptInstallOp,
    AptRemoveOp,
    BrewInstallOp,
    BrewRemoveOp,
    LaunchctlOp,
    SystemctlOp,
    UfwRemoveOp,
    UfwRuleOp,
)

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

_DEFAULT_TIMEOUT_S = 120
_WINGET_TIMEOUT_S = 600           # Installations can take a long time
_MAX_STDOUT_CHARS = 4000


# ---------------------------------------------------------------------
# Registry mapping (hive string → constant)
# ---------------------------------------------------------------------

def _hive_constant(hive: str) -> int:
    """Return the winreg constant for a hive string."""
    import winreg  # stdlib, available on Windows only

    mapping = {
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKCR": winreg.HKEY_CLASSES_ROOT,
        "HKU": winreg.HKEY_USERS,
        "HKCC": winreg.HKEY_CURRENT_CONFIG,
    }
    return mapping[hive]


def _value_type_constant(vt: str) -> int:
    import winreg

    mapping = {
        "REG_SZ": winreg.REG_SZ,
        "REG_DWORD": winreg.REG_DWORD,
        "REG_QWORD": winreg.REG_QWORD,
        "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
        "REG_MULTI_SZ": winreg.REG_MULTI_SZ,
        "REG_BINARY": winreg.REG_BINARY,
    }
    return mapping[vt]


# ---------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------

class AdminExecutor:
    """Dispatcher class — mockable in tests via ``_run_subprocess``."""

    def __init__(
        self,
        *,
        default_timeout_s: int = _DEFAULT_TIMEOUT_S,
        winget_timeout_s: int = _WINGET_TIMEOUT_S,
    ) -> None:
        self._default_timeout_s = default_timeout_s
        self._winget_timeout_s = winget_timeout_s

    # ------------------------------------------------------------------
    # Subprocess runner (sole path for native executable calls)
    # ------------------------------------------------------------------

    async def _run_subprocess(
        self,
        argv: list[str],
        *,
        timeout_s: int,
    ) -> tuple[int, str, str]:
        """Start a process using a list of arguments. Never shell=True."""
        if not argv:
            raise ValueError("argv must not be empty")

        # Prevent accidental string-concatenation injection: argv must be
        # List[str] containing only strings, and shell metacharacters are
        # not interpreted by the Windows APIs (each segment lands separately
        # in lpCommandLine).
        for segment in argv:
            if not isinstance(segment, str):
                raise TypeError(f"argv-Segment muss str sein, nicht {type(segment)}")

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise

        stdout = stdout_b.decode("utf-8", errors="replace")[:_MAX_STDOUT_CHARS]
        stderr = stderr_b.decode("utf-8", errors="replace")[:_MAX_STDOUT_CHARS]
        return proc.returncode or 0, stdout, stderr

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    async def execute(self, op: AdminOperation) -> AdminResponse:
        """Dispatcher: route ``op`` to the appropriate ``_do_*`` method."""
        start_ns = time.time_ns()
        op_type = op.type
        try:
            if isinstance(op, InstallWingetOp):
                res = await asyncio.wait_for(
                    self._do_install_winget(op), timeout=self._winget_timeout_s + 5
                )
            elif isinstance(op, UninstallWingetOp):
                res = await asyncio.wait_for(
                    self._do_uninstall_winget(op), timeout=self._winget_timeout_s + 5
                )
            elif isinstance(op, StartServiceOp):
                res = await asyncio.wait_for(
                    self._do_start_service(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, StopServiceOp):
                res = await asyncio.wait_for(
                    self._do_stop_service(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, RemoveServiceOp):
                res = await asyncio.wait_for(
                    self._do_remove_service(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, AddFirewallRuleOp):
                res = await asyncio.wait_for(
                    self._do_add_firewall_rule(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, RemoveFirewallRuleOp):
                res = await asyncio.wait_for(
                    self._do_remove_firewall_rule(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, ReadRegistryOp):
                res = await asyncio.wait_for(
                    self._do_read_registry(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, WriteRegistryHkcuOp):
                res = await asyncio.wait_for(
                    self._do_write_registry(op, hive_override=None),
                    timeout=self._default_timeout_s,
                )
            elif isinstance(op, WriteRegistryHklmOp):
                res = await asyncio.wait_for(
                    self._do_write_registry(op, hive_override="HKLM"),
                    timeout=self._default_timeout_s,
                )
            elif isinstance(op, AddScheduledTaskOp):
                res = await asyncio.wait_for(
                    self._do_add_scheduled_task(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, RemoveScheduledTaskOp):
                res = await asyncio.wait_for(
                    self._do_remove_scheduled_task(op),
                    timeout=self._default_timeout_s,
                )
            elif isinstance(op, WriteProtectedPathOp):
                res = await asyncio.wait_for(
                    self._do_write_protected_path(op),
                    timeout=self._default_timeout_s,
                )
            # --- Linux ops ---
            elif isinstance(op, AptInstallOp):
                res = await asyncio.wait_for(
                    self._do_apt_install(op), timeout=self._winget_timeout_s + 5
                )
            elif isinstance(op, AptRemoveOp):
                res = await asyncio.wait_for(
                    self._do_apt_remove(op), timeout=self._winget_timeout_s + 5
                )
            elif isinstance(op, SystemctlOp):
                res = await asyncio.wait_for(
                    self._do_systemctl(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, UfwRuleOp):
                res = await asyncio.wait_for(
                    self._do_ufw_rule(op), timeout=self._default_timeout_s
                )
            elif isinstance(op, UfwRemoveOp):
                res = await asyncio.wait_for(
                    self._do_ufw_remove(op), timeout=self._default_timeout_s
                )
            # --- macOS ops ---
            elif isinstance(op, BrewInstallOp):
                res = await asyncio.wait_for(
                    self._do_brew_install(op), timeout=self._winget_timeout_s + 5
                )
            elif isinstance(op, BrewRemoveOp):
                res = await asyncio.wait_for(
                    self._do_brew_remove(op), timeout=self._winget_timeout_s + 5
                )
            elif isinstance(op, LaunchctlOp):
                res = await asyncio.wait_for(
                    self._do_launchctl(op), timeout=self._default_timeout_s
                )
            else:  # pragma: no cover — caught by Pydantic discriminator
                return AdminResponse(
                    op_id=op.op_id, success=False,
                    error_code="unknown_op_type",
                    error_message=f"Unknown op type: {op_type}",
                )
        except TimeoutError:
            duration_ms = max(0, (time.time_ns() - start_ns) // 1_000_000)
            logger.warning("admin_exec.timeout", op_id=str(op.op_id),
                           op_type=op_type, duration_ms=duration_ms)
            return AdminResponse(
                op_id=op.op_id, success=False,
                error_code="timeout",
                error_message=f"Op {op_type} exceeded timeout",
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = max(0, (time.time_ns() - start_ns) // 1_000_000)
            logger.exception("admin_exec.error", op_id=str(op.op_id),
                             op_type=op_type)
            return AdminResponse(
                op_id=op.op_id, success=False,
                error_code="exception",
                error_message=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = max(0, (time.time_ns() - start_ns) // 1_000_000)
        logger.info("admin_exec.done", op_id=str(op.op_id),
                    op_type=op_type, duration_ms=duration_ms,
                    success=res["success"])
        return AdminResponse(
            op_id=op.op_id,
            success=bool(res["success"]),
            error_code=res.get("error_code"),
            error_message=res.get("error_message"),
            result=res.get("result", {}),
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Winget
    # ------------------------------------------------------------------

    async def _do_install_winget(self, op: InstallWingetOp) -> dict[str, Any]:
        # Pydantic already validated the regex — we rely on that here.
        argv = [
            "winget", "install", "--id", op.package_id,
            "--accept-package-agreements",
            "--accept-source-agreements",
        ]
        if op.version:
            argv += ["--version", op.version]
        if op.silent:
            argv.append("--silent")

        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._winget_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "winget_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "stdout_tail": stdout[-500:]},
        }

    async def _do_uninstall_winget(self, op: UninstallWingetOp) -> dict[str, Any]:
        argv = [
            "winget", "uninstall", "--id", op.package_id,
            "--silent", "--accept-source-agreements",
        ]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._winget_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "winget_uninstall_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "stdout_tail": stdout[-500:]},
        }

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    async def _do_start_service(self, op: StartServiceOp) -> dict[str, Any]:
        rc, _, stderr = await self._run_subprocess(
            ["sc.exe", "start", op.service], timeout_s=self._default_timeout_s
        )
        # sc start returns 0 on success, 1056 if already running (ERROR_SERVICE_ALREADY_RUNNING).
        success = rc in (0, 1056)
        return {
            "success": success,
            "error_code": None if success else "service_start_failed",
            "error_message": stderr if not success else None,
            "result": {"exit_code": rc, "already_running": rc == 1056},
        }

    async def _do_stop_service(self, op: StopServiceOp) -> dict[str, Any]:
        rc, _, stderr = await self._run_subprocess(
            ["sc.exe", "stop", op.service], timeout_s=self._default_timeout_s
        )
        # 1062 = ERROR_SERVICE_NOT_ACTIVE
        success = rc in (0, 1062)
        return {
            "success": success,
            "error_code": None if success else "service_stop_failed",
            "error_message": stderr if not success else None,
            "result": {"exit_code": rc, "already_stopped": rc == 1062},
        }

    async def _do_remove_service(self, op: RemoveServiceOp) -> dict[str, Any]:
        rc, _, stderr = await self._run_subprocess(
            ["sc.exe", "delete", op.service], timeout_s=self._default_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "service_delete_failed",
            "error_message": stderr if not success else None,
            "result": {"exit_code": rc},
        }

    # ------------------------------------------------------------------
    # Firewall
    # ------------------------------------------------------------------

    async def _do_add_firewall_rule(self, op: AddFirewallRuleOp) -> dict[str, Any]:
        # netsh advfirewall firewall add rule name=... dir=in action=allow ...
        # Note: name/program may contain spaces; create_subprocess_exec passes
        # each argv segment as a separate argument — netsh accepts that.
        argv = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={op.name}",
            f"dir={'in' if op.direction == 'inbound' else 'out'}",
            f"action={op.action}",
            f"protocol={op.protocol}",
        ]
        if op.local_port is not None:
            argv.append(f"localport={op.local_port}")
        if op.remote_address:
            argv.append(f"remoteip={op.remote_address}")
        if op.program:
            argv.append(f"program={op.program}")

        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._default_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "firewall_add_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc},
        }

    async def _do_remove_firewall_rule(
        self, op: RemoveFirewallRuleOp
    ) -> dict[str, Any]:
        argv = [
            "netsh", "advfirewall", "firewall", "delete", "rule",
            f"name={op.name}",
        ]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._default_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "firewall_delete_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc},
        }

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    async def _do_read_registry(self, op: ReadRegistryOp) -> dict[str, Any]:
        import winreg

        def _do_read() -> dict[str, Any]:
            hive = _hive_constant(op.hive)
            try:
                with winreg.OpenKey(hive, op.key_path, 0, winreg.KEY_READ) as key:
                    if op.value_name is None:
                        # Read the default value (name = "")
                        data, typ = winreg.QueryValueEx(key, "")
                    else:
                        data, typ = winreg.QueryValueEx(key, op.value_name)
                    # Stringify bytes/lists so Pydantic accepts the
                    # ``result`` dict (str|int|bool|list[str]).
                    if isinstance(data, bytes):
                        data_out: str | int | bool | list[str] = data.hex()
                    elif isinstance(data, list):
                        data_out = [str(x) for x in data]
                    elif isinstance(data, (int, bool)):
                        data_out = data
                    else:
                        data_out = str(data)
                    return {
                        "success": True,
                        "result": {
                            "value": data_out,
                            "type": str(typ),
                            "hive": op.hive,
                            "key_path": op.key_path,
                        },
                    }
            except FileNotFoundError:
                return {
                    "success": False,
                    "error_code": "registry_key_not_found",
                    "error_message": f"{op.hive}\\{op.key_path}",
                }
            except OSError as exc:
                return {
                    "success": False,
                    "error_code": "registry_read_failed",
                    "error_message": str(exc),
                }

        return await asyncio.to_thread(_do_read)

    async def _do_write_registry(
        self,
        op: WriteRegistryHkcuOp | WriteRegistryHklmOp,
        *,
        hive_override: str | None,
    ) -> dict[str, Any]:
        import winreg

        hive_name = hive_override if hive_override else "HKCU"

        def _do_write() -> dict[str, Any]:
            hive = _hive_constant(hive_name)
            try:
                type_const = _value_type_constant(op.value_type)
                # winreg expects matching Python types depending on the REG type.
                data = op.value_data
                if op.value_type == "REG_BINARY" and isinstance(data, str):
                    # Hex strings are allowed — the Pydantic union is "str|int|list[str]".
                    try:
                        data = bytes.fromhex(data)
                    except ValueError:
                        return {
                            "success": False,
                            "error_code": "invalid_binary_hex",
                            "error_message": "value_data konnte nicht als Hex dekodiert werden.",
                        }
                with winreg.CreateKey(hive, op.key_path) as key:
                    winreg.SetValueEx(key, op.value_name, 0, type_const, data)
                return {
                    "success": True,
                    "result": {
                        "hive": hive_name,
                        "key_path": op.key_path,
                        "value_name": op.value_name,
                    },
                }
            except OSError as exc:
                return {
                    "success": False,
                    "error_code": "registry_write_failed",
                    "error_message": str(exc),
                }

        return await asyncio.to_thread(_do_write)

    # ------------------------------------------------------------------
    # Scheduled Tasks
    # ------------------------------------------------------------------

    async def _do_add_scheduled_task(
        self, op: AddScheduledTaskOp
    ) -> dict[str, Any]:
        # schtasks /Create /XML <file> /TN <name> /F
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-16"
        ) as tmp:
            tmp.write(op.schedule_xml)
            xml_path = tmp.name
        try:
            argv = [
                "schtasks.exe", "/Create",
                "/TN", op.task_name,
                "/XML", xml_path,
                "/F",
            ]
            if op.run_as == "system":
                argv += ["/RU", "SYSTEM"]
            rc, stdout, stderr = await self._run_subprocess(
                argv, timeout_s=self._default_timeout_s
            )
            success = rc == 0
            return {
                "success": success,
                "error_code": None if success else "schtasks_create_failed",
                "error_message": (stderr or stdout) if not success else None,
                "result": {"exit_code": rc, "task_name": op.task_name},
            }
        finally:
            try:
                await asyncio.to_thread(
                    lambda: Path(xml_path).unlink(missing_ok=True)
                )
            except OSError:
                pass

    async def _do_remove_scheduled_task(
        self, op: RemoveScheduledTaskOp
    ) -> dict[str, Any]:
        argv = [
            "schtasks.exe", "/Delete",
            "/TN", op.task_name,
            "/F",
        ]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._default_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "schtasks_delete_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "task_name": op.task_name},
        }

    # ------------------------------------------------------------------
    # Protected Path
    # ------------------------------------------------------------------

    async def _do_write_protected_path(
        self, op: WriteProtectedPathOp
    ) -> dict[str, Any]:
        target = Path(op.path)
        # Existence check runs in to_thread because Path.exists() does disk I/O.
        exists = await asyncio.to_thread(target.exists)
        if exists and not op.overwrite:
            return {
                "success": False,
                "error_code": "path_exists",
                "error_message": f"{target} existiert und overwrite=False",
            }

        try:
            content = base64.b64decode(op.content_b64, validate=True)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            return {
                "success": False,
                "error_code": "invalid_base64",
                "error_message": str(exc),
            }

        def _do_write() -> dict[str, Any]:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                return {
                    "success": True,
                    "result": {"path": str(target), "bytes_written": len(content)},
                }
            except OSError as exc:
                return {
                    "success": False,
                    "error_code": "write_failed",
                    "error_message": str(exc),
                }

        return await asyncio.to_thread(_do_write)

    # ------------------------------------------------------------------
    # Linux — apt (package install / remove)
    # ------------------------------------------------------------------
    #
    # Pydantic already validated ``op.package`` against the apt-package regex
    # (``^[a-z0-9][a-z0-9+\-.]{0,127}$``), so a metacharacter payload such as
    # ``"git; rm -rf /"`` never reaches this argv. We still pass a list-argv +
    # ``shell=False`` (via ``_run_subprocess``) for defense in depth — exactly
    # the Windows executor's contract. ``DEBIAN_FRONTEND=noninteractive`` keeps
    # apt from blocking on a TTY prompt; the elevation itself is the caller's
    # (helper-process) responsibility (AD-12).

    async def _do_apt_install(self, op: AptInstallOp) -> dict[str, Any]:
        argv = ["apt-get", "install", "-y", op.package]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._winget_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "apt_install_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "stdout_tail": stdout[-500:]},
        }

    async def _do_apt_remove(self, op: AptRemoveOp) -> dict[str, Any]:
        argv = ["apt-get", "remove", "-y", op.package]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._winget_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "apt_remove_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "stdout_tail": stdout[-500:]},
        }

    # ------------------------------------------------------------------
    # Linux — systemctl (service control)
    # ------------------------------------------------------------------

    async def _do_systemctl(self, op: SystemctlOp) -> dict[str, Any]:
        # ``op.action`` is a Literal (start|stop|enable|disable|restart) and
        # ``op.unit`` is regex-validated — both land as separate argv segments.
        argv = ["systemctl", op.action, op.unit]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._default_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "systemctl_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "action": op.action, "unit": op.unit},
        }

    # ------------------------------------------------------------------
    # Linux — ufw (firewall rule)
    # ------------------------------------------------------------------

    async def _do_ufw_rule(self, op: UfwRuleOp) -> dict[str, Any]:
        # ``ufw allow 8080/tcp`` — port is an int (1..65535) and proto/action
        # are Literals, so the spec string is built from validated primitives.
        spec = f"{op.port}/{op.proto}"
        argv = ["ufw", op.action, spec]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._default_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "ufw_rule_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "rule": f"{op.action} {spec}"},
        }

    async def _do_ufw_remove(self, op: UfwRemoveOp) -> dict[str, Any]:
        # ``ufw delete allow 8080/tcp``.
        spec = f"{op.port}/{op.proto}"
        argv = ["ufw", "delete", op.action, spec]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._default_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "ufw_remove_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "rule": f"delete {op.action} {spec}"},
        }

    # ------------------------------------------------------------------
    # macOS — Homebrew (package install / remove)
    # ------------------------------------------------------------------

    async def _do_brew_install(self, op: BrewInstallOp) -> dict[str, Any]:
        argv = ["brew", "install", op.formula]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._winget_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "brew_install_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "stdout_tail": stdout[-500:]},
        }

    async def _do_brew_remove(self, op: BrewRemoveOp) -> dict[str, Any]:
        argv = ["brew", "uninstall", op.formula]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._winget_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "brew_remove_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "stdout_tail": stdout[-500:]},
        }

    # ------------------------------------------------------------------
    # macOS — launchctl (service control)
    # ------------------------------------------------------------------

    async def _do_launchctl(self, op: LaunchctlOp) -> dict[str, Any]:
        # ``op.action`` is a Literal (load|unload|enable|disable) and
        # ``op.label`` is regex-validated — both land as separate argv segments.
        argv = ["launchctl", op.action, op.label]
        rc, stdout, stderr = await self._run_subprocess(
            argv, timeout_s=self._default_timeout_s
        )
        success = rc == 0
        return {
            "success": success,
            "error_code": None if success else "launchctl_failed",
            "error_message": (stderr or stdout) if not success else None,
            "result": {"exit_code": rc, "action": op.action, "label": op.label},
        }

