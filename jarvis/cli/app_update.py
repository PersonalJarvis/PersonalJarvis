"""``jarvis update`` — update the installed app to the newest release.

The terminal twin of the in-app "Update Now" button for an install made with
the one-line installer (a *managed* checkout, see
``jarvis/ui/web/update_routes.py``). It reuses that machinery end to end:

1. **Check** — the same release lookup the button uses decides whether a newer
   published version exists.
2. **Stage** — the same guard, pinned tag fetch and pending-update manifest as
   ``POST /api/update/apply``.
3. **Finalize** — the same ``relauncher.finalize_pending_update`` the restart
   helper runs: full installer, tracked UI bundle check, automatic rollback to
   the previous revision on any failure.

Steps 1 and 2 import the web stack (pydantic and friends ship native modules),
so they run in a short-lived child process. This process stays on the
standard library plus a few stdlib-only ``jarvis`` modules, because step 3
reinstalls the venv's packages and Windows cannot replace a native module that
a live process has loaded — the reason the in-app path finalizes only after
the old app exited. Step 3 runs in its own session/process group so a Ctrl+C
in the terminal can never stop the installer halfway.

The app must not be running while its files are replaced. A running desktop
app is not stopped from here: quitting the app is a desktop-UI action that
Control-API and coding-agent clients are refused (``lifecycle_guard.py``), and
a terminal command is no better proof of a human. The windowless background
service, which is ours to stop, is asked to hand back first.

Works the same on Windows, macOS and Linux, including a headless server.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

PROBE_FLAG = "--probe"
FINALIZE_FLAG = "--finalize"

#: Exit codes. 0 = updated or already current.
EXIT_FAILED = 1
EXIT_NOT_UPDATABLE = 2
EXIT_APP_RUNNING = 3

_PROBE_TIMEOUT_S = 300.0
_SERVICE_HANDBACK_S = 60.0

_ONE_LINER_WINDOWS = (
    "irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/"
    "install/install.ps1 | iex"
)
_ONE_LINER_POSIX = (
    "curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/"
    "install/install.sh | bash"
)

#: Runs a child ``python -m jarvis.cli.app_update`` and returns
#: ``(returncode, stdout)``. A seam so tests never spawn a real interpreter.
ChildRunner = Callable[[list[str], float | None, bool], tuple[int, str]]


# --------------------------------------------------------------------------- #
# Child side: the heavy work, one JSON line on stdout
# --------------------------------------------------------------------------- #
def _probe(mode: str) -> dict[str, Any]:
    """Run inside the child: check (``status``) or stage (``stage``)."""
    import asyncio

    from fastapi import HTTPException

    from jarvis.ui.web import update_routes

    if mode == "status":
        status = dict(asyncio.run(update_routes.update_status(force=True)))
        try:
            from jarvis.core.background_service import service_pid

            status["service_pid"] = service_pid()
        except Exception:  # noqa: BLE001 - no service module, no service
            status["service_pid"] = None
        return status
    try:
        root = asyncio.run(update_routes.managed_checkout_root())
        staged = dict(asyncio.run(update_routes.stage_managed_update()))
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("error") or json.dumps(detail)
        return {"ok": False, "error": str(detail), "status_code": exc.status_code}
    staged["root"] = str(root) if root is not None else None
    return staged


def _finalize(root: str) -> int:
    """Run inside the detached child: apply the staged update, or roll back."""
    from jarvis.ui.relauncher import finalize_pending_update

    return 0 if finalize_pending_update(root) else 1


def _child_main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == PROBE_FLAG and argv[1] in {"status", "stage"}:
        try:
            payload = _probe(argv[1])
        except Exception as exc:  # noqa: BLE001 - reported to the parent as JSON
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write("\n" + json.dumps(payload, default=str) + "\n")
        sys.stdout.flush()
        return 0
    if len(argv) == 2 and argv[0] == FINALIZE_FLAG:
        return _finalize(argv[1])
    return 2


# --------------------------------------------------------------------------- #
# Parent side: lean orchestration and plain-language output
# --------------------------------------------------------------------------- #
def _run_child(args: list[str], timeout: float | None, detached: bool) -> tuple[int, str]:
    import jarvis

    cmd = [sys.executable, "-m", "jarvis.cli.app_update", *args]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    kwargs: dict[str, Any] = {
        # ``-m`` puts the working directory first on sys.path. Run from the
        # tree this process came from, so a `jarvis/` folder in the user's
        # current directory can never stand in for the installed one.
        "cwd": str(Path(jarvis.__file__).resolve().parent.parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "env": env,
        "creationflags": NO_WINDOW_CREATIONFLAGS,
    }
    if detached:
        # Out of the terminal's reach: Ctrl+C must not kill the installer
        # between "checkout moved" and "packages installed".
        if sys.platform == "win32":
            kwargs["creationflags"] |= subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - our own module, fixed argv
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        try:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            out, _ = proc.communicate(timeout=remaining)
            break
        except KeyboardInterrupt:
            if not detached:
                proc.kill()
                raise
            print(
                "\n  Still updating. Stopping now would leave a half-installed app, "
                "so this keeps going - please wait.",
                flush=True,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return -1, ""
    return proc.returncode, (out or b"").decode("utf-8", errors="replace")


def _last_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


class _Ticker:
    """Show elapsed time on one terminal line while a long step runs."""

    def __init__(self, label: str, *, enabled: bool) -> None:
        self._label = label
        self._enabled = enabled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _Ticker:
        if self._enabled:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            print(f"  {self._label}", flush=True)
        return self

    def _run(self) -> None:
        start = time.monotonic()
        while not self._stop.wait(1.0):
            minutes, seconds = divmod(int(time.monotonic() - start), 60)
            print(f"\r  {self._label} {minutes}:{seconds:02d}", end="", flush=True)

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            print(flush=True)


def _not_updatable_message() -> str:
    one_liner = _ONE_LINER_WINDOWS if sys.platform == "win32" else _ONE_LINER_POSIX
    return (
        "This copy of Personal Jarvis cannot update itself: it was not installed\n"
        "with the one-line installer (it is a developer checkout or a pip install).\n"
        "  Developer checkout:  git pull\n"
        "  pip install:         pip install --upgrade personal-jarvis\n"
        f"  Or install the managed version:  {one_liner}"
    )


def _running_app_pid() -> int | None:
    from jarvis.cli_ctl.discovery import discover

    session = discover()
    if session is None:
        return None
    return session.pid or -1


def _stop_background_service(pid: int, *, wait: Callable[..., bool] | None = None) -> bool:
    """Ask the windowless background service to hand back and wait for it."""
    from jarvis.core.background_service import clear_handover_request, request_handover
    from jarvis.ui.relauncher import wait_for_pid_exit

    wait = wait or wait_for_pid_exit
    request_handover()
    try:
        return bool(wait(pid, timeout=_SERVICE_HANDBACK_S))
    finally:
        clear_handover_request()


def _read_result(root: Path) -> dict[str, Any]:
    from jarvis.ui.relauncher import UPDATE_RESULT_FILENAME

    try:
        payload = json.loads((root / UPDATE_RESULT_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _clear_result(root: Path) -> None:
    from jarvis.ui.relauncher import UPDATE_RESULT_FILENAME

    try:
        (root / UPDATE_RESULT_FILENAME).unlink(missing_ok=True)
    except OSError as exc:
        print(f"  (could not clear the previous update result: {exc})", file=sys.stderr)


def run_update(
    *,
    check_only: bool = False,
    run_child: ChildRunner = _run_child,
    is_frozen: Callable[[], bool] | None = None,
    running_app_pid: Callable[[], int | None] = _running_app_pid,
    stop_service: Callable[[int], bool] = _stop_background_service,
    interactive: bool | None = None,
) -> int:
    """Check for, download and install the newest release. Returns an exit code."""
    if is_frozen is None:
        from jarvis.core.frozen import is_frozen as _is_frozen

        is_frozen = _is_frozen
    if interactive is None:
        interactive = sys.stdout.isatty()

    if is_frozen():
        print(
            "This Personal Jarvis came from a downloaded installer. Open the app and\n"
            "use Settings -> Update; it downloads and installs the new version for you."
        )
        return EXIT_NOT_UPDATABLE

    print("Checking for a newer version of Personal Jarvis...", flush=True)
    code, out = run_child([PROBE_FLAG, "status"], _PROBE_TIMEOUT_S, False)
    status = _last_json(out)
    if status is None:
        print(f"Could not check for updates (exit code {code}).", file=sys.stderr)
        return EXIT_FAILED
    if status.get("error"):
        print(f"Could not check for updates: {status['error']}", file=sys.stderr)
        return EXIT_FAILED
    if not status.get("managed"):
        print(_not_updatable_message())
        return EXIT_NOT_UPDATABLE

    current = status.get("current") or "unknown"
    if status.get("check_failed"):
        print(
            "Could not reach GitHub to look for a new version (offline or rate-limited).\n"
            "Try again in a few minutes.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    latest = status.get("latest") or current
    if not status.get("update_available"):
        print(f"Personal Jarvis {current} is already the newest version.")
        return 0
    print(f"Version {latest} is available (you have {current}).")
    if check_only:
        print("Run `jarvis update` to install it.")
        return 0

    # The service is found by its own marker, not through the session file: a
    # live service with a missing or stale session.json would otherwise keep
    # the venv's native modules open through the whole reinstall.
    service = status.get("service_pid")
    if service:
        print("Stopping the background service for the update...", flush=True)
        if not stop_service(int(service)):
            print(
                "The background service did not stop. Open Personal Jarvis, quit it\n"
                "from the tray icon, then run `jarvis update` again.",
                file=sys.stderr,
            )
            return EXIT_APP_RUNNING
    pid = running_app_pid()
    if pid is not None and pid != service:
        print(
            "Personal Jarvis is running, and it cannot be updated while it is open.\n"
            "Either click Update inside the app, or quit it (tray icon -> Quit)\n"
            "and run `jarvis update` again.",
            file=sys.stderr,
        )
        return EXIT_APP_RUNNING

    print(f"Downloading version {latest}...", flush=True)
    code, out = run_child([PROBE_FLAG, "stage"], _PROBE_TIMEOUT_S, False)
    staged = _last_json(out)
    if staged is None or not staged.get("ok") or not staged.get("root"):
        reason = (staged or {}).get("error") or f"exit code {code}"
        print(f"The update could not be downloaded: {reason}", file=sys.stderr)
        print("Nothing was changed.", file=sys.stderr)
        return EXIT_FAILED

    root = Path(str(staged["root"]))
    version = staged.get("version") or latest
    # A result left by an earlier run must not speak for this one.
    _clear_result(root)
    with _Ticker(
        f"Installing version {version} - this takes a few minutes, keep this window open...",
        enabled=interactive,
    ):
        code, _ = run_child([FINALIZE_FLAG, str(root)], None, True)
    result = _read_result(root)
    if code == 0 and result.get("ok") is True:
        print(f"Done. Personal Jarvis {version} is installed.")
        print("Start it with: jarvis")
        return 0
    if result.get("rolled_back"):
        print(
            f"Installing version {version} failed, so Personal Jarvis went back to "
            f"{current}. Your settings and data are unchanged.",
            file=sys.stderr,
        )
    else:
        print(
            f"Installing version {version} failed and the previous version could not be "
            "restored automatically. Re-run the install one-liner to repair it:\n"
            f"  {_ONE_LINER_WINDOWS if sys.platform == 'win32' else _ONE_LINER_POSIX}",
            file=sys.stderr,
        )
    return EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis update",
        description="Update Personal Jarvis to the newest published version.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only report whether a newer version exists; change nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_update(check_only=args.check)
    except KeyboardInterrupt:
        # Only reachable before the install step, which ignores Ctrl+C.
        print("\nCancelled. Nothing was changed.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
