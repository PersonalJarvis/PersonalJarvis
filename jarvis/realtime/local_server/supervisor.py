"""Lifecycle owner for the managed local realtime server.

The ONE spawn/stop/warm/ownership path. Before this module existed the server
was started in two racing places (the desktop warm worker and the provider's
connect-revive), nothing recorded which process was OURS, nothing could stop
it deliberately, and the Ollama brain model was evicted after five idle
minutes so the first turn after a pause paid a cold model load.

Design decisions (plan 2026-08-08):

- **Ownership is the pidfile**, ``local_realtime_server.pid.json`` in the data
  dir: ``{pid, create_time, port, command, spawned_at}`` — never environment
  or secrets. A port probe answers "is something serving" (reachability) and
  is NEVER a kill criterion: a foreign listener on our port must not die for
  standing there.
- **The server intentionally survives app exit.** Instant next connect is the
  entire point of supervising it; ownership exists for deliberate stop /
  uninstall / reinstall, not for exit cleanup.
- **Spawns are refused, not queued**, whenever spawning cannot help: install
  running, port already served, owned process alive, rate limit, non-loopback
  target. The caller gets the reason as ``"refused:<why>"``.

Everything here is read-only-safe to import (AP-26: no work at import time);
heavy imports stay function-local.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Never spawn more often than this — a crash-looping server is marked bad by
#: its refusals, not hammered back up (AP-24 doctrine). Mirrors the provider's
#: historical revive rate limit.
SPAWN_MIN_INTERVAL_S = 60.0

#: How long the Ollama brain model stays resident after a warm ping. Slides on
#: every warm call (the desktop warm worker re-arms after each voice session),
#: so only a genuinely idle multi-hour gap pays a reload.
BRAIN_KEEP_ALIVE = "2h"

_DEFAULT_PORT = 8765

_LOCK = threading.Lock()
_last_spawn_at: float = float("-inf")


# ── Paths ────────────────────────────────────────────────────────────────


def _data_dir() -> Path:
    env_dir = os.environ.get("JARVIS_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip()).resolve()
    from jarvis.core.config import DATA_DIR  # lazy (AP-26)

    return DATA_DIR


def _pidfile() -> Path:
    return _data_dir() / "local_realtime_server.pid.json"


def _server_log() -> Path:
    return _data_dir() / "local_realtime_server.log"


# ── Address handling ─────────────────────────────────────────────────────


def _host_port(base_url: str) -> tuple[str, int]:
    """Host and port of a configured server address, defaults preserved."""
    text = (base_url or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0]
    host, _, port_text = text.partition(":")
    try:
        port = int(port_text) if port_text else _DEFAULT_PORT
    except ValueError:
        port = _DEFAULT_PORT
    return host or "localhost", port


def _is_loopback(host: str) -> bool:
    return host.lower() in {"localhost", "127.0.0.1", "::1"}


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


# ── Ownership (pidfile) ──────────────────────────────────────────────────


def _read_pidfile() -> dict[str, object] | None:
    try:
        raw = _pidfile().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _process_create_time(pid: int) -> float | None:
    """The process's kernel start stamp, or ``None`` when unverifiable."""
    try:
        import psutil  # type: ignore[import-untyped]  # lazy, optional
    except ImportError:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 — gone/denied both mean "not verifiable"
        return None


def _owned_process() -> tuple[int | None, bool]:
    """(pid, alive) of the recorded server, PID-reuse safe.

    ``create_time`` recorded at spawn is compared against the live process:
    a machine reboot can hand the same pid to an innocent process, and a
    match within a second is the difference between "our server" and
    "somebody's browser". Without psutil the answer degrades to "recorded
    but unverifiable" — reported as not alive so no kill path ever trusts it.
    """
    record = _read_pidfile()
    if record is None:
        return None, False
    try:
        pid = int(record.get("pid", 0) or 0)
    except (TypeError, ValueError):
        return None, False
    if pid <= 0:
        return None, False
    live_created = _process_create_time(pid)
    if live_created is None:
        return pid, False
    recorded = record.get("create_time")
    try:
        recorded_f = float(recorded) if recorded is not None else None
    except (TypeError, ValueError):
        recorded_f = None
    if recorded_f is None:
        return pid, False
    return pid, abs(live_created - recorded_f) < 1.0


def _write_pidfile(pid: int, port: int, command: str) -> None:
    payload = {
        "pid": pid,
        "create_time": _process_create_time(pid),
        "port": port,
        "command": command,
        "spawned_at": time.time(),
    }
    try:
        _pidfile().parent.mkdir(parents=True, exist_ok=True)
        _pidfile().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        log.warning("local-realtime supervisor: pidfile write failed", exc_info=True)


def clear_pidfile() -> None:
    try:
        _pidfile().unlink(missing_ok=True)
    except OSError:  # pragma: no cover — best effort
        log.debug("local-realtime supervisor: pidfile unlink failed", exc_info=True)


