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

from jarvis.core.branding import (
    CONFIG_FILE_NAME,
    MACOS_APP_DIR_NAME,
    MANAGED_INSTALL_MARKER,
    PRODUCT_NAME,
    WINDOWS_BRANDED_LAUNCH_ENV_VAR,
    WINDOWS_BRANDED_LAUNCHER_DIR_NAME,
)

LAUNCHER_MODULE = "jarvis.ui.web.launcher"
MANAGED_MARKER = MANAGED_INSTALL_MARKER
PENDING_UPDATE_FILENAME = ".jarvis-update-pending.json"
UPDATE_RESULT_FILENAME = ".jarvis-update-result.json"
_REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
#: Must match ``jarvis.ui.web.launcher._DEFAULT_ADMIN_PORT`` / the packaged
#: ``[ui].admin_api_port`` default. Used only when the checkout's config
#: cannot be read, so a restart still has a port to probe.
_DEFAULT_ADMIN_PORT = 47821
#: ``CREATE_BREAKAWAY_FROM_JOB``. Without it a restart helper that was spawned
#: from a process inside a Windows Job Object dies with its parent — the
#: window closes and nothing comes back (BUG-181).
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def build_launch_command(executable: str) -> list[str]:
    """Argv that boots a fresh desktop app through its stable OS identity.

    A macOS desktop restart always re-enters through LaunchServices so it can
    never attach TCC access to a raw Python interpreter.  A missing or invalid
    bundle therefore fails closed; the managed installer/repair path owns
    recreating it.
    """
    from jarvis.core.instance import current_instance

    identity = current_instance()
    fallback = [executable, "-m", LAUNCHER_MODULE, *identity.launcher_args]
    if sys.platform == "darwin" and not identity.is_default:
        # The bundle is the DEFAULT app's stable identity and LaunchServices
        # does not carry ``JARVIS_INSTANCE`` into it — re-entering through it
        # would bring the dev instance back as a second default app. A dev
        # instance restarts through the interpreter directly (it never owns the
        # microphone-bound duties the TCC attachment is for).
        return fallback
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
    if sys.platform == "win32":
        branded = _existing_branded_launcher()
        if branded is not None:
            # Launch the branded exe itself so the PID we watch IS the window
            # process. Spawning ``python -m launcher`` used to re-exec through
            # this copy and exit; the helper treated that exit as a bounce and
            # started two more copies that raced the first (BUG-181).
            return [str(branded), "-m", LAUNCHER_MODULE, *identity.launcher_args]
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
    layers, including config-soll.json, keeps being overridden  # i18n-allow: filename
    by the stale inherited copy on every ``restart-app`` — live case
    2026-07-17: the TTS voice pin kept resurrecting a replaced voice. Only
    ``JARVIS__*`` keys (the pydantic config-override namespace) are refreshed;
    everything else stays inherited except per-process launch guards, which must
    reset for a genuinely new launch chain.
    """
    env = dict(os.environ if base is None else base)
    # This is a one-process loop guard set only on the branded launcher child.
    # An in-app restart starts a genuinely new launch chain, so inheriting the
    # marker would make that chain skip branding and fall back to pythonw.exe's
    # Python icon. Match case-insensitively because Windows environment names are
    # case-insensitive even though a supplied test/POSIX mapping may not be.
    for transient in [key for key in env if key.upper() == WINDOWS_BRANDED_LAUNCH_ENV_VAR.upper()]:
        del env[transient]
    persisted = _read_persisted()
    if persisted is None:
        return env
    # Drop every inherited JARVIS__* key first (Windows env names are
    # case-insensitive, so spelling variants must not survive alongside the
    # refreshed names), then lay down the persisted set verbatim.
    for stale in [k for k in env if k.upper().startswith("JARVIS__") and k not in persisted]:
        del env[stale]
    env.update(persisted)
    return env


def detached_creationflags() -> int:
    """Windows creationflags that make a child outlive its parent, windowless.

    ``DETACHED_PROCESS`` cuts the child loose from the parent's console/process
    group; ``CREATE_NO_WINDOW`` keeps ``pythonw`` from flashing a console;
    ``CREATE_BREAKAWAY_FROM_JOB`` lets the child survive if the parent is
    inside a Job Object that kills its members on close (the "Restart
    shut the window and nothing came back" failure). ``0`` on non-Windows
    (the caller uses ``start_new_session`` there instead).
    """
    if sys.platform == "win32":
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", _CREATE_BREAKAWAY_FROM_JOB)
        return detached | no_window | breakaway
    return 0


def detached_popen_kwargs(
    *, cwd: str | None = None, env: dict[str, str] | None = None
) -> dict[str, object]:
    """Popen kwargs for a child that must outlive this process, with valid stdio.

    ``DETACHED_PROCESS`` leaves stdin/stdout/stderr as invalid handles unless
    they are redirected; a boot-time write then kills the child before a
    window appears (the same defect ``maybe_reexec_through_branded_launcher``
    already fixed for the Start-Menu path).
    """
    kwargs: dict[str, object] = {
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
    if sys.platform == "win32":
        kwargs["creationflags"] = detached_creationflags()
    else:
        kwargs["start_new_session"] = True
    return kwargs


def spawn_detached(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    _popen: object | None = None,
) -> object:
    """Spawn ``argv`` so it outlives this process.

    Retries once without ``CREATE_BREAKAWAY_FROM_JOB`` when Windows refuses
    the flag (the parent is in a job that forbids breakaway — WinError 5).
    The child still starts; it just stays in the parent's job.
    """
    popen = _popen or subprocess.Popen
    kwargs = detached_popen_kwargs(cwd=cwd, env=env)
    flags = int(kwargs.get("creationflags", 0) or 0)
    try:
        return popen(argv, **kwargs)  # type: ignore[operator]  # noqa: S603
    except PermissionError:
        if sys.platform != "win32" or not (flags & _CREATE_BREAKAWAY_FROM_JOB):
            raise
        kwargs = dict(kwargs)
        kwargs["creationflags"] = flags & ~_CREATE_BREAKAWAY_FROM_JOB
        logging.getLogger(__name__).warning(
            "relauncher spawn denied CREATE_BREAKAWAY_FROM_JOB; retrying without it"
        )
        return popen(argv, **kwargs)  # type: ignore[operator]  # noqa: S603


def _existing_branded_launcher() -> Path | None:
    """Return an already-built branded launcher exe, or ``None``.

    Cheap existence check only — never copies, never smoke-starts. The Start
    Menu path owns creating the file; a restart just needs to find it.
    """
    if sys.platform != "win32":
        return None
    from jarvis.core.instance import current_instance

    name = current_instance().windows_branded_launcher_file_name
    candidates: list[Path] = [Path(sys.executable).with_name(name)]
    base = getattr(sys, "_base_executable", "") or ""
    if base:
        candidates.append(Path(base).with_name(name))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / WINDOWS_BRANDED_LAUNCHER_DIR_NAME / "bin" / name)
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                return cand
        except OSError:
            continue  # unreadable candidate; try the next home
    return None


def _apply_branded_launch_env(env: dict[str, str], argv: list[str]) -> dict[str, str]:
    """Mark a branded-exe spawn so the child does not re-exec itself.

    ``fresh_user_env`` strips the one-process branding loop guard (a restart
    is a new launch chain). When THIS spawn *is* the branded exe, put the
    guard back so the child boots in-process and the PID we watch stays up.
    """
    if sys.platform != "win32" or not argv:
        return env
    from jarvis.core.instance import current_instance

    branded_name = current_instance().windows_branded_launcher_file_name.casefold()
    if Path(argv[0]).name.casefold() != branded_name:
        return env
    env = dict(env)
    env[WINDOWS_BRANDED_LAUNCH_ENV_VAR] = "1"
    branded = Path(argv[0])
    if (branded.parent.parent / "pyvenv.cfg").is_file():
        env.pop("__PYVENV_LAUNCHER__", None)
    else:
        venv_pythonw = Path(sys.executable).with_name("pythonw.exe")
        if venv_pythonw.is_file():
            env["__PYVENV_LAUNCHER__"] = str(venv_pythonw)
    return env


def _restart_admin_port(cwd: str | Path | None = None) -> int:
    """Admin port the fresh instance will bind, including the instance offset."""
    from jarvis.core.instance import current_instance

    base = _DEFAULT_ADMIN_PORT
    root = Path(cwd) if cwd else None
    if root is not None:
        cfg = root / CONFIG_FILE_NAME
        try:
            import tomllib

            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
            port = data.get("ui", {}).get("admin_api_port")
            if isinstance(port, int) and 1 <= port <= 65535:
                base = port
        except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
            pass  # unreadable jarvis.toml → packaged default port
    return current_instance().port(base)


def _desktop_is_serving(
    *,
    cwd: str | Path | None = None,
    host: str = "127.0.0.1",
    timeout: float = 0.25,
    _connect=None,
) -> bool:
    """True when something is already accepting on this instance's admin port.

    Used to recognise a successful Windows branded hand-off: the python
    launcher PID dies on purpose after spawning ``PersonalJarvis.exe``, and
    the new window process is a different PID. A listening port is the
    honest "the app came back" signal.
    """
    import socket

    connect = _connect or socket.create_connection
    port = _restart_admin_port(cwd)
    try:
        with connect((host, port), timeout=timeout):
            return True
    except OSError:
        return False  # nothing accepted; that is the probe result


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
    _serving=None,
    checks: int = 8,
    interval: float = 1.0,
) -> bool:
    """True if a freshly spawned launcher actually came up.

    Two success signals, because they diverge on Windows:

    * The spawn PID stays alive for the whole grace — an in-process boot
      (macOS bundle, Linux, a python that did not re-exec).
    * The desktop admin port starts accepting — the Windows branded hand-off.
      ``python -m launcher`` (and even a branded spawn that still re-execs)
      exits within a second after starting ``PersonalJarvis.exe``. Treating
      that exit as a bounce used to spawn two more copies that raced the
      first, and sometimes none of them kept the window (BUG-181).

    A true bounce (already-running secondary) dies AND never binds the
    port, so both signals stay false. An unverifiable pid is treated as
    success to avoid spinning needlessly.
    """
    serving = _serving if _serving is not None else _desktop_is_serving
    if not isinstance(pid, int) or pid <= 0:
        return True
    still_alive = True
    for _ in range(checks):
        _sleep(interval)
        if serving():
            return True
        if not _alive(pid):
            still_alive = False
    return still_alive or serving()


def _read_pending_update(root: Path) -> dict[str, object] | None:
    """Read and strictly validate a relaunch-time update transaction."""

    try:
        payload = json.loads((root / PENDING_UPDATE_FILENAME).read_text(encoding="utf-8"))
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


def _run_update_command(cmd: list[str], *, root: Path, timeout: float) -> int:
    """Run a windowless update child and collapse every launch failure to -1."""

    kwargs: dict[str, object] = {
        "cwd": str(root),
        "env": {key: value for key, value in os.environ.items() if key != "JARVIS_INSTALL_NO_PIP"},
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
        if (
            _run_update_command(
                ["git", "ls-files", "--error-unmatch", "--", rel],
                root=root,
                timeout=30.0,
            )
            != 0
        ):
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


#: Precious, small user state that must survive ANY update outcome. Copied to
#: a sibling directory OUTSIDE the checkout before the first ``git reset`` so
#: even a catastrophic tree replacement (the 2026-07-20 wipe lost API keys,
#: config, and wiki pages) can be recovered by hand. Large data/ databases are
#: deliberately excluded: git never touches untracked files, and a full copy
#: of a multi-hundred-MB data dir per update is not "best-effort" territory.
_STATE_SNAPSHOT_ITEMS = (
    "jarvis.toml",
    ".env",
    Path("data") / "credentials.json",
    Path("wiki") / "obsidian-vault",
)
_STATE_SNAPSHOT_KEEP = 3


def snapshot_user_state(root: Path) -> Path | None:
    """Copy precious user state to ``<root>.pre-update-state/<timestamp>/``.

    Best-effort belt-and-suspenders: an update must proceed even when the
    snapshot fails, and a snapshot failure must never raise. Returns the
    snapshot directory when at least one item was saved, else ``None``.
    Old snapshots beyond the newest ``_STATE_SNAPSHOT_KEEP`` are pruned.
    """
    import shutil

    base = root.parent / (root.name + ".pre-update-state")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = base / stamp
    saved = False
    for item in _STATE_SNAPSHOT_ITEMS:
        src = root / item
        try:
            if not src.exists():
                continue
            dst = target / item
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
                try:
                    # credentials.json/.env carry secrets; match the 0600
                    # discipline of their creation sites (no-op on Windows).
                    os.chmod(dst, 0o600)
                except OSError:
                    pass
            saved = True
        except OSError:
            logging.getLogger(__name__).warning(
                "pre-update snapshot could not save %s", src, exc_info=True
            )
    if not saved:
        return None
    try:
        stamps = sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)
        for old in stamps[_STATE_SNAPSHOT_KEEP:]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass
    return target


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

    snapshot_user_state(root)

    previous = str(payload["previous_revision"])
    target = str(payload["target_revision"])
    profile = str(payload["profile"])

    target_reset_ok = (
        _run_update_command(["git", "reset", "--hard", target], root=root, timeout=120.0) == 0
    )
    target_install_ok = False
    if target_reset_ok:
        target_install_ok = _run_update_command(
            _installer_command(root, profile), root=root, timeout=3600.0
        ) == 0 and _ui_bundle_ready(root)
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
        _run_update_command(["git", "reset", "--hard", previous], root=root, timeout=120.0) == 0
    )
    rollback_install_ok = False
    if rollback_reset_ok:
        rollback_install_ok = _run_update_command(
            _installer_command(root, profile), root=root, timeout=3600.0
        ) == 0 and _ui_bundle_ready(root)
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
    _serving=None,
    _report=None,
    attempts: int = 3,
) -> int:
    """Wait for the old app to exit, then start a fresh launcher — verified.

    The single-instance lock frees only once the old process is gone, so we
    wait for that first. After spawning the new launcher we verify it actually
    came up — either the spawn PID stayed alive, or (on Windows) the branded
    child bound the admin port after this PID handed off. Retry only when
    neither signal is present, so a branded hand-off is not mistaken for a
    bounce and doubled.

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
    _report = _report or _report_restart_failure
    serving = _serving if _serving is not None else (lambda: _desktop_is_serving(cwd=cwd))

    try:
        from jarvis.ui.desktop_log import _install_desktop_log_sink, desktop_log_path

        _install_desktop_log_sink(desktop_log_path())
        logging.getLogger(__name__).info("relauncher: start parent_pid=%s cwd=%s", pid, cwd)
    except Exception:  # noqa: BLE001, S110 — a mute helper is what we had before
        pass

    cmd = build_launch_command(sys.executable)
    env = _apply_branded_launch_env(fresh_user_env(), cmd)

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

        proc = spawn_detached(cmd, cwd=cwd, env=env, _popen=_spawn)
        new_pid = getattr(proc, "pid", None)
        logging.getLogger(__name__).info(
            "relauncher: spawn attempt %s pid=%s cmd=%s",
            attempt + 1,
            new_pid,
            cmd[0] if cmd else "",
        )
        if _settled(new_pid, _alive=_alive, _sleep=_sleep, _serving=serving):
            logging.getLogger(__name__).info("relauncher: new instance is up (pid=%s)", new_pid)
            return 0
    _report(pid, still_alive=_alive(pid))
    return 1


def _report_restart_failure(old_pid: int, *, still_alive: bool) -> None:
    """Say out loud that the restart did not bring the app back.

    The relauncher is a windowless helper: its return code goes nowhere and the
    app it was replacing is gone, so a failed restart used to look exactly like
    "the app closed and never came back" — until the user found the Start menu
    (live incident 2026-08-25). One native box, best-effort, never raises.
    """
    if still_alive:
        message = (
            f"{PRODUCT_NAME} could not restart: the previous instance (process "
            f"{old_pid}) has not exited. End it in the task manager, then start "
            f"{PRODUCT_NAME} from the Start menu."
        )
    else:
        message = (
            f"{PRODUCT_NAME} could not restart: the new instance did not stay up. "
            f"Start {PRODUCT_NAME} from the Start menu; the log file has the details."
        )
    logging.getLogger(__name__).error(message)
    try:
        from jarvis.core.process_utils import standard_error_is_visible

        if standard_error_is_visible():
            print(message, file=sys.stderr, flush=True)
            return  # a readable stderr (terminal, pipe, test capture) is enough
        from jarvis.ui.native_dialog import show_error_dialog

        show_error_dialog(f"{PRODUCT_NAME} restart failed", message)
    except Exception:  # noqa: BLE001, S110 — a helper that cannot show a box stays quiet
        pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
