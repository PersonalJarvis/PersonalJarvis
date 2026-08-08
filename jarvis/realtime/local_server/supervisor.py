"""Lifecycle owner for the managed local realtime server.

The ONE spawn/stop/warm/ownership path. Before this module existed the server
was started in two racing places (the desktop warm worker and the provider's
connect-revive), nothing recorded which process was OURS, nothing could stop
it deliberately, and the Ollama brain model was evicted after five idle
minutes so the first turn after a pause paid a cold model load.

Design decisions (plan 2026-08-08):

- **Ownership is an atomically replaced pidfile** plus an exclusive
  cross-process spawn lock. A thread lock cannot serialize two Jarvis
  processes. Neither file stores environment variables or secrets.
- **Readiness is the model-pool probe**, not a TCP accept. ``/v1/pool`` exists
  only after the speech pipelines are constructed. Port reachability remains
  diagnostic and is NEVER a kill criterion.
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
import re
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import SplitResult, urlsplit, urlunsplit

log = logging.getLogger(__name__)

#: Never spawn more often than this — a crash-looping server is marked bad by
#: its refusals, not hammered back up (AP-24 doctrine). Mirrors the provider's
#: historical revive rate limit.
SPAWN_MIN_INTERVAL_S = 60.0

# Installed checkpoints are already local after the mandatory smoke boot;
# 120 seconds still covers the measured 90-second CUDA graph cold start.
RUNTIME_READY_TIMEOUT_S = 120.0
RUNTIME_READY_POLL_S = 0.5
OWNED_STARTUP_TIMEOUT_S = RUNTIME_READY_TIMEOUT_S

# The managed process is meant to be warm before a user calls. Poll its
# ownership cheaply once a second so an idle native crash starts recovering
# immediately; probe the full model pool less often to catch a live-but-wedged
# child without filling the server log with health requests.
RUNTIME_MONITOR_POLL_S = 1.0
RUNTIME_MONITOR_POOL_INTERVAL_S = 30.0
RUNTIME_MONITOR_UNREADY_GRACE_S = 30.0

_MAX_POOL_RESPONSE_BYTES = 64 * 1024

#: How long the Ollama brain model stays resident after a warm ping. Slides on
#: every warm call (the desktop warm worker re-arms after each voice session),
#: so only a genuinely idle multi-hour gap pays a reload.
BRAIN_KEEP_ALIVE = "2h"

_DEFAULT_PORT = 8765

_WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_WINDOWS_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_WINDOWS_BREAKAWAY_FROM_JOB = getattr(
    subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000
)

_LOCK = threading.Lock()
_last_spawn_at: float = float("-inf")
_MONITOR_LOCK = threading.Lock()
_monitor_thread: threading.Thread | None = None
_monitor_stop: threading.Event | None = None
_monitor_key: tuple[str, str] | None = None


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


def _spawn_lock() -> Path:
    return _data_dir() / "local_realtime_server.spawn.lock"


# ── Address handling ─────────────────────────────────────────────────────


def _split_base_url(base_url: str) -> SplitResult:
    """Parse a local endpoint without DNS work and normalize WS schemes."""
    text = (base_url or "").strip() or f"http://127.0.0.1:{_DEFAULT_PORT}"
    if "://" not in text:
        text = f"http://{text}"
    parsed = urlsplit(text)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme.lower(), parsed.scheme.lower())
    return parsed._replace(scheme=scheme or "http")


def _host_port(base_url: str) -> tuple[str, int]:
    """Host and port of a configured server address, IPv6-safe."""
    parsed = _split_base_url(base_url)
    try:
        port = parsed.port or _DEFAULT_PORT
    except ValueError:
        port = _DEFAULT_PORT
    return parsed.hostname or "127.0.0.1", port


def _is_loopback(host: str) -> bool:
    return host.lower() in {"localhost", "127.0.0.1", "::1"}


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _pool_url(base_url: str) -> str:
    """Return the pinned server's HTTP model-pool endpoint."""
    parsed = _split_base_url(base_url)
    host = parsed.hostname or "127.0.0.1"
    if host.lower() == "localhost":
        host = "127.0.0.1"
    host_text = f"[{host}]" if ":" in host else host
    try:
        port = parsed.port or _DEFAULT_PORT
    except ValueError:
        port = _DEFAULT_PORT
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = host_text if port == default_port else f"{host_text}:{port}"
    path = parsed.path.rstrip("/")
    if path.endswith("/realtime"):
        path = path[: -len("/realtime")].rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, netloc, f"{path}/pool", "", ""))


