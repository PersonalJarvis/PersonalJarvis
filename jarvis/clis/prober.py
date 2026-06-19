"""``CliStatusProber`` — checks binary installation and auth status of a CLI.

Two independent checks:

1. **Binary check**: ``shutil.which(binary_name)`` + invoke ``check_command`` +
   match ``version_parse_regex``. Returns ``(installed, version, binary_path)``.
2. **Auth check**: when ``auth.type != "none"``, ``auth.status_command`` is
   executed and routed through one of the 10 ``StatusParseStrategy`` functions.

Both calls have tight timeouts (10s / 15s) because CLIs tend to hang. On
timeout → ``unknown``, never an exception to the caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from asyncio.subprocess import PIPE
from typing import Literal

from jarvis.clis.spec import CliSpec, CliStatus, StatusParseStrategy
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS, resolve_executable

log = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 10.0
AUTH_TIMEOUT_S = 15.0


class CliStatusProber:
    async def probe(self, spec: CliSpec) -> CliStatus:
        installed, version, binary_path = await self._probe_binary(spec)
        if not installed:
            return CliStatus(
                installed=False,
                version=None,
                binary_path=None,
                auth_status="unknown",
            )
        auth_status = await self._probe_auth(spec)
        return CliStatus(
            installed=True,
            version=version,
            binary_path=binary_path,
            auth_status=auth_status,
        )

    async def probe_all(self, specs: list[CliSpec]) -> dict[str, CliStatus]:
        results = await asyncio.gather(
            *(self.probe(spec) for spec in specs), return_exceptions=True
        )
        out: dict[str, CliStatus] = {}
        for spec, res in zip(specs, results, strict=False):
            if isinstance(res, BaseException):
                log.warning("probe(%s) exception: %s", spec.name, res)
                out[spec.name] = CliStatus(error=str(res))
            else:
                out[spec.name] = res
        return out

    async def _probe_binary(self, spec: CliSpec) -> tuple[bool, str | None, str | None]:
        path = shutil.which(spec.binary_name)
        if not path:
            return False, None, None
        # On Windows the binary may be a .cmd/.bat/.ps1 shim (gcloud, npm, ...).
        # ``create_subprocess_exec`` (shell=False) cannot exec a bare name in
        # that case — substitute the resolved full path for argv[0]. See
        # ``process_utils.resolve_executable``.
        check_argv = (path, *spec.check_command[1:])
        try:
            proc = await asyncio.create_subprocess_exec(
                *check_argv,
                stdout=PIPE,
                stderr=PIPE,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return True, None, path
        except FileNotFoundError:
            return False, None, None
        except Exception as exc:  # noqa: BLE001
            log.debug("probe-binary(%s) failed: %s", spec.name, exc)
            return True, None, path
        text = (out_b or b"").decode("utf-8", errors="replace")
        if not text:
            text = (err_b or b"").decode("utf-8", errors="replace")
        version = None
        if spec.version_parse_regex:
            m = re.search(spec.version_parse_regex, text)
            if m and m.groups():
                version = m.group(1)
        return True, version, path

    async def _probe_auth(
        self, spec: CliSpec
    ) -> Literal["connected", "expired", "not_connected", "unknown"]:
        if spec.auth.type == "none" or not spec.auth.status_command:
            return "connected"
        # Resolve argv[0] to the full path so .cmd/.bat shims (gcloud auth list)
        # are exec'able on Windows.
        status_argv = (
            resolve_executable(spec.auth.status_command[0]),
            *spec.auth.status_command[1:],
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *status_argv,
                stdout=PIPE,
                stderr=PIPE,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=AUTH_TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return "unknown"
            exit_code = proc.returncode or 0
        except FileNotFoundError:
            return "not_connected"
        except Exception as exc:  # noqa: BLE001
            log.debug("probe-auth(%s) failed: %s", spec.name, exc)
            return "unknown"
        stdout = (out_b or b"").decode("utf-8", errors="replace")
        stderr = (err_b or b"").decode("utf-8", errors="replace")
        return _apply_parse_strategy(spec.auth.status_parse, stdout, stderr, exit_code)


def _apply_parse_strategy(
    strategy: StatusParseStrategy,
    stdout: str,
    stderr: str,
    exit_code: int,
) -> Literal["connected", "expired", "not_connected", "unknown"]:
    if exit_code != 0 and strategy not in ("json_array_nonempty_or_error",):
        return "not_connected"

    if strategy == "json_accounts":
        try:
            data = json.loads(stdout or "[]")
        except json.JSONDecodeError:
            return "unknown"
        if isinstance(data, list) and any(
            isinstance(a, dict) and a.get("status") == "ACTIVE" for a in data
        ):
            return "connected"
        return "not_connected"

    if strategy == "json_object_exists":
        try:
            data = json.loads(stdout or "null")
        except json.JSONDecodeError:
            return "not_connected"
        if isinstance(data, dict) and data:
            return "connected"
        return "not_connected"

    if strategy == "json_array_nonempty":
        try:
            data = json.loads(stdout or "[]")
        except json.JSONDecodeError:
            return "not_connected"
        if isinstance(data, list) and len(data) > 0:
            return "connected"
        return "not_connected"

    if strategy == "json_array_nonempty_or_error":
        if exit_code != 0:
            combined = (stderr + stdout).lower()
            if "not logged in" in combined or "unauthorized" in combined or "login" in combined:
                return "not_connected"
            return "unknown"
        return "connected"

    if strategy == "json_has_field_username":
        try:
            data = json.loads(stdout or "null")
        except json.JSONDecodeError:
            return "unknown"
        if not isinstance(data, dict):
            return "unknown"
        if data.get("Username"):
            return "connected"
        return "not_connected"

    if strategy == "text_contains_email":
        pattern = r"[\w.+-]+@[\w-]+\.[\w.-]+"
        if re.search(pattern, stdout):
            return "connected"
        return "not_connected"

    if strategy == "text_contains_username":
        if stdout.strip() and "error" not in stdout.lower()[:50]:
            return "connected"
        return "not_connected"

    if strategy == "text_contains_logged_in":
        if re.search(r"logged in", stdout, re.IGNORECASE):
            return "connected"
        return "not_connected"

    if strategy == "text_contains_key":
        if re.search(r"api[_-]?key\s*=\s*\S+", stdout, re.IGNORECASE):
            return "connected"
        return "not_connected"

    if strategy == "text_nonempty":
        return "connected" if stdout.strip() else "not_connected"

    log.warning("unknown status_parse strategy: %r", strategy)
    return "unknown"


__all__ = ["CliStatusProber"]
