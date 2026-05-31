"""``CliAuthManager`` — Auth flows for CLIs (API-key vs OAuth-delegate).

Two authentication strategies side by side:

- **API-Key** — Jarvis stores the key in the Windows Credential Manager and
  injects it as an environment variable when invoking the subprocess.
- **OAuth-CLI** — the CLI has its own login command (``gcloud auth login``).
  ``CliAuthManager.start_oauth_login(...)`` spawns it as a subprocess and then
  polls ``CliStatusProber`` until the auth status changes to ``connected``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from jarvis.clis.prober import CliStatusProber
from jarvis.clis.spec import CliSpec
from jarvis.core.config import delete_secret, get_secret, set_secret
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

OAUTH_POLL_INTERVAL_S = 2.0
OAUTH_MAX_POLL_SECONDS = 300.0


@dataclass(slots=True)
class OAuthLoginHandle:
    job_id: str
    cli_name: str
    started_at_ms: int
    task: asyncio.Task[Literal["connected", "timeout", "cancelled", "error"]]

    def cancel(self) -> None:
        if not self.task.done():
            self.task.cancel()


class CliAuthManager:
    def __init__(self, prober: CliStatusProber | None = None) -> None:
        self._prober = prober or CliStatusProber()
        self._active_logins: dict[str, OAuthLoginHandle] = {}

    def env_for(self, spec: CliSpec) -> dict[str, str]:
        if spec.auth.type != "api_key":
            return {}
        if not spec.auth.secret_keys or not spec.auth.env_vars:
            return {}
        out: dict[str, str] = {}
        for key, env_var in zip(spec.auth.secret_keys, spec.auth.env_vars, strict=False):
            val = get_secret(key, env_fallback=env_var)
            if val:
                out[env_var] = val
        return out

    async def connect_api_key(
        self,
        spec: CliSpec,
        secrets: dict[str, str],
        *,
        validate: bool = True,
    ) -> tuple[bool, str | None]:
        if spec.auth.type != "api_key":
            return False, f"{spec.name} nutzt auth.type='{spec.auth.type}', nicht api_key"

        missing = [k for k in spec.auth.secret_keys if not secrets.get(k)]
        required = list(spec.auth.secret_keys)
        if missing and missing == required:
            return False, f"secrets fehlen: {', '.join(missing)}"

        if validate:
            import os
            backup: dict[str, str | None] = {}
            try:
                for key, env_var in zip(spec.auth.secret_keys, spec.auth.env_vars, strict=False):
                    backup[env_var] = os.environ.get(env_var)
                    if secrets.get(key):
                        os.environ[env_var] = secrets[key]
                status = await self._prober.probe(spec)
                if status.auth_status != "connected":
                    return False, f"Validation fehlgeschlagen (status={status.auth_status})"
            finally:
                for env_var, old in backup.items():
                    if old is None:
                        os.environ.pop(env_var, None)
                    else:
                        os.environ[env_var] = old

        for key, value in secrets.items():
            if key not in spec.auth.secret_keys:
                log.warning("auth: unerwarteter secret_key '%s' fuer %s", key, spec.name)
                continue
            if not value:
                continue
            if not set_secret(key, value):
                return False, f"Keyring-Write fuer '{key}' fehlgeschlagen"
        return True, None

    def disconnect_api_key(self, spec: CliSpec) -> bool:
        if spec.auth.type != "api_key":
            return False
        ok = True
        for key in spec.auth.secret_keys:
            if not delete_secret(key):
                ok = False
        return ok

    def start_oauth_login(
        self,
        spec: CliSpec,
        *,
        job_id: str,
        on_line: callable | None = None,  # type: ignore[type-arg]
    ) -> OAuthLoginHandle | None:
        if spec.auth.type != "oauth_cli" or not spec.auth.login_command:
            return None
        existing = self._active_logins.get(spec.name)
        if existing and not existing.task.done():
            return existing

        task = asyncio.create_task(
            self._run_oauth_flow(spec, on_line=on_line),
            name=f"oauth-login-{spec.name}",
        )
        handle = OAuthLoginHandle(
            job_id=job_id,
            cli_name=spec.name,
            started_at_ms=int(time.time() * 1000),
            task=task,
        )
        self._active_logins[spec.name] = handle
        return handle

    def active_login(self, cli_name: str) -> OAuthLoginHandle | None:
        handle = self._active_logins.get(cli_name)
        if handle and handle.task.done():
            self._active_logins.pop(cli_name, None)
            return None
        return handle

    async def disconnect_oauth(self, spec: CliSpec) -> tuple[bool, str | None]:
        if spec.auth.type != "oauth_cli" or not spec.auth.logout_command:
            return False, "keine logout_command definiert"
        proc = await asyncio.create_subprocess_exec(
            *spec.auth.logout_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "logout timeout"
        return proc.returncode == 0, None if proc.returncode == 0 else f"exit {proc.returncode}"

    async def _run_oauth_flow(
        self,
        spec: CliSpec,
        *,
        on_line: callable | None,  # type: ignore[type-arg]
    ) -> Literal["connected", "timeout", "cancelled", "error"]:
        assert spec.auth.login_command, "precondition: login_command muss gesetzt sein"
        try:
            proc = await asyncio.create_subprocess_exec(
                *spec.auth.login_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except FileNotFoundError:
            return "error"

        async def _drain() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                if on_line is not None:
                    try:
                        on_line(line.decode("utf-8", errors="replace").rstrip())
                    except Exception:  # noqa: BLE001
                        pass

        drain_task = asyncio.create_task(_drain(), name=f"oauth-drain-{spec.name}")
        deadline = time.monotonic() + OAUTH_MAX_POLL_SECONDS
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(OAUTH_POLL_INTERVAL_S)
                status = await self._prober.probe(spec)
                if status.auth_status == "connected":
                    return "connected"
            proc.terminate()
            return "timeout"
        except asyncio.CancelledError:
            proc.terminate()
            return "cancelled"
        except Exception as exc:  # noqa: BLE001
            log.exception("oauth-flow(%s) failed: %s", spec.name, exc)
            return "error"
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except (TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
            drain_task.cancel()
            try:
                await drain_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


__all__ = ["CliAuthManager", "OAuthLoginHandle"]
