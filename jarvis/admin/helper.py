"""Entry point for the UAC-elevated admin helper process.

This module is **not** imported by the parent app; instead it is launched via
``ShellExecuteW(runas, python.exe, -m jarvis.admin.helper ...)``.
It runs in the elevated context (Integrity Level High), stays idle until
SIGTERM, and then shuts down cleanly.

CLI:
    python -m jarvis.admin.helper --pipe-name <name>
                                   [--keyring-key jarvis_admin_hmac]
                                   [--env-fallback JARVIS_ADMIN_HMAC]

On startup:
1. Load the secret from the Windows Credential Manager (keyring).
2. Wire up ``AdminPipeServer`` + ``AdminExecutor``.
3. ``asyncio.run(server.serve_forever())`` — runs until SIGTERM/Ctrl+C.

Kill propagation: the helper is **not** killed by the KillSwitch
(ADR-0004 §Consequences). Pending ops are rejected, however, because the
pipe is closed by the parent — the next Read call in the helper fails,
the executor aborts, and the response never arrives.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import signal
import sys

from loguru import logger

from jarvis.core.config import get_secret

from .client import ADMIN_HMAC_ENV, ADMIN_HMAC_KEY
from .executor import AdminExecutor
from .ipc import AdminPipeServer
from .transport import current_user_sid, default_pipe_name, make_admin_transport


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="jarvis.admin.helper",
        description="UAC-elevateter Admin-Helper fuer Personal Jarvis (ADR-0001).",
    )
    p.add_argument(
        "--pipe-name",
        default=None,
        help="Named-Pipe-Pfad. Default: \\\\.\\pipe\\jarvis-admin-<user-sid>",
    )
    p.add_argument(
        "--keyring-key",
        default=ADMIN_HMAC_KEY,
        help="Keyring-Key fuer das HMAC-Secret.",
    )
    p.add_argument(
        "--env-fallback",
        default=ADMIN_HMAC_ENV,
        help="ENV-Variable als Fallback fuer das HMAC-Secret.",
    )
    return p.parse_args(argv)


def _load_secret(keyring_key: str, env_fallback: str) -> bytes:
    raw = get_secret(keyring_key, env_fallback=env_fallback)
    if not raw:
        logger.error("admin_helper.no_secret",
                     keyring_key=keyring_key, env_fallback=env_fallback)
        raise SystemExit(
            "Kein HMAC-Secret im Credential Manager. "
            "Setup-Wizard ausfuehren oder `jarvis_admin_hmac` via keyring setzen."
        )
    try:
        return base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception:  # noqa: BLE001
        return raw.encode("utf-8")


async def _serve(server: AdminPipeServer) -> None:
    """Installs signal handlers and starts the accept loop."""
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        logger.info("admin_helper.shutdown_signal")
        stop_event.set()
        server.stop()

    # SIGINT / SIGTERM handling — also works on Windows (SIGINT via Ctrl+C).
    # SIGTERM emulation: the parent can simply kill the process; we catch nothing.
    try:
        loop.add_signal_handler(signal.SIGINT, _request_stop)
    except (NotImplementedError, RuntimeError):
        signal.signal(signal.SIGINT, _request_stop)

    serve_task = asyncio.create_task(server.serve_forever(), name="admin_serve")
    await stop_event.wait()
    # Wait until the accept loop has finished and all running handlers are cleaned up.
    try:
        await asyncio.wait_for(serve_task, timeout=10.0)
    except TimeoutError:
        logger.warning("admin_helper.shutdown_timeout")
        serve_task.cancel()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pipe_name = args.pipe_name or default_pipe_name()
    secret = _load_secret(args.keyring_key, args.env_fallback)
    sid = current_user_sid()

    executor = AdminExecutor()
    # Bind the OS-appropriate transport (Windows named pipe / Unix domain
    # socket) instead of hardcoding the named pipe; the reused HMAC/executor
    # chain in AdminPipeServer.handle_raw is transport-agnostic (AD-12).
    transport = make_admin_transport(pipe_name, sid=sid)
    server = AdminPipeServer(secret, pipe_name, executor, sid=sid,
                             transport=transport)

    logger.info("admin_helper.boot", pipe=pipe_name, sid=sid)
    try:
        asyncio.run(_serve(server))
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001
        logger.exception("admin_helper.crash")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