# ── Environment ──────────────────────────────────────────────────────────


def hardened_child_env(*, inject_openai_key: bool) -> dict[str, str]:
    """The spawn environment every managed-server child gets.

    One place for the whole hardening story (previously duplicated between
    the provider revive and the installer smoke boot):

    - faulthandler + unbuffered output so a native crash finally names its
      faulting module in the server log;
    - ``HF_HUB_DISABLE_SYMLINKS`` on WINDOWS only — the symlinked snapshot
      layout dies with WinError 1314 without the symlink privilege (live
      2026-08-07); on macOS/Linux symlinks work and save gigabytes;
    - optionally the OpenAI key from the keyring for the cloud-brained
      managed server (secrets never enter the persisted launch command).
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONFAULTHANDLER", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if os.name == "nt":
        env.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    if inject_openai_key and not env.get("OPENAI_API_KEY"):
        try:
            from jarvis.core.config import get_secret  # lazy (AP-26)

            key = get_secret("openai_api_key", env_fallback="OPENAI_API_KEY") or ""
            if key:
                env["OPENAI_API_KEY"] = key
        except Exception:  # noqa: BLE001 — a secretless host is a valid host
            log.debug("supervisor: OpenAI key lookup failed", exc_info=True)
    return env


# ── Status ───────────────────────────────────────────────────────────────


def status(base_url: str = "") -> dict[str, object]:
    """Live runtime view: ``{reachable, port, pid, owned, stale}``.

    ``reachable`` is the port probe (something answers), ``owned`` whether the
    recorded pid verifiably runs, ``stale`` whether a pidfile exists whose
    process is gone. ``reachable and not owned`` is the honest "another
    process serves this port" signal the UI can render.
    """
    _host, port = _host_port(base_url)
    pid, alive = _owned_process()
    return {
        "reachable": _port_open(port),
        "port": port,
        "pid": pid,
        "owned": alive,
        "stale": pid is not None and not alive,
    }


# ── Spawn ────────────────────────────────────────────────────────────────


def ensure_running(*, launch_command: str, base_url: str, reason: str) -> str:
    """Start the server if — and only if — starting can help.

    Returns ``"already-running"`` (port served, or our recorded process is
    alive and presumably booting), ``"spawned"``, or ``"refused:<why>"``.
    The single spawn path for BOTH the boot-time prewarm and the connect-time
    revive, which is what makes their race harmless.
    """
    command = (launch_command or "").strip()
    if not command:
        return "refused:no-launch-command"
    host, port = _host_port(base_url)
    if not _is_loopback(host):
        return "refused:not-local"
    with _LOCK:
        if _port_open(port):
            return "already-running"
        pid, alive = _owned_process()
        if alive:
            # Our server process exists but has not bound its port yet: it is
            # mid-boot (models loading). A second spawn would fight it for
            # the GPU and the port.
            return "already-running"
        try:
            from jarvis.realtime.local_server import install  # lazy (AP-26)

            if install.snapshot().get("running"):
                # Spawning a half-installed venv mid-install proves nothing
                # and locks files the installer is about to replace.
                return "refused:install-running"
        except Exception:  # noqa: BLE001 — install state is advisory here
            log.debug("supervisor: install snapshot unavailable", exc_info=True)
        global _last_spawn_at
        now = time.monotonic()
        if now - _last_spawn_at < SPAWN_MIN_INTERVAL_S:
            return "refused:rate-limited"
        _last_spawn_at = now
        spawned_pid = _spawn(command, reason=reason)
        if spawned_pid is None:
            return "refused:spawn-failed"
        _write_pidfile(spawned_pid, port, command)
        log.info(
            "local-realtime supervisor: spawned server pid=%s (%s)",
            spawned_pid,
            reason,
        )
        return "spawned"


def _spawn(command: str, *, reason: str) -> int | None:
    """Detached, window-less, log-sinked spawn. ``None`` when it failed."""
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # lazy

    try:
        _server_log().parent.mkdir(parents=True, exist_ok=True)
        sink = open(_server_log(), "ab")  # noqa: SIM115 — handed to the child
    except OSError as exc:
        log.warning(
            "supervisor: could not open %s (%s); child runs without a log",
            _server_log(),
            exc,
        )
        sink = None
    try:
        argv: str | list[str]
        popen_kwargs: dict[str, object] = {}
        if os.name == "nt":
            argv = command
        else:
            import shlex

            argv = shlex.split(command)
            # Its own session/process group, so a deliberate stop can take the
            # whole tree down with killpg instead of orphaning workers.
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(  # noqa: S603 — the maintainer configured this command
            argv,
            stdin=subprocess.DEVNULL,
            stdout=sink or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            env=hardened_child_env(inject_openai_key=True),
            **popen_kwargs,
        )
    except Exception as exc:  # noqa: BLE001 — a bad command must not kill the caller
        log.warning(
            "supervisor: spawning the server failed (%s: %s) — check "
            "[brain.providers.local-realtime].launch_command (%s)",
            type(exc).__name__,
            exc,
            reason,
        )
        return None
    finally:
        if sink is not None:
            sink.close()
    return proc.pid


# ── Stop ─────────────────────────────────────────────────────────────────


def stop(*, owned_only: bool = True, install_root: Path | None = None) -> tuple[bool, str]:
    """Stop the server this install owns. ``(changed, message)``.

    Kill order: the pidfile's verified pid (create_time match) first; when
    that cannot answer and ``install_root`` is given, fall back to the
    install-tree needle scan (kills only processes running out of the managed
    venv). A port match is never a kill criterion. With ``owned_only`` there
    is no further escalation — a foreign listener stays untouched.
    """
    pid, alive = _owned_process()
    if alive and pid is not None:
        ok = _kill_pid_tree(pid)
        clear_pidfile()
        return ok, f"stopped pid {pid}" if ok else f"could not stop pid {pid}"
    if pid is not None and not alive:
        clear_pidfile()
    if install_root is not None:
        killed = _kill_by_install_root(install_root)
        if killed:
            return True, f"stopped {killed} process(es) from {install_root}"
    if not owned_only:
        log.info("supervisor: no owned server process found to stop")
    return False, "no owned server process found"


def _kill_pid_tree(pid: int) -> bool:
    try:
        if os.name == "nt":
            from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # lazy

            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            return True
        import signal

        # The spawn used start_new_session, so the pid IS the group leader:
        # SIGTERM the group, grace, then SIGKILL what remains.
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _process_create_time(pid) is None and not _pid_exists(pid):
                return True
            time.sleep(0.2)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)
        return True
    except Exception:  # noqa: BLE001 — best-effort teardown, reported honestly
        log.warning("supervisor: kill of pid %s incomplete", pid, exc_info=True)
        return False


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_by_install_root(root: Path) -> int:
    """Kill processes running out of the managed tree. Needs psutil; else 0."""
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        log.debug("supervisor: psutil unavailable — skipping needle scan")
        return 0
    needle = str(root).lower()
    killed = 0
    for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
        try:
            exe = (proc.info.get("exe") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or ()).lower()
            if needle in exe or needle in cmdline:
                proc.kill()
                proc.wait(timeout=10)
                killed += 1
        except (psutil.Error, OSError):  # pragma: no cover — best effort
            continue
    return killed


# ── Brain warm-up ────────────────────────────────────────────────────────


def warm_brain(*, launch_command: str, timeout: float = 5.0) -> bool:
    """Make the Ollama brain model resident BEFORE the first turn needs it.

    Reads the brain endpoint out of the launch command itself (a capability
    of the configured artifact, never a provider-name check): with an
    ``--responses_api_base_url`` pointing at an Ollama ``/v1`` root, one
    ``/api/generate`` ping with ``keep_alive`` loads the model and keeps it
    in memory. Without it, Ollama evicts after five idle minutes and the
    first sentence after a pause pays a multi-second cold load. Best-effort:
    a non-Ollama endpoint simply answers 404 and nothing changes.
    """
    model, brain_url = _brain_endpoint(launch_command)
    if not model or not brain_url:
        return False
    root = brain_url[: -len("/v1")] if brain_url.endswith("/v1") else brain_url
    payload = json.dumps(
        {"model": model, "prompt": "", "keep_alive": BRAIN_KEEP_ALIVE, "stream": False}
    ).encode("utf-8")
    import urllib.error
    import urllib.request

    url = f"{root}/api/generate"
    if not url.startswith(("http://", "https://")):
        return False
    request = urllib.request.Request(  # noqa: S310 — scheme checked above
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310
            pass
    except (urllib.error.URLError, OSError, ValueError):
        log.debug("supervisor: brain warm ping failed for %s", url, exc_info=True)
        return False
    log.info(
        "local-realtime supervisor: brain model %s warmed (keep_alive=%s)",
        model,
        BRAIN_KEEP_ALIVE,
    )
    return True


def _brain_endpoint(launch_command: str) -> tuple[str, str]:
    """(model, responses_api_base_url) parsed from a launch command."""
    try:
        import shlex

        tokens = shlex.split(launch_command or "", posix=os.name != "nt")
    except ValueError:
        return "", ""
    model = ""
    base = ""
    for index, flag in enumerate(tokens[:-1]):
        if flag == "--model_name":
            model = tokens[index + 1].strip('"')
        elif flag == "--responses_api_base_url":
            base = tokens[index + 1].strip('"').rstrip("/")
    return model, base


# ── Test support ─────────────────────────────────────────────────────────


def _reset_for_tests() -> None:
    """Reset module-level state (rate limit) between tests."""
    global _last_spawn_at
    _last_spawn_at = float("-inf")
