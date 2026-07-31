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
# After a timed-out probe is kill()ed, how long to wait for the OS to reap it
# before giving up. A .cmd/.bat shim's actual child process can survive
# kill() (the shim exits, the grandchild lives on) — an unbounded
# ``await proc.wait()`` here would then hang the probe (and the synchronous
# bootstrap path that awaits it, see jarvis/clis/loader.py) forever. A leaked
# zombie process beats an infinite hang.
KILL_WAIT_TIMEOUT_S = 2.0
# Budget for a WHOLE catalog sweep. Derived from the per-probe bounds above so
# the two can never drift out of order: a single legitimate probe may spend
# CHECK_TIMEOUT_S + KILL_WAIT_TIMEOUT_S on the binary and AUTH_TIMEOUT_S +
# KILL_WAIT_TIMEOUT_S on the auth check (29s today) before it is anything but
# healthy. The extra headroom covers OS scheduling when ~20 probes each spawn
# their own subprocess at once — on Windows an AV scanner alone can add
# seconds. A sweep budget BELOW the single-probe worst case would fire during
# normal operation instead of acting as a backstop (see BUG: the 30s registry
# ceiling blanked all 22 CLIs on 2026-07-25).
PROBE_ALL_TIMEOUT_S = CHECK_TIMEOUT_S + AUTH_TIMEOUT_S + 2 * KILL_WAIT_TIMEOUT_S + 15.0


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
        """Probe every spec concurrently and ALWAYS return one row per spec.

        Bounded per spec, not just in aggregate: probes that outlive
        ``PROBE_ALL_TIMEOUT_S`` are cancelled individually and reported as
        unknown, while every probe that finished keeps its real result. The
        old ``wait_for(gather(...))`` shape cancelled the whole sweep, so a
        few wedged Windows ``.cmd`` shims discarded the results of every
        healthy CLI as well (0 tools exposed for the rest of the session).

        A timed-out probe is ``CliStatus()`` — unknown, deliberately WITHOUT
        ``error``: ``cli_routes._status_string`` renders any error as the red
        "error" label, and "we could not finish checking" is not the same
        claim as "this CLI is broken".
        """
        if not specs:
            return {}
        tasks = [asyncio.ensure_future(self.probe(spec)) for spec in specs]
        await asyncio.wait(tasks, timeout=PROBE_ALL_TIMEOUT_S)

        out: dict[str, CliStatus] = {}
        wedged: list[asyncio.Future[CliStatus]] = []
        for spec, task in zip(specs, tasks, strict=False):
            if not task.done():
                task.cancel()
                wedged.append(task)
                log.warning(
                    "probe(%s): still running after %.0fs — reporting it as "
                    "unknown so the rest of the catalog keeps its results.",
                    spec.name,
                    PROBE_ALL_TIMEOUT_S,
                )
                out[spec.name] = CliStatus()
                continue
            if task.cancelled():
                out[spec.name] = CliStatus()
                continue
            exc = task.exception()
            if exc is not None:
                log.warning("probe(%s) exception: %s", spec.name, exc)
                out[spec.name] = CliStatus(error=str(exc))
            else:
                out[spec.name] = task.result()

        if wedged:
            # Let the cancellations settle so asyncio does not log "Task was
            # destroyed but it is pending" — but bounded, because the whole
            # point here is that these probes do not come back promptly. A
            # leaked task (and its zombie subprocess) beats a hung sweep,
            # exactly as in the kill()/wait() path above.
            try:
                await asyncio.wait_for(
                    asyncio.gather(*wedged, return_exceptions=True),
                    timeout=KILL_WAIT_TIMEOUT_S,
                )
            except TimeoutError:
                log.warning(
                    "probe_all: %d probe(s) did not honour cancellation — "
                    "leaking them instead of blocking the sweep.",
                    len(wedged),
                )
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
                try:
                    await asyncio.wait_for(proc.wait(), timeout=KILL_WAIT_TIMEOUT_S)
                except TimeoutError:
                    log.warning(
                        "probe-binary(%s): child survived kill() — leaking the "
                        "zombie instead of hanging the probe.", spec.name,
                    )
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
                try:
                    await asyncio.wait_for(proc.wait(), timeout=KILL_WAIT_TIMEOUT_S)
                except TimeoutError:
                    log.warning(
                        "probe-auth(%s): child survived kill() — leaking the "
                        "zombie instead of hanging the probe.", spec.name,
                    )
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
