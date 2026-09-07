"""Shared plumbing for screen providers: state dirs, tokens, runner staging."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Where a screen keeps its mailbox, staged runner and persistent profile.
#: Under the user data dir (never the repo) so a packaged install and a source
#: checkout behave identically, and never a hardcoded absolute path.
_SCREEN_DIR_NAME = "agent_screens"


def screens_root() -> Path:
    from jarvis.core.paths import user_data_dir  # noqa: PLC0415

    return Path(user_data_dir()) / _SCREEN_DIR_NAME


def screen_dir(screen_id: str) -> Path:
    path = screens_root() / screen_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_root() -> Path:
    """The persistent slice an isolated screen keeps between boots.

    A Windows Sandbox is discarded on every stop, which would log the agent
    out of everything on every job. Mapping ONE folder back in gives the agent
    a durable browser profile — the "digital life" half of the clone — without
    weakening the isolation of the rest of the session.
    """
    path = screens_root() / "_profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def mint_token() -> str:
    """A per-boot bearer token for the runner.

    ``secrets`` rather than ``uuid``: this authorises input injection into a
    session, and a predictable value would let any local process drive the
    agent's screen.
    """
    return secrets.token_urlsafe(32)


def write_token_file(path: Path, token: str) -> None:
    """Persist the token where only the runner will read it.

    Passed as a file, never as an argv element: process listings are readable
    by every local user on all three supported systems.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            logger.debug("could not tighten screen token permissions", exc_info=True)


def stage_sandbox_runner(target_dir: Path) -> Path:
    """Copy the PowerShell runner next to the screen that will use it.

    Copied rather than mapped from the source tree because the guest gets a
    read-only view of exactly this folder — mapping the whole repository into
    a sandbox would hand an isolated session the user's entire codebase.
    """
    source = Path(__file__).resolve().parent.parent / "runner" / "sandbox_runner.ps1"
    if not source.is_file():
        raise FileNotFoundError(f"the sandbox runner is missing at {source}")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copyfile(source, target)
    return target


def popen_detached(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.Popen[bytes]:
    """Start a helper process without flashing a console window.

    ``NO_WINDOW_CREATIONFLAGS`` on Windows and a new session on POSIX, so a
    screen's helpers never inherit the app's terminal and never die with it in
    a way the provider cannot control.
    """
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    merged = dict(os.environ)
    if env:
        merged.update(env)
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    merged.setdefault("PYTHONUTF8", "1")
    kwargs: dict[str, object] = {
        "env": merged,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if sys.platform == "win32":
        kwargs["creationflags"] = NO_WINDOW_CREATIONFLAGS
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)  # type: ignore[arg-type]  # noqa: S603


def terminate_quietly(process: subprocess.Popen[bytes] | None, *, timeout_s: float = 5.0) -> None:
    """Stop a helper process, escalating once, never raising."""
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout_s)
    except Exception:  # noqa: BLE001 — escalate below
        try:
            process.kill()
            process.wait(timeout=timeout_s)
        except Exception:  # noqa: BLE001 — teardown must never raise
            logger.warning("could not stop a screen helper process", exc_info=True)


__all__ = [
    "mint_token",
    "popen_detached",
    "profile_root",
    "screen_dir",
    "screens_root",
    "stage_sandbox_runner",
    "terminate_quietly",
    "write_token_file",
]