def probe_runtime(base_url: str, timeout: float = 0.75) -> dict[str, int] | None:
    """Return a sanitized live pool snapshot, or ``None`` when not ready.

    A TCP accept only proves that a socket exists. The pinned server mounts
    ``/v1/pool`` after constructing every speech pipeline, so a valid response
    is the first point at which a realtime handshake can actually succeed.
    Session identifiers and arbitrary server payload fields are deliberately
    discarded before anything reaches logs or the UI.
    """
    parsed = _split_base_url(base_url)
    host, _port = _host_port(base_url)
    if parsed.scheme not in {"http", "https"} or not _is_loopback(host):
        return None
    import http.client

    url = _pool_url(base_url)
    target = urlsplit(url)
    target_host = target.hostname
    if target_host is None:
        return None
    connection_type = (
        http.client.HTTPSConnection
        if target.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(
        target_host,
        target.port,
        timeout=max(0.05, timeout),
    )
    try:
        connection.request("GET", target.path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            return None
        raw = response.read(_MAX_POOL_RESPONSE_BYTES + 1)
    except (http.client.HTTPException, OSError, TimeoutError, ValueError):
        return None
    finally:
        connection.close()
    if len(raw) > _MAX_POOL_RESPONSE_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    size = payload.get("size")
    in_use = payload.get("in_use")
    units = payload.get("units")
    if (
        type(size) is not int
        or type(in_use) is not int
        or size < 1
        or not 0 <= in_use <= size
        or not isinstance(units, list)
        or len(units) != size
    ):
        return None
    allowed_states = {"idle", "active", "draining", "stuck"}
    states: list[str] = []
    for unit in units:
        if not isinstance(unit, dict):
            return None
        state = unit.get("state")
        if not isinstance(state, str) or state not in allowed_states:
            return None
        states.append(state)
    return {
        "size": size,
        "in_use": in_use,
        "available": size - in_use,
        "stuck": states.count("stuck"),
    }


def wait_until_ready(
    base_url: str,
    *,
    timeout: float = RUNTIME_READY_TIMEOUT_S,
    poll_interval: float = RUNTIME_READY_POLL_S,
    launch_command: str = "",
    cleanup_on_timeout: bool = False,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Wait until the managed model pool, not merely TCP, answers.

    A timed-out managed child is torn down when requested so it cannot remain
    as an owned-but-never-ready zombie. Cancellation is different from a
    timeout: callers stopping the server set ``cancel_event`` and no second
    lifecycle operation is started from this waiter.
    """
    cleanup_root = managed_install_root(launch_command) if cleanup_on_timeout else None
    expected_generation = _owned_generation() if cleanup_root is not None else None
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return False
        if probe_runtime(base_url, timeout=min(0.75, max(0.05, timeout))) is not None:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if cancel_event is not None and cancel_event.is_set():
                return False
            if cleanup_root is not None and expected_generation is not None:
                outcome, message = _cleanup_timed_out_generation(
                    base_url=base_url,
                    install_root=cleanup_root,
                    expected_generation=expected_generation,
                )
                if outcome == "ready":
                    return True
                log.warning(
                    "local-realtime supervisor: readiness timed out; cleanup %s (%s)",
                    outcome,
                    message,
                )
            return False
        delay = min(max(0.01, poll_interval), remaining)
        if cancel_event is None:
            time.sleep(delay)
        elif cancel_event.wait(delay):
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


def _json_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _json_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


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
    pid = _json_int(record.get("pid"))
    if pid is None:
        return None, False
    if pid <= 0:
        return None, False
    live_created = _process_create_time(pid)
    if live_created is None:
        return pid, False
    recorded_f = _json_float(record.get("create_time"))
    if recorded_f is None:
        return pid, False
    return pid, abs(live_created - recorded_f) < 1.0


def _owned_generation() -> tuple[int, float, str] | None:
    """Verified identity of the exact child generation in the pidfile."""
    record = _read_pidfile()
    if record is None:
        return None
    pid = _json_int(record.get("pid"))
    created = _json_float(record.get("create_time"))
    token = record.get("spawn_token")
    if pid is None or pid <= 0 or created is None or not isinstance(token, str) or not token:
        return None
    live_created = _process_create_time(pid)
    if live_created is None or abs(live_created - created) >= 1.0:
        return None
    return pid, created, token


def _verified_owned_command() -> str | None:
    """Launch command for the exact live process recorded in the pidfile."""
    record = _read_pidfile()
    if record is None:
        return None
    pid = _json_int(record.get("pid"))
    created = _json_float(record.get("create_time"))
    command = record.get("command")
    if (
        pid is None
        or pid <= 0
        or created is None
        or not isinstance(command, str)
        or not command.strip()
    ):
        return None
    live_created = _process_create_time(pid)
    if live_created is None or abs(live_created - created) >= 1.0:
        return None
    return command.strip()


def _atomic_write_json(path: Path, payload: dict[str, object]) -> bool:
    """Durably replace one small ownership file without partial JSON."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True
    except OSError:
        log.warning("local-realtime supervisor: atomic ownership write failed", exc_info=True)
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            log.debug("supervisor: temporary ownership cleanup failed", exc_info=True)


def _write_pidfile(pid: int, port: int, command: str) -> bool:
    create_time = _process_create_time(pid)
    if create_time is None:
        log.error(
            "local-realtime supervisor: cannot verify create time for spawned pid %s",
            pid,
        )
        return False
    payload = {
        "pid": pid,
        "create_time": create_time,
        "port": port,
        "command": command,
        "spawned_at": time.time(),
        "spawn_token": uuid.uuid4().hex,
    }
    return _atomic_write_json(_pidfile(), payload)


def _try_lock_file(handle: BinaryIO) -> bool:
    """Acquire one non-blocking OS lock, automatically released on death."""
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt  # lazy, Windows only

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # lazy, POSIX only

            fcntl.flock(  # type: ignore[attr-defined]
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
            )
        return True
    except OSError:
        return False


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt  # lazy, Windows only

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # lazy, POSIX only

        fcntl.flock(  # type: ignore[attr-defined]
            handle.fileno(),
            fcntl.LOCK_UN,  # type: ignore[attr-defined]
        )


@contextmanager
def _exclusive_spawn_guard() -> Iterator[bool]:
    """Cross-process lifecycle lease backed by a kernel-held file lock."""
    path = _spawn_lock()
    handle: BinaryIO | None = None
    acquired = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        acquired = _try_lock_file(handle)
    except OSError:
        log.warning("supervisor: lifecycle lease creation failed", exc_info=True)
    try:
        yield acquired
    finally:
        if handle is not None:
            if acquired:
                try:
                    _unlock_file(handle)
                except OSError:
                    log.warning("supervisor: lifecycle lease release failed", exc_info=True)
            handle.close()


@contextmanager
def lifecycle_guard() -> Iterator[bool]:
    """Non-blocking in-process and cross-process lifecycle single-flight."""
    if not _LOCK.acquire(blocking=False):
        yield False
        return
    try:
        with _exclusive_spawn_guard() as acquired:
            yield acquired
    finally:
        _LOCK.release()


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
    """Live ownership plus distinct TCP and model-readiness verdicts."""
    host, port = _host_port(base_url)
    pid, alive = _owned_process()
    reachable = _is_loopback(host) and _port_open(port, timeout=0.25)
    pool = probe_runtime(base_url, timeout=0.5) if reachable else None
    return {
        "reachable": reachable,
        "ready": pool is not None,
        "available": bool(pool and pool["available"] > 0),
        "pool": pool,
        "port": port,
        "pid": pid,
        "owned": alive,
        "stale": pid is not None and not alive,
    }


# ── Spawn ────────────────────────────────────────────────────────────────


def _recorded_spawn_age() -> float | None:
    record = _read_pidfile()
    if record is None:
        return None
    started_at = _json_float(record.get("spawned_at"))
    if started_at is None:
        started_at = _json_float(record.get("create_time"))
    if started_at is None:
        return None
    age = time.time() - started_at
    return age if age >= 0.0 else None


def _recorded_spawn_is_recent() -> bool:
    age = _recorded_spawn_age()
    return age is not None and age < SPAWN_MIN_INTERVAL_S


def _command_references_root(command: str, root: Path) -> bool:
    """Whether a launch token resolves inside the managed install tree."""
    try:
        import shlex

        tokens = shlex.split(command, posix=os.name != "nt")
        resolved_root = root.resolve()
    except (OSError, ValueError):
        return False
    for token in tokens:
        candidate = token.split("=", 1)[-1].strip("\"'")
        if not candidate or not Path(candidate).is_absolute():
            continue
        try:
            if Path(candidate).resolve().is_relative_to(resolved_root):
                return True
        except OSError:
            continue
    return False


def managed_install_root(launch_command: str) -> Path | None:
    """Return the managed install root referenced by a launch command."""
    command = (launch_command or "").strip()
    if not command:
        return None
    try:
        from jarvis.realtime.local_server import install  # lazy (AP-26)

        root = install.install_root()
    except Exception:  # noqa: BLE001 - optional install state is advisory
        log.debug("supervisor: managed install root unavailable", exc_info=True)
        return None
    return root if _command_references_root(command, root) else None


def is_managed_launch_command(launch_command: str) -> bool:
    """Whether the command belongs to Jarvis's pinned managed server."""
    return managed_install_root(launch_command) is not None


_WS_HOST_FLAG = re.compile(
    r"(?<!\S)--ws_host(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)


def _force_loopback_bind(command: str) -> str:
    """Migrate legacy managed commands to a loopback-only server bind."""
    if _WS_HOST_FLAG.search(command):
        return _WS_HOST_FLAG.sub("--ws_host 127.0.0.1", command)
    return f"{command.rstrip()} --ws_host 127.0.0.1"


def _uses_loopback_bind(command: str) -> bool:
    """Whether the effective managed-server bind is explicitly loopback-only."""
    try:
        import shlex

        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return False
    host: str | None = None
    for index, token in enumerate(tokens):
        lowered = token.lower()
        if lowered == "--ws_host":
            if index + 1 >= len(tokens):
                return False
            host = tokens[index + 1]
        elif lowered.startswith("--ws_host="):
            host = token.split("=", 1)[1]
    return host is not None and _is_loopback(host.strip().strip("[]"))


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
    with lifecycle_guard() as guarded:
        if not guarded:
            return "refused:spawn-in-progress"

        managed_root = managed_install_root(command)
        if managed_root is not None:
            # Existing installs predate the loopback flag. Normalize at the
            # actual spawn boundary so they become safe without a reinstall.
            command = _force_loopback_bind(command)
            try:
                from jarvis.realtime.local_server import install  # lazy (AP-26)

                if install.snapshot().get("running"):
                    return "refused:install-running"
            except Exception:  # noqa: BLE001 - install state is advisory here
                log.debug("supervisor: install snapshot unavailable", exc_info=True)

        pool = probe_runtime(base_url, timeout=0.25)
        port_open = pool is not None or _port_open(port, timeout=0.25)
        _pid, alive = _owned_process()
        if managed_root is not None and alive:
            owned_command = _verified_owned_command()
            owned_root = (
                managed_install_root(owned_command) if owned_command is not None else None
            )
            if (
                owned_root is not None
                and owned_command is not None
                and not _uses_loopback_bind(owned_command)
            ):
                # Managed servers survive app exits, so an upgrade can inherit
                # a healthy but LAN-bound legacy generation. Only a verified
                # owned process is eligible for this one-time migration.
                changed, message = _stop_owned_unlocked(
                    owned_only=True,
                    install_root=owned_root,
                )
                if not changed:
                    log.warning(
                        "local-realtime supervisor: unsafe legacy bind migration failed (%s)",
                        message,
                    )
                    return "refused:stuck-process"
                pool = None
                alive = False
                port_open = _port_open(port, timeout=0.25)
        if pool is not None:
            return "already-running"
        if alive:
            age = _recorded_spawn_age()
            if managed_root is None or age is None or age < OWNED_STARTUP_TIMEOUT_S:
                # The child may still be loading. A second process would fight
                # it for GPU memory and corrupt the readiness signal.
                return "already-running"
            changed, message = _stop_owned_unlocked(
                owned_only=True,
                install_root=managed_root,
            )
            if not changed:
                log.warning(
                    "local-realtime supervisor: stale owned process cleanup failed (%s)",
                    message,
                )
                return "refused:stuck-process"
            port_open = _port_open(port, timeout=0.25)
        if port_open:
            # The managed stack has a pinned readiness contract, so an
            # unowned non-protocol listener is a collision. Bring-your-own
            # servers are not required to implement /v1/pool.
            return "refused:port-in-use" if managed_root is not None else "already-running"

        global _last_spawn_at
        now = time.monotonic()
        if now - _last_spawn_at < SPAWN_MIN_INTERVAL_S or _recorded_spawn_is_recent():
            return "refused:rate-limited"

        # A crash between Popen and the old non-atomic pidfile write left an
        # untracked model process. Sweep only OUR managed install tree and
        # only while the cross-process guard is held.
        if managed_root is not None:
            killed, survivors = _kill_by_install_root(managed_root)
            if survivors:
                return "refused:managed-process-survived"
            if killed:
                log.warning(
                    "local-realtime supervisor: removed %s orphan process(es) before spawn",
                    killed,
                )
        clear_pidfile()
        spawned_pid = _spawn(command, reason=reason)
        if spawned_pid is None:
            return "refused:spawn-failed"
        _last_spawn_at = now
        if not _write_pidfile(spawned_pid, port, command):
            _kill_pid_tree(spawned_pid)
            if managed_root is not None:
                _kill_by_install_root(managed_root)
            return "refused:ownership-failed"
        log.info(
            "local-realtime supervisor: spawned server pid=%s (%s)",
            spawned_pid,
            reason,
        )
        return "spawned"


def _server_creationflags(*, platform_name: str | None = None) -> int:
    """Windowless flags that let the managed server survive an app restart."""
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # lazy

    platform = os.name if platform_name is None else platform_name
    if platform != "nt":
        return NO_WINDOW_CREATIONFLAGS
    return (
        _WINDOWS_NO_WINDOW
        | _WINDOWS_DETACHED_PROCESS
        | _WINDOWS_BREAKAWAY_FROM_JOB
    )


def _spawn(command: str, *, reason: str) -> int | None:
    """Detached, window-less, log-sinked spawn. ``None`` when it failed."""

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
        creationflags = _server_creationflags()
        common_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": sink or subprocess.DEVNULL,
            "stderr": subprocess.STDOUT,
            "env": hardened_child_env(inject_openai_key=True),
            **popen_kwargs,
        }
        try:
            proc = subprocess.Popen(  # noqa: S603
                argv,
                creationflags=creationflags,
                **common_kwargs,
            )
        except PermissionError as exc:
            if not creationflags & _WINDOWS_BREAKAWAY_FROM_JOB:
                raise
            # Some Windows hosts forbid explicit job breakaway. Keep the
            # server usable, but report the degraded survive-parent guarantee.
            fallback_flags = creationflags & ~_WINDOWS_BREAKAWAY_FROM_JOB
            log.warning(
                "supervisor: server breakaway was denied (%s); retrying without "
                "CREATE_BREAKAWAY_FROM_JOB",
                exc,
            )
            proc = subprocess.Popen(  # noqa: S603
                argv,
                creationflags=fallback_flags,
                **common_kwargs,
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


def start_runtime_monitor(*, launch_command: str, base_url: str) -> bool:
    """Keep one verified managed runtime warm for this Jarvis process.

    The server still survives app exit. This lightweight daemon only closes
    the gap while Jarvis is running: an idle native crash is otherwise noticed
    only when the next user starts a call and pays the whole model cold boot.
    Returns ``True`` when a new monitor was armed and ``False`` when the same
    generation is already covered or cannot be owned safely.
    """
    command = (launch_command or "").strip()
    host, _port = _host_port(base_url)
    if managed_install_root(command) is None or not _is_loopback(host):
        return False
    try:
        from jarvis.realtime.local_server import install

        if not bool(install.server_status().get("ready")):
            return False
    except Exception:  # noqa: BLE001 - no proof means no autonomous monitor
        log.warning("supervisor: runtime monitor could not verify the install", exc_info=True)
        return False
    _pid, alive = _owned_process()
    if not alive:
        return False
    key = (command, _pool_url(base_url))

    global _monitor_thread, _monitor_stop, _monitor_key
    with _MONITOR_LOCK:
        if (
            _monitor_thread is not None
            and _monitor_thread.is_alive()
            and _monitor_key == key
        ):
            return False
        if _monitor_stop is not None:
            _monitor_stop.set()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_runtime_monitor,
            args=(command, base_url, stop_event, key),
            name="local-realtime-monitor",
            daemon=True,
        )
        _monitor_stop = stop_event
        _monitor_thread = thread
        _monitor_key = key
        thread.start()
    log.info("local-realtime supervisor: continuous runtime monitor armed")
    return True


def _revive_from_monitor(
    *,
    launch_command: str,
    base_url: str,
    reason: str,
    cancel_event: threading.Event,
) -> str:
    """Run one generation-bound revive and return its explicit outcome."""
    try:
        from jarvis.realtime.local_server import install

        if not bool(install.server_status().get("ready")):
            return "refused:install-unproven"
    except Exception:  # noqa: BLE001 - fail closed before an autonomous spawn
        log.warning("supervisor: monitor revive could not verify the install", exc_info=True)
        return "refused:install-unproven"
    outcome = ensure_running(
        launch_command=launch_command,
        base_url=base_url,
        reason=reason,
    )
    if outcome != "spawned":
        return outcome
    ready = wait_until_ready(
        base_url,
        timeout=RUNTIME_READY_TIMEOUT_S,
        launch_command=launch_command,
        cleanup_on_timeout=True,
        cancel_event=cancel_event,
    )
    if not ready:
        return "cancelled" if cancel_event.is_set() else "readiness-timeout"
    try:
        install.repair_smoke_marker_from_live_runtime(base_url)
    except Exception:  # noqa: BLE001 - runtime recovery remains usable
        log.warning("supervisor: smoke-proof repair after revive failed", exc_info=True)
    warm_brain(launch_command=launch_command)
    return "ready"


def _runtime_monitor(
    launch_command: str,
    base_url: str,
    stop_event: threading.Event,
    key: tuple[str, str],
) -> None:
    """Detect an idle process exit promptly and a wedged pool conservatively."""
    next_pool_probe = time.monotonic() + RUNTIME_MONITOR_POOL_INTERVAL_S
    unready_since: float | None = None
    last_outcome = ""
    try:
        while not stop_event.wait(RUNTIME_MONITOR_POLL_S):
            try:
                if managed_install_root(launch_command) is None:
                    return
                _pid, alive = _owned_process()
                if not alive:
                    # A healthy listener without our ownership is foreign. Do
                    # not fight it or keep trying to adopt it.
                    if probe_runtime(base_url, timeout=0.25) is not None:
                        log.warning(
                            "local-realtime monitor stopped: ready listener is no "
                            "longer owned by this install"
                        )
                        return
                    outcome = _revive_from_monitor(
                        launch_command=launch_command,
                        base_url=base_url,
                        reason="watchdog-exit",
                        cancel_event=stop_event,
                    )
                    if outcome == "ready":
                        log.info("local-realtime monitor: crashed runtime recovered")
                        unready_since = None
                        next_pool_probe = (
                            time.monotonic() + RUNTIME_MONITOR_POOL_INTERVAL_S
                        )
                    elif outcome != last_outcome:
                        log.warning(
                            "local-realtime monitor: crash recovery deferred (%s)",
                            outcome,
                        )
                    last_outcome = outcome
                    continue

                now = time.monotonic()
                if now < next_pool_probe:
                    continue
                next_pool_probe = now + RUNTIME_MONITOR_POOL_INTERVAL_S
                if probe_runtime(base_url, timeout=0.75) is not None:
                    unready_since = None
                    last_outcome = ""
                    continue
                if unready_since is None:
                    unready_since = now
                    log.warning(
                        "local-realtime monitor: owned process stopped reporting "
                        "a ready model pool; waiting for a second failed probe"
                    )
                    continue
                if now - unready_since < RUNTIME_MONITOR_UNREADY_GRACE_S:
                    continue
                outcome = _revive_from_monitor(
                    launch_command=launch_command,
                    base_url=base_url,
                    reason="watchdog-unready",
                    cancel_event=stop_event,
                )
                if outcome == "ready":
                    log.info("local-realtime monitor: wedged runtime recovered")
                    unready_since = None
                    next_pool_probe = (
                        time.monotonic() + RUNTIME_MONITOR_POOL_INTERVAL_S
                    )
                elif outcome != last_outcome:
                    log.warning(
                        "local-realtime monitor: unready recovery deferred (%s)",
                        outcome,
                    )
                last_outcome = outcome
            except Exception:  # noqa: BLE001 - a monitor must survive one bad probe
                log.warning("local-realtime monitor iteration failed", exc_info=True)
    finally:
        global _monitor_thread, _monitor_stop, _monitor_key
        with _MONITOR_LOCK:
            if _monitor_stop is stop_event:
                _monitor_thread = None
                _monitor_stop = None
                _monitor_key = None


def _request_runtime_monitor_stop() -> bool:
    """Disarm autonomous recovery before a deliberate lifecycle stop."""
    with _MONITOR_LOCK:
        if _monitor_stop is None:
            return False
        _monitor_stop.set()
        return True


# ── Stop ─────────────────────────────────────────────────────────────────


def stop(*, owned_only: bool = True, install_root: Path | None = None) -> tuple[bool, str]:
    """Serialize a deliberate stop against every start and cleanup."""
    with lifecycle_guard() as guarded:
        if not guarded:
            return False, "server lifecycle operation already in progress"
        _request_runtime_monitor_stop()
        return _stop_owned_unlocked(owned_only=owned_only, install_root=install_root)


def _stop_owned_unlocked(
    *,
    owned_only: bool = True,
    install_root: Path | None = None,
) -> tuple[bool, str]:
    """Stop the server this install owns. ``(changed, message)``.

    Kill order: the pidfile's verified pid tree (create-time match) first,
    followed by an install-root sweep even when that kill succeeded. The
    second step catches a model process orphaned before ownership was written.
    A port match is never a kill criterion, so a foreign listener survives.
    """
    pid, alive = _owned_process()
    owner_stopped = False
    if alive and pid is not None:
        owner_stopped = _kill_pid_tree(pid)
    elif pid is not None:
        clear_pidfile()

    # Always sweep after the parent-tree kill. A failed bind can leave a
    # second managed process alive even though the recorded parent stopped;
    # returning early here was the orphan leak seen in the live incident.
    swept, survivors = (
        _kill_by_install_root(install_root) if install_root is not None else (0, 0)
    )
    if survivors:
        if owner_stopped:
            clear_pidfile()
        return False, f"could not stop {survivors} managed process(es)"
    if owner_stopped or swept:
        clear_pidfile()
        details: list[str] = []
        if owner_stopped and pid is not None:
            details.append(f"pid {pid}")
        if swept:
            details.append(f"{swept} managed process(es)")
        return True, f"stopped {' and '.join(details)}"
    if alive and pid is not None:
        return False, f"could not stop pid {pid}"
    if not owned_only:
        log.info("supervisor: no owned server process found to stop")
    return False, "no owned server process found"


def _cleanup_timed_out_generation(
    *,
    base_url: str,
    install_root: Path,
    expected_generation: tuple[int, float, str],
) -> tuple[str, str]:
    """Conditionally stop only the exact child a readiness waiter observed."""
    with lifecycle_guard() as guarded:
        if not guarded:
            return "deferred", "another lifecycle operation is in progress"
        # Both checks occur while the lifecycle lease is held. No newer spawn
        # can slip between the readiness verdict, generation compare and stop.
        if probe_runtime(base_url, timeout=0.75) is not None:
            return "ready", "model pool became ready at the timeout boundary"
        if _owned_generation() != expected_generation:
            return "skipped", "owned server generation changed"
        changed, message = _stop_owned_unlocked(
            owned_only=True,
            install_root=install_root,
        )
        return ("completed" if changed else "failed"), message


def _kill_pid_tree(pid: int) -> bool:
    try:
        if os.name == "nt":
            from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # lazy

            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            if result.returncode == 0:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and _pid_exists(pid):
                    time.sleep(0.1)
            if not _pid_exists(pid):
                return True
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            log.warning(
                "supervisor: taskkill did not terminate pid %s (exit %s): %s",
                pid,
                result.returncode,
                stderr.strip()[:300],
            )
            return False
        import signal

        # The spawn used start_new_session, so the pid IS the group leader:
        # SIGTERM the group, grace, then SIGKILL what remains.
        try:
            os.killpg(pid, signal.SIGTERM)  # type: ignore[attr-defined]
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
            os.killpg(pid, signal.SIGKILL)  # type: ignore[attr-defined]
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)  # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001 — best-effort teardown, reported honestly
        log.warning("supervisor: kill of pid %s incomplete", pid, exc_info=True)
        return False


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` only proves that Windows can still open the
        # process object. A terminated object remains open while another
        # handle references it, which made a successful taskkill look alive
        # and aborted repair installs. A signalled handle is the real test.
        try:
            import ctypes
            from ctypes import wintypes

            synchronize = 0x00100000
            wait_timeout = 0x00000102
            wait_failed = 0xFFFFFFFF
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(synchronize, False, pid)
            if not handle:
                # Access denied proves an object exists but not that it is safe
                # to call dead. Every other error is the normal gone-PID case.
                return ctypes.get_last_error() == 5
            try:
                result = int(kernel32.WaitForSingleObject(handle, 0))
            finally:
                kernel32.CloseHandle(handle)
            return result in {wait_timeout, wait_failed}
        except Exception:  # noqa: BLE001 - unknown must fail safe as possibly alive
            log.debug("supervisor: Windows pid-state probe failed", exc_info=True)
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _path_is_within_root(value: str, root: Path) -> bool:
    text = (value or "").strip().strip("\"'")
    if "=" in text:
        text = text.split("=", 1)[1].strip().strip("\"'")
    if not text or not Path(text).is_absolute():
        return False
    try:
        candidate = Path(text).resolve()
    except OSError:
        return False
    return candidate == root or candidate.is_relative_to(root)


def _lexical_path_is_within_root(value: str, root: Path) -> bool:
    """Containment without dereferencing a POSIX venv interpreter symlink."""
    text = (value or "").strip().strip("\"'")
    if not text or not Path(text).is_absolute():
        return False
    candidate = os.path.normcase(os.path.abspath(text))
    root_text = os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath((candidate, root_text)) == root_text
    except ValueError:
        return False


def _process_identity_is_managed(exe: str, cmdline: tuple[str, ...], root: Path) -> bool:
    """Match only executable identity, never arbitrary opened-file arguments."""
    return _lexical_path_is_within_root(exe, root) or bool(
        cmdline and _lexical_path_is_within_root(cmdline[0], root)
    )


def _kill_by_install_root(root: Path) -> tuple[int, int]:
    """Return ``(stopped, survivors)`` for processes in OUR managed tree."""
    try:
        resolved_root = root.resolve()
    except OSError:
        return 0, 0
    if resolved_root.name.lower() != "local_realtime" or not resolved_root.exists():
        log.warning("supervisor: refused unsafe install-root sweep of %s", resolved_root)
        return 0, 0
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        log.debug("supervisor: psutil unavailable — skipping needle scan")
        return 0, 0

    protected = {os.getpid()}
    try:
        protected.update(parent.pid for parent in psutil.Process().parents())
    except psutil.Error:
        log.debug("supervisor: process-parent protection lookup failed", exc_info=True)

    matches = []
    for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
        try:
            if int(proc.info.get("pid") or 0) in protected:
                continue
            exe = str(proc.info.get("exe") or "")
            cmdline = tuple(str(item) for item in (proc.info.get("cmdline") or ()))
            if _process_identity_is_managed(exe, cmdline, resolved_root):
                matches.append(proc)
        except (psutil.Error, OSError, TypeError, ValueError):
            log.debug("supervisor: managed process inspection failed", exc_info=True)

    signalled = []
    vanished = 0
    failed = 0
    for proc in matches:
        try:
            proc.kill()
            signalled.append(proc)
        except psutil.NoSuchProcess:
            vanished += 1
        except (psutil.Error, OSError):
            failed += 1
            log.warning(
                "supervisor: could not kill managed pid %s",
                getattr(proc, "pid", "?"),
                exc_info=True,
            )
    if not signalled:
        return vanished, failed
    gone, alive = psutil.wait_procs(signalled, timeout=10)
    for proc in alive:
        log.warning("supervisor: managed pid %s survived forced stop", proc.pid)
    return vanished + len(gone), failed + len(alive)


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
    """Reset module-level rate-limit and monitor state between tests."""
    global _last_spawn_at, _monitor_thread, _monitor_stop, _monitor_key
    _last_spawn_at = float("-inf")
    with _MONITOR_LOCK:
        if _monitor_stop is not None:
            _monitor_stop.set()
        _monitor_thread = None
        _monitor_stop = None
        _monitor_key = None
