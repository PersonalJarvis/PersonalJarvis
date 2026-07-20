"""Detached helper that restarts the Jarvis desktop app cleanly.

The desktop app cannot restart itself in-process: its single-instance Named
Mutex (``Global\\PersonalJarvis_v1``) is held until the process exits, and a
fresh launcher started while the old process still lives would just activate the
old window and exit (see ``jarvis/ui/shell/single_instance.py``). So a restart is
two-phase:

1. The dying app spawns THIS detached helper (``DesktopApp.request_restart``).
2. The helper waits for the old PID to disappear (the kernel releases the mutex
   on exit), then starts a fresh launcher that claims the now-free mutex.

Invoked as::

    python -m jarvis.ui.relauncher <parent_pid> <repo_cwd>

It runs windowless + detached so it outlives its parent. Stdlib only — it must
start fast and never pull the heavy ``jarvis`` runtime into a throwaway process.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from jarvis.core.branding import MACOS_APP_DIR_NAME, MANAGED_INSTALL_MARKER

LAUNCHER_MODULE = "jarvis.ui.web.launcher"
MANAGED_MARKER = MANAGED_INSTALL_MARKER
PENDING_UPDATE_FILENAME = ".jarvis-update-pending.json"
UPDATE_RESULT_FILENAME = ".jarvis-update-result.json"
_REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def build_launch_command(executable: str) -> list[str]:
    """Argv that boots a fresh desktop app through its stable OS identity.

    A macOS desktop restart always re-enters through LaunchServices so it can
    never attach TCC access to a raw Python interpreter.  A missing or invalid
    bundle therefore fails closed; the managed installer/repair path owns
    recreating it.
    """
    fallback = [executable, "-m", LAUNCHER_MODULE]
    if sys.platform == "darwin":
        bundle = Path.home() / "Applications" / MACOS_APP_DIR_NAME
        try:
            from jarvis.setup.macos_app_bundle import (
                macos_app_bundle_is_launchable,
                macos_app_bundle_path,
                macos_launch_services_command,
            )

            bundle = macos_app_bundle_path()
            if not macos_app_bundle_is_launchable(bundle):
                logging.getLogger(__name__).error(
                    "macOS restart target is missing or invalid: %s", bundle
                )
            return macos_launch_services_command(bundle, wait_for_exit=True)
        except Exception:  # noqa: BLE001 - preserve stable identity fail-closed
            logging.getLogger(__name__).exception(
                "Could not validate the macOS restart bundle; using its canonical path"
            )
            return ["/usr/bin/open", "-W", "-a", str(bundle)]
    return fallback


def _read_windows_user_jarvis_env() -> dict[str, str] | None:
    """The ``JARVIS__*`` config overrides persisted in the user's CURRENT
    Windows environment (HKCU\\Environment), or ``None`` when unavailable.

    ``None`` (non-Windows host, unreadable registry) means "no fresher source
    than the inherited environment exists" — the caller keeps it unchanged.
    POSIX hosts have no persisted user-env registry to re-read; their inherited
    environment is already the freshest source, so this is an honest no-op.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg

        persisted: dict[str, str] = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if isinstance(name, str) and name.upper().startswith("JARVIS__"):
                    persisted[name] = str(value)
        return persisted
    except OSError:
        return None


def fresh_user_env(
    base: dict[str, str] | None = None, *, _read_persisted=_read_windows_user_jarvis_env
) -> dict[str, str]:
    """Environment for the NEW launcher, with ``JARVIS__*`` overrides re-read
    from the user's currently persisted environment.

    Without this, the restart chain FOSSILIZES the env config layer: each
    restarted process inherits the ``JARVIS__*`` values captured when the
    first tray process started, so a config fix that updates all three pinned
    layers (jarvis.toml + config-soll.json + user env) keeps being overridden  # i18n-allow: config-soll.json is a filename
    by the stale inherited copy on every ``restart-app`` — live case
    2026-07-17: the TTS voice pin kept resurrecting a replaced voice. Only
    ``JARVIS__*`` keys (the pydantic config-override namespace) are refreshed;
    everything else stays inherited.
    """
    env = dict(os.environ if base is None else base)
    persisted = _read_persisted()
    if persisted is None:
        return env
    # Drop every inherited JARVIS__* key first (Windows env names are
    # case-insensitive, so spelling variants must not survive alongside the
    # refreshed names), then lay down the persisted set verbatim.
    for stale in [
        k for k in env if k.upper().startswith("JARVIS__") and k not in persisted
    ]:
        del env[stale]
    env.update(persisted)
    return env


def detached_creationflags() -> int:
    """Windows creationflags that make a child outlive its parent, windowless.

    ``DETACHED_PROCESS`` cuts the child loose from the parent's console/process
    group; ``CREATE_NO_WINDOW`` keeps ``pythonw`` from flashing a console. ``0``
    on non-Windows (the caller uses ``start_new_session`` there instead).
    """
    if sys.platform == "win32":
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return detached | no_window
    return 0


def pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` currently exists. Never kills it.

    ``os.kill(pid, 0)`` is POSIX-safe (signal 0 only probes) but on Windows it
    routes to ``TerminateProcess`` for non-CTRL signals — so on Windows we probe
    with ``OpenProcess``/``WaitForSingleObject`` instead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102  # still running; WAIT_OBJECT_0 (0) = exited
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False  # already gone (or no rights — treat as gone)
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def wait_for_pid_exit(
    pid: int,
    *,
    timeout: float = 45.0,
    poll: float = 0.15,
    _alive=pid_alive,
    _now=time.monotonic,
    _sleep=time.sleep,
) -> bool:
    """Block until ``pid`` is gone (True) or ``timeout`` elapses (False)."""
    deadline = _now() + timeout
    while _now() < deadline:
        if not _alive(pid):
            return True
        _sleep(poll)
    return not _alive(pid)


def _arm_hard_exit_watchdog(delay: float, exit_fn) -> None:
    """Force-exit independently even when the GUI destroy call never returns."""

    def _force_exit() -> None:
        time.sleep(delay)
        exit_fn(0)

    threading.Thread(
        target=_force_exit,
        name="jarvis-restart-hard-exit",
        daemon=True,
    ).start()


def run_restart_quit_sequence(
    *,
    set_quit,
    destroy_window,
    pre_delay: float = 0.15,
    hard_exit_after: float = 0.7,
    _sleep=time.sleep,
    _exit=os._exit,
    _arm_watchdog=_arm_hard_exit_watchdog,
) -> None:
    """Quit the DYING app for a restart, hard-exiting if shutdown stalls.

    Runs in a daemon thread of the app being replaced. It (1) waits a beat so
    the HTTP 200 reaches the frontend, (2) marks the quit + destroys the window
    (the normal clean-shutdown path), then (3) **force-exits the process** if it
    is still alive after ``hard_exit_after`` seconds.

    The hard exit is the load-bearing part: the relauncher's fresh instance can
    only claim the single-instance mutex + TCP port once THIS process is gone.
    It is armed in an independent daemon thread *before* ``window.destroy``.
    A cross-thread destroy can itself block forever (the BUG-031 hazard), so a
    watchdog placed after that call is not a watchdog at all. If normal shutdown
    finishes first, the process ends and takes the daemon thread with it.

    Speed note (2026-06-21): for a RESTART the dying app does not need a full,
    leisurely clean shutdown — the fresh instance re-initialises every subsystem
    anyway. So the hard-exit cap is tight (0.7 s, was 10 s): a slow or hanging
    teardown (MCP session close, the BUG-031 window-destroy hang) is force-exited
    fast, freeing the lock + port for the fresh, fast-booting instance. The only
    cost is some teardown skipped on restart (e.g. an MCP subprocess re-spawned
    by the new instance) — acceptable for a controlled restart.
    """
    _sleep(pre_delay)
    try:
        set_quit()
    except Exception:  # noqa: BLE001, S110 — never block quit on callback error
        pass
    _arm_watchdog(hard_exit_after, _exit)
    try:
        destroy_window()
    except Exception:  # noqa: BLE001, S110 — destroy may be impossible; watchdog exits
        pass


def _new_instance_settled(
    pid,
    *,
    _alive=pid_alive,
    _sleep=time.sleep,
    checks: int = 5,
    interval: float = 1.0,
) -> bool:
    """True if a freshly spawned launcher is still alive after a short grace.

    A secondary that bounces off the single-instance lock prints "already
    running", focuses the existing window, and exits within ~1 s; a real primary
    keeps running. So "still alive after a few seconds" is a good proxy for "the
    new instance actually came up". An unverifiable pid (missing/invalid) is
    treated as success to avoid spinning needlessly.
    """
    if not isinstance(pid, int) or pid <= 0:
        return True
    for _ in range(checks):
        _sleep(interval)
        if not _alive(pid):
            return False
    return True


def _read_pending_update(root: Path) -> dict[str, object] | None:
    """Read and strictly validate a relaunch-time update transaction."""

    try:
        payload = json.loads(
            (root / PENDING_UPDATE_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        return None
    previous = payload.get("previous_revision")
    target = payload.get("target_revision")
    profile = payload.get("profile")
    if not isinstance(previous, str) or not _REVISION_RE.fullmatch(previous):
        return None
    if not isinstance(target, str) or not _REVISION_RE.fullmatch(target):
        return None
    if profile not in {"full", "headless"}:
        return None
    return payload


def _managed_python(root: Path) -> str:
    """Use only the checkout venv for installer work when it is available."""

    if sys.platform == "win32":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    return str(candidate if candidate.is_file() else Path(sys.executable))


def _run_update_command(
    cmd: list[str], *, root: Path, timeout: float
) -> int:
    """Run a windowless update child and collapse every launch failure to -1."""

    kwargs: dict[str, object] = {
        "cwd": str(root),
        "env": {
            key: value
            for key, value in os.environ.items()
            if key != "JARVIS_INSTALL_NO_PIP"
        },
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": timeout,
        "check": False,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        return subprocess.run(cmd, **kwargs).returncode
    except (OSError, subprocess.SubprocessError):
        return -1


def _installer_command(root: Path, profile: str) -> list[str]:
    """Build the full, no-relaunch installer command for an update profile."""

    cmd = [
        _managed_python(root),
        str(root / "install" / "installer.py"),
        "--no-launch",
    ]
    cmd.append("--with-desktop" if profile == "full" else "--headless")
    return cmd


def _ui_bundle_ready(root: Path) -> bool:
    """Verify that the checked-out release owns a loadable JS/CSS entry set."""

    dist = root / "jarvis" / "ui" / "web" / "dist"
    index = dist / "index.html"
    try:
        html = index.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    refs = {
        match.group(1).split("?", 1)[0]
        for match in re.finditer(r'(?:src|href)=["\']/?(assets/[^"\']+)', html)
    }
    if not any(ref.endswith(".js") for ref in refs):
        return False

    required = {"jarvis/ui/web/dist/index.html"}
    for ref in refs:
        asset = dist / Path(ref)
        try:
            if not asset.is_file() or asset.stat().st_size <= 0:
                return False
        except OSError:
            return False
        required.add(f"jarvis/ui/web/dist/{ref}")

    for rel in required:
        if _run_update_command(
            ["git", "ls-files", "--error-unmatch", "--", rel],
            root=root,
            timeout=30.0,
        ) != 0:
            return False
    return True


def _write_update_result(
    root: Path,
    *,
    ok: bool,
    rolled_back: bool,
    previous_revision: str,
    target_revision: str,
) -> None:
    """Persist a non-sensitive result for diagnostics after the new launch."""

    path = root / UPDATE_RESULT_FILENAME
    temp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "schema": 1,
        "ok": ok,
        "rolled_back": rolled_back,
        "previous_revision": previous_revision,
        "target_revision": target_revision,
        "completed_at": int(time.time()),
    }
    try:
        temp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        pass


def finalize_pending_update(cwd: str | Path) -> bool:
    """Apply a fetched update while the old app is fully stopped.

    Success requires the complete installer and a tracked JavaScript bundle.
    Any failure resets the checkout to the exact previous revision and runs its
    installer once more before launch, so an incomplete update is never treated
    as installed.
    """

    root = Path(cwd).resolve()
    pending_path = root / PENDING_UPDATE_FILENAME
    payload = _read_pending_update(root)
    if payload is None:
        return True
    if not (root / MANAGED_MARKER).is_file() or not (root / ".git").exists():
        pending_path.unlink(missing_ok=True)
        return False

    previous = str(payload["previous_revision"])
    target = str(payload["target_revision"])
    profile = str(payload["profile"])

    target_reset_ok = (
        _run_update_command(
            ["git", "reset", "--hard", target], root=root, timeout=120.0
        )
        == 0
    )
    target_install_ok = False
    if target_reset_ok:
        target_install_ok = (
            _run_update_command(
                _installer_command(root, profile), root=root, timeout=3600.0
            )
            == 0
            and _ui_bundle_ready(root)
        )
    if target_install_ok:
        pending_path.unlink(missing_ok=True)
        _write_update_result(
            root,
            ok=True,
            rolled_back=False,
            previous_revision=previous,
            target_revision=target,
        )
        return True

    rollback_reset_ok = (
        _run_update_command(
            ["git", "reset", "--hard", previous], root=root, timeout=120.0
        )
        == 0
    )
    rollback_install_ok = False
    if rollback_reset_ok:
        rollback_install_ok = (
            _run_update_command(
                _installer_command(root, profile), root=root, timeout=3600.0
            )
            == 0
            and _ui_bundle_ready(root)
        )
    pending_path.unlink(missing_ok=True)
    rolled_back = rollback_reset_ok and rollback_install_ok
    _write_update_result(
        root,
        ok=False,
        rolled_back=rolled_back,
        previous_revision=previous,
        target_revision=target,
    )
    return False


def main(
    argv: list[str] | None = None,
    *,
    _wait=wait_for_pid_exit,
    _spawn=subprocess.Popen,
    _sleep=time.sleep,
    _alive=pid_alive,
    _settled=_new_instance_settled,
    _finalize_update=finalize_pending_update,
    attempts: int = 3,
) -> int:
    """Wait for the old app to exit, then start a fresh launcher — verified.

    The single-instance lock frees only once the old process is gone, so we
    wait for that first. After spawning the new launcher we verify it actually
    stayed up (it would otherwise have bounced off a still-held lock) and retry
    a couple of times if not.

    Returns ``2`` on bad argv, ``0`` once a new instance is confirmed up, ``1``
    if every spawn attempt failed to bring one up.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        return 2
    try:
        pid = int(argv[0])
    except ValueError:
        return 2
    cwd = argv[1]

    kwargs: dict[str, object] = {"cwd": cwd, "close_fds": True}
    # Fresh JARVIS__* overrides for the new instance — never the dying
    # process's fossilized copy (see fresh_user_env).
    kwargs["env"] = fresh_user_env()
    if sys.platform == "win32":
        kwargs["creationflags"] = detached_creationflags()
    else:
        kwargs["start_new_session"] = True

    update_finalized = False
    for attempt in range(attempts):
        # Never launch into a still-held lock: the old process must be gone.
        if _alive(pid):
            parent_exited = _wait(pid, timeout=45.0 if attempt == 0 else 15.0)
            if not parent_exited:
                continue
        # Extra grace so the kernel finishes releasing the mutex + the TCP port
        # before the new launcher tries to claim them. Short — the kernel frees
        # both the instant the old PID is gone; this only covers the tail.
        _sleep(0.2)

        # Update only after the old interpreter has released imported native
        # modules. The finalizer runs the complete installer and rolls back on
        # any incomplete dependency, UI, or desktop-registration result.
        if not update_finalized:
            _finalize_update(cwd)
            update_finalized = True

        proc = _spawn(build_launch_command(sys.executable), **kwargs)
        new_pid = getattr(proc, "pid", None)
        if _settled(new_pid, _alive=_alive, _sleep=_sleep):
            return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
