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

#: Ceiling for the failure-widened spawn spacing below. A generation that never
#: reaches a ready pool still loads gigabytes of weights onto the accelerator
#: before it dies, so an endlessly failing install must not keep paying that
#: cost every minute forever.
SPAWN_MAX_INTERVAL_S = 900.0

# Installed checkpoints are already local after the mandatory smoke boot, but
# imports, CUDA graph capture, STT warm-up and the local brain can still take
# more than two minutes on a contended 16 GB host.  This five-minute ceiling is
# for background supervision only; an interactive call keeps its shorter
# provider-owned connection budget.  It is deliberately bounded so a genuine
# native hang is still reclaimed.
RUNTIME_READY_TIMEOUT_S = 300.0
#: Floor for the measured readiness budget: never judge a boot by a median so
#: small that one contended run looks like a hang.
RUNTIME_READY_MIN_TIMEOUT_S = 120.0
RUNTIME_READY_POLL_S = 0.5
OWNED_STARTUP_TIMEOUT_S = RUNTIME_READY_TIMEOUT_S

# The managed process is meant to be warm before a user calls. Poll its
# ownership cheaply once a second so an idle native crash starts recovering
# immediately; probe the full model pool often enough that an abandoned
# capacity-one session cannot make the next caller wait minutes.  Replacement
# still requires 30 seconds of continuously unavailable capacity and a second
# generation-bound probe under the lifecycle lease.
RUNTIME_MONITOR_POLL_S = 1.0
RUNTIME_MONITOR_POOL_INTERVAL_S = 5.0
RUNTIME_MONITOR_UNREADY_GRACE_S = 30.0

_MAX_POOL_RESPONSE_BYTES = 64 * 1024

#: Rotate the shared server log once it passes this size. Large enough that a
#: whole boot transcript plus a long session's noise stays in one file for the
#: crash tail, small enough that an always-on host cannot silently fill a disk.
MAX_SERVER_LOG_BYTES = 8 * 1024 * 1024

#: How long the Ollama brain model stays resident after a warm ping. Slides on
#: every warm call (the desktop warm worker re-arms after each voice session),
#: so only a genuinely idle multi-hour gap pays a reload.
BRAIN_KEEP_ALIVE = "2h"

#: How often the runtime monitor re-arms that residency. The keep-alive above
#: is a DEADLINE, not a subscription: an overnight gap with no voice session
#: expires it, and the first sentence of the morning then pays a cold model
#: load on top of an otherwise warm server. Re-pinging well inside the window
#: costs one empty HTTP request from a thread that is already polling.
BRAIN_REWARM_INTERVAL_S = 45 * 60.0

# Ollama's OpenAI-compatible endpoints cannot accept a per-request context
# size.  Current long-context models can therefore reserve their full native
# window (qwen3.5:4b used 262,144 tokens and spilled 5+ GiB into shared GPU
# memory on a 16 GiB card).  The managed voice stack needs only a compact turn
# history, so it runs through a deterministic Ollama profile with an explicit
# bounded context.  This leaves room for local TTS and prevents system-wide
# WDDM paging while preserving the user's chosen base model.
VOICE_BRAIN_CONTEXT_TOKENS = 8192
_VOICE_MODEL_SUFFIX = "-voice-8k"
_VOICE_MODEL_PREPARE_LOCK = threading.Lock()
_prepared_voice_models: set[tuple[str, str, str]] = set()

_DEFAULT_PORT = 8765

_WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_WINDOWS_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_WINDOWS_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)

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


def _boot_stats() -> Path:
    return _data_dir() / "local_realtime_server.boot.json"


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
        # Unparseable port in a stored address — fall back to the pinned default.
        port = _DEFAULT_PORT
    return parsed.hostname or "127.0.0.1", port


def _is_loopback(host: str) -> bool:
    return host.lower() in {"localhost", "127.0.0.1", "::1"}


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        # Connection refused/timed out — the port simply is not open yet.
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
        # Unparseable port in a stored address — fall back to the pinned default.
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
        http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
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
        # Reported through the documented "None means not ready" return contract above.
        return None
    finally:
        connection.close()
    if len(raw) > _MAX_POOL_RESPONSE_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        # Reported through the documented "None means not ready" return contract above.
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
    idle = states.count("idle")
    active = states.count("active")
    draining = states.count("draining")
    stuck = states.count("stuck")
    if in_use != active + draining + stuck:
        return None
    return {
        "size": size,
        "in_use": in_use,
        "available": idle,
        "active": active,
        "draining": draining,
        "stuck": stuck,
    }


def _pool_has_no_usable_capacity(pool: dict[str, int]) -> bool:
    """Whether every slot is abandoned and no client is still connected."""
    return (
        pool.get("available") == 0
        and pool.get("active") == 0
        and pool.get("draining", 0) + pool.get("stuck", 0) == pool.get("size")
    )


def _recorded_boot_stats() -> Any:
    """The persisted boot statistics, or ``None`` when they cannot be read."""
    try:
        from jarvis.realtime.local_server import boot_progress  # lazy (AP-26)

        return boot_progress.load_stats(_boot_stats())
    except Exception:  # noqa: BLE001 - statistics never gate a lifecycle decision
        log.debug("supervisor: boot statistics unavailable", exc_info=True)
        return None


def ready_timeout_s() -> float:
    """How long a fresh generation may take to report a ready model pool.

    The fixed five-minute ceiling was sized for the slowest imaginable host,
    which means every machine pays five minutes to notice a boot that hung —
    including one whose own measured boots take a minute. The recorded median
    is the honest local expectation, and three times it absorbs a contended
    run without turning a genuine native hang into a five-minute wait. Without
    history the full ceiling stands: a first boot on unknown hardware is
    exactly when patience is warranted.
    """
    stats = _recorded_boot_stats()
    if stats is None:
        return RUNTIME_READY_TIMEOUT_S
    try:
        from jarvis.realtime.local_server import boot_progress  # lazy (AP-26)

        expected = boot_progress.expected_boot_s(stats)
    except Exception:  # noqa: BLE001 - a bad statistic keeps the safe ceiling
        log.debug("supervisor: expected boot time unavailable", exc_info=True)
        return RUNTIME_READY_TIMEOUT_S
    if expected is None or expected <= 0:
        return RUNTIME_READY_TIMEOUT_S
    return max(
        RUNTIME_READY_MIN_TIMEOUT_S,
        min(RUNTIME_READY_TIMEOUT_S, 3.0 * expected),
    )


def _spawn_min_interval_s() -> float:
    """Minimum spawn spacing, widened by consecutive never-ready boots.

    ``failed_streak`` counts only generations that never reached a ready pool
    inside their whole budget, and :func:`boot_progress.record_ready` clears it
    the moment one succeeds. So a server that crashes after a healthy hour
    keeps the plain one-minute window and recovers promptly, while an install
    that can no longer boot at all stops re-loading gigabytes of weights onto
    the accelerator every minute forever.
    """
    stats = _recorded_boot_stats()
    if stats is None:
        return SPAWN_MIN_INTERVAL_S
    streak = int(stats["failed_streak"])
    if streak <= 0:
        return SPAWN_MIN_INTERVAL_S
    return min(SPAWN_MAX_INTERVAL_S, SPAWN_MIN_INTERVAL_S * 2.0 ** min(streak, 4))


def wait_until_ready(
    base_url: str,
    *,
    timeout: float | None = None,
    poll_interval: float = RUNTIME_READY_POLL_S,
    launch_command: str = "",
    cleanup_on_timeout: bool = False,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Wait until the managed model pool, not merely TCP, answers.

    A timed-out managed child is torn down when requested so it cannot remain
    as an owned-but-never-ready zombie. Cancellation is different from a
    timeout: callers stopping the server set ``cancel_event`` and no second
    lifecycle operation is started from this waiter. ``timeout`` defaults to
    the measured budget from :func:`ready_timeout_s`.
    """
    if timeout is None:
        timeout = ready_timeout_s()
    cleanup_root = managed_install_root(launch_command) if cleanup_on_timeout else None
    expected_generation = _owned_generation() if cleanup_root is not None else None
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return False
        if probe_runtime(base_url, timeout=min(0.75, max(0.05, timeout))) is not None:
            _record_boot_ready_once()
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
        # No pidfile written yet, or unreadable — treat as "not owned".
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        # Corrupt pidfile — same "not owned" outcome as a missing one.
        return None
    return data if isinstance(data, dict) else None


def _json_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except ValueError:
        # Non-numeric string in a stored/received field — treated as absent.
        return None


def _json_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        # Non-numeric string in a stored/received field — treated as absent.
        return None


def _process_create_time(pid: int) -> float | None:
    """The process's kernel start stamp, or ``None`` when unverifiable.

    A POSIX child that has exited but was never reaped keeps its process-table
    entry: ``/proc/<pid>/stat`` still answers, so ``create_time()`` and
    ``os.kill(pid, 0)`` both keep reporting the CRASHED server as a healthy
    one. :func:`_spawn` deliberately drops its ``Popen`` handle (the server
    outlives this app by design), so nothing reaps that entry until some other
    subprocess in this interpreter happens to trigger ``subprocess._cleanup``.
    Until then the monitor would never see the alive→gone transition and would
    never recover the crash. A zombie is a dead process; say so here, once, on
    the one path every ownership question already goes through.
    """
    try:
        import psutil  # type: ignore[import-untyped]  # lazy, optional
    except ImportError:
        # psutil not installed — ownership falls back to the caller's other checks.
        return None
    try:
        process = psutil.Process(pid)
        created = float(process.create_time())
    except Exception:  # noqa: BLE001 — gone/denied both mean "not verifiable"
        return None
    try:
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
    except Exception:  # noqa: BLE001 — an unreadable status must not revoke ownership
        log.debug("supervisor: process status probe failed for pid %s", pid, exc_info=True)
    return created


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
        # Already locked by another process/thread — reported through the bool.
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


# ── Boot progress ────────────────────────────────────────────────────────


def _record_boot_ready_once() -> None:
    """Persist the just-observed boot completion, once per spawn generation.

    Callers race to be the first observer (status polls, the readiness
    waiter, the monitor revive); the per-token guard in the stats file makes
    every observation after the first a no-op. The duration sanity bound
    keeps a long-running server's status polls from recording hours as a
    "boot".
    """
    try:
        record = _read_pidfile() or {}
        token = record.get("spawn_token")
        spawned_at = _json_float(record.get("spawned_at"))
        if not isinstance(token, str) or not token or spawned_at is None:
            return
        duration = time.time() - spawned_at
        if not 0 < duration <= RUNTIME_READY_TIMEOUT_S + 60.0:
            return
        from jarvis.realtime.local_server import boot_progress  # lazy (AP-26)

        boot_progress.record_ready(_boot_stats(), token=token, duration_s=duration)
    except Exception:  # noqa: BLE001 - statistics must never break readiness
        log.debug("supervisor: boot-duration record failed", exc_info=True)


def _record_boot_timeout(token: str) -> None:
    """Count one readiness timeout toward the consecutive-failure streak."""
    try:
        from jarvis.realtime.local_server import boot_progress  # lazy (AP-26)

        boot_progress.record_timeout(_boot_stats(), token=token)
    except Exception:  # noqa: BLE001 - statistics must never break cleanup
        log.debug("supervisor: boot-timeout record failed", exc_info=True)


def _boot_status(*, booting: bool) -> dict[str, object]:
    """The boot sub-verdict of :func:`status`, safe on every failure path."""
    from jarvis.realtime.local_server import boot_progress  # lazy (AP-26)

    stats = boot_progress.load_stats(_boot_stats())
    payload: dict[str, object] = {
        "failed_streak": stats["failed_streak"],
        "starting": False,
    }
    if not booting:
        return payload
    record = _read_pidfile() or {}
    spawned_at = _json_float(record.get("spawned_at"))
    if spawned_at is None:
        return payload
    elapsed = max(0.0, time.time() - spawned_at)
    stage = boot_progress.parse_boot_stage(
        boot_progress.read_log_tail(_server_log()), spawned_at=spawned_at
    )
    expected = boot_progress.expected_boot_s(stats)
    remaining: float | None = None
    # Past twice the historical boot time the countdown would be a lie;
    # showing only the stage is the honest degradation.
    if expected is not None and elapsed <= 2.0 * expected:
        remaining = max(5.0, expected - elapsed)
    payload.update(
        {
            "starting": True,
            "stage": stage[0] if stage else None,
            "stage_label": stage[1] if stage else None,
            "elapsed_s": round(elapsed, 1),
            "expected_total_s": expected,
            "remaining_s": round(remaining) if remaining is not None else None,
        }
    )
    return payload


def boot_snapshot() -> dict[str, object]:
    """Live boot progress of the owned child, for user-facing refusals.

    Lighter than :func:`status`: no port or pool probe — the caller already
    holds a "not ready" verdict and only needs the stage and the honest ETA.
    """
    try:
        _pid, alive = _owned_process()
        return _boot_status(booting=alive)
    except Exception:  # noqa: BLE001 - a progress hint never breaks a verdict
        log.debug("supervisor: boot snapshot failed", exc_info=True)
        return {"failed_streak": 0, "starting": False}


def _log_crash_tail() -> None:
    """One bounded forensic tail when the owned server exits unexpectedly.

    A silently dying native child (live 2026-08-10 18:41) previously left
    nothing but readiness-poll noise; whatever the server managed to say
    last belongs in the desktop log next to the recovery decision.
    """
    try:
        from jarvis.realtime.local_server import boot_progress  # lazy (AP-26)

        tail = boot_progress.crash_tail(boot_progress.read_log_tail(_server_log()))
        if tail:
            log.warning(
                "local-realtime monitor: owned server process exited "
                "unexpectedly; last server-log lines:\n%s",
                "\n".join(tail),
            )
        else:
            log.warning(
                "local-realtime monitor: owned server process exited "
                "unexpectedly and left no substantive log tail"
            )
    except Exception:  # noqa: BLE001 - forensics must never break recovery
        log.debug("supervisor: crash-tail capture failed", exc_info=True)


# ── Status ───────────────────────────────────────────────────────────────


def status(base_url: str = "") -> dict[str, object]:
    """Live ownership plus distinct TCP and model-readiness verdicts."""
    host, port = _host_port(base_url)
    pid, alive = _owned_process()
    reachable = _is_loopback(host) and _port_open(port, timeout=0.25)
    pool = probe_runtime(base_url, timeout=0.5) if reachable else None
    if pool is not None and alive:
        # The UI polls this while the card is open, which makes it the most
        # reliable first observer of a completed boot (token-guarded).
        _record_boot_ready_once()
    return {
        "reachable": reachable,
        "ready": pool is not None,
        "available": bool(pool and pool["available"] > 0),
        "pool": pool,
        "port": port,
        "pid": pid,
        "owned": alive,
        "stale": pid is not None and not alive,
        "boot": _boot_status(booting=alive and pool is None),
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


def _recorded_spawn_is_recent(interval_s: float = SPAWN_MIN_INTERVAL_S) -> bool:
    age = _recorded_spawn_age()
    return age is not None and age < interval_s


def _command_references_root(command: str, root: Path) -> bool:
    """Whether a launch token resolves inside the managed install tree."""
    try:
        import shlex

        tokens = shlex.split(command, posix=os.name != "nt")
        resolved_root = root.resolve()
    except (OSError, ValueError):
        # Unparseable command or unresolvable root — reported via the returned bool.
        return False
    for token in tokens:
        candidate = token.split("=", 1)[-1].strip("\"'")
        if not candidate or not Path(candidate).is_absolute():
            continue
        try:
            if Path(candidate).resolve().is_relative_to(resolved_root):
                return True
        except OSError:
            # This one token cannot be resolved — check the remaining ones.
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
_LLM_BACKEND_FLAG = re.compile(
    r"(?<!\S)--llm_backend(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_REASONING_EFFORT_FLAG = re.compile(
    r"(?<!\S)--responses_api_reasoning_effort(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_MIN_SILENCE_FLAG = re.compile(
    r"(?<!\S)--min(?:_|-)silence(?:_|-)ms(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_SMART_TURN_INCOMPLETE_DELAY_FLAG = re.compile(
    r"(?<!\S)--smart(?:_|-)turn(?:_|-)incomplete(?:_|-)delay(?:_|-)ms"
    r"(?:\s+|=)(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_UNANSWERED_REOPEN_FLAG = re.compile(
    r"(?<!\S)--unanswered(?:_|-)reopen(?:_|-)ms(?:\s+|=)"
    r"(?:\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_LIVE_TRANSCRIPTION_FLAG = re.compile(
    r"(?<!\S)--(?:no(?:_|-))?enable(?:_|-)live(?:_|-)transcription"
    r"(?:=(?:true|false|0|1)|\s+(?:true|false|0|1))?",
    re.IGNORECASE,
)


def _force_loopback_bind(command: str) -> str:
    """Migrate legacy managed commands to a loopback-only server bind."""
    if _WS_HOST_FLAG.search(command):
        return _WS_HOST_FLAG.sub("--ws_host 127.0.0.1", command)
    return f"{command.rstrip()} --ws_host 127.0.0.1"


def _replace_cli_flag(command: str, pattern: re.Pattern[str], rendered: str) -> str:
    """Replace every spelling of one value flag with one canonical value."""
    without_old_values = pattern.sub("", command).rstrip()
    return f"{without_old_values} {rendered}"


def _cli_flag_value(command: str, flag: str) -> str | None:
    """Return the last effective CLI value, accepting ``--flag x`` and ``=x``."""
    try:
        import shlex

        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        # Unbalanced quotes in a stored command — treat as "flag not found".
        return None
    value: str | None = None
    normalized_flag = flag.lower()
    for index, token in enumerate(tokens):
        lowered = token.lower()
        if lowered == normalized_flag:
            if index + 1 >= len(tokens):
                return None
            value = tokens[index + 1].strip("\"'")
        elif lowered.startswith(f"{normalized_flag}="):
            value = token.split("=", 1)[1].strip("\"'")
    return value


def _is_loopback_ollama_brain(command: str) -> bool:
    """Whether a server command targets the local Ollama compatibility API."""
    _model, base_url, api_key = _brain_endpoint_details(command)
    if not base_url or api_key.lower() != "ollama":
        return False
    return _is_loopback(urlsplit(base_url).hostname or "")


def _needs_ollama_backend_migration(command: str) -> bool:
    """Whether an Ollama command can still burn hidden reasoning tokens."""
    return _is_loopback_ollama_brain(command) and (
        _cli_flag_value(command, "--llm_backend") != "chat-completions"
        or _cli_flag_value(command, "--responses_api_reasoning_effort") != "none"
    )


def _force_low_latency_ollama_backend(command: str) -> str:
    """Use Ollama's supported no-thinking endpoint for managed voice turns."""
    if not _needs_ollama_backend_migration(command):
        return command
    command = _replace_cli_flag(
        command,
        _LLM_BACKEND_FLAG,
        "--llm_backend chat-completions",
    )
    return _replace_cli_flag(
        command,
        _REASONING_EFFORT_FLAG,
        "--responses_api_reasoning_effort none",
    )


def _needs_stable_turn_detection_migration(command: str) -> bool:
    """Whether the managed server can publish premature/repeated finals."""
    return (
        _cli_flag_value(command, "--min_silence_ms") != "320"
        or _cli_flag_value(command, "--smart_turn_incomplete_delay_ms") != "2000"
        or _cli_flag_value(command, "--unanswered_reopen_ms") != "2000"
        or not re.search(
            r"(?<!\S)--no(?:_|-)enable(?:_|-)live(?:_|-)transcription(?!\S)",
            command,
            re.IGNORECASE,
        )
    )


def _force_stable_turn_detection(command: str) -> str:
    """Canonicalize the managed VAD/STT profile used by Jarvis."""
    if not _needs_stable_turn_detection_migration(command):
        return command
    command = _replace_cli_flag(
        command,
        _MIN_SILENCE_FLAG,
        "--min_silence_ms 320",
    )
    command = _replace_cli_flag(
        command,
        _SMART_TURN_INCOMPLETE_DELAY_FLAG,
        "--smart_turn_incomplete_delay_ms 2000",
    )
    command = _replace_cli_flag(
        command,
        _UNANSWERED_REOPEN_FLAG,
        "--unanswered_reopen_ms 2000",
    )
    without_live_transcription = _LIVE_TRANSCRIPTION_FLAG.sub("", command).rstrip()
    return f"{without_live_transcription} --no_enable_live_transcription"


def _uses_loopback_bind(command: str) -> bool:
    """Whether the effective managed-server bind is explicitly loopback-only."""
    try:
        import shlex

        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        # Unbalanced quotes in a stored command — treat as "not explicitly loopback".
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


def ensure_running(
    *,
    launch_command: str,
    base_url: str,
    reason: str,
    replace_unavailable_generation: tuple[int, float, str] | None = None,
    honor_failure_backoff: bool = True,
) -> str:
    """Start the server if — and only if — starting can help.

    Returns ``"already-running"`` (port served, or our recorded process is
    alive and presumably booting), ``"spawned"``, or ``"refused:<why>"``.
    The single spawn path for BOTH the boot-time prewarm and the connect-time
    revive, which is what makes their race harmless. The monitor may also pass
    the exact generation it observed with no usable capacity; replacement then
    happens only after the pool and ownership are verified again under the
    lifecycle lease.

    ``honor_failure_backoff`` widens the spawn spacing after repeated
    never-ready boots (see :func:`_spawn_min_interval_s`). Autonomous callers
    keep it; a human pressing Start has just supplied new information the
    statistics cannot have, and sets it to ``False``.
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
            # Normalize at the actual spawn boundary so existing installs get
            # both security and latency migrations without a reinstall.
            command = _force_loopback_bind(command)
            command = _force_low_latency_ollama_backend(command)
            command = _force_stable_turn_detection(command)
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
            owned_root = managed_install_root(owned_command) if owned_command is not None else None
            unsafe_bind = bool(owned_command and not _uses_loopback_bind(owned_command))
            slow_ollama_backend = bool(
                owned_command and _needs_ollama_backend_migration(owned_command)
            )
            unstable_turn_detection = bool(
                owned_command and _needs_stable_turn_detection_migration(owned_command)
            )
            latency_migration = slow_ollama_backend or unstable_turn_detection
            if (
                latency_migration
                and not unsafe_bind
                and (pool is None or pool.get("active", pool["in_use"]) > 0)
            ):
                # Latency migration is never a reason to terminate somebody's
                # live call or a generation whose state cannot be proven. The
                # next verified-idle connect can replace this generation.
                return "already-running"
            if owned_root is not None and (
                unsafe_bind or (latency_migration and replace_unavailable_generation is None)
            ):
                # Managed servers survive app exits, so an upgrade can inherit
                # a healthy legacy generation. Only a verified owned process
                # is eligible for these one-time migrations.
                changed, message = _stop_owned_unlocked(
                    owned_only=True,
                    install_root=owned_root,
                )
                if not changed:
                    log.warning(
                        "local-realtime supervisor: legacy runtime migration failed (%s)",
                        message,
                    )
                    return "refused:stuck-process"
                pool = None
                alive = False
                port_open = _port_open(port, timeout=0.25)
        if (
            pool is not None
            and replace_unavailable_generation is not None
            and _pool_has_no_usable_capacity(pool)
        ):
            if managed_root is None:
                return "refused:not-managed"
            if _owned_generation() != replace_unavailable_generation:
                return "refused:generation-changed"
            owned_command = _verified_owned_command()
            if (
                not alive
                or owned_command is None
                or managed_install_root(owned_command) != managed_root
            ):
                return "refused:unverified-owner"
            changed, message = _stop_owned_unlocked(
                owned_only=True,
                install_root=managed_root,
            )
            if not changed:
                log.warning(
                    "local-realtime supervisor: unavailable pool cleanup failed (%s)",
                    message,
                )
                return "refused:stuck-process"
            log.warning(
                "local-realtime supervisor: replaced an owned runtime whose "
                "entire pool remained unavailable without an active client"
            )
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
        interval = _spawn_min_interval_s() if honor_failure_backoff else SPAWN_MIN_INTERVAL_S
        if now - _last_spawn_at < interval or _recorded_spawn_is_recent(interval):
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
            try:
                command = prepare_voice_brain_command(command)
            except RuntimeError as exc:
                log.warning(
                    "local-realtime supervisor: bounded brain profile failed: %s",
                    exc,
                )
                return "refused:brain-context-profile"
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


def replace_idle_managed_runtime(
    *,
    current_command: str,
    launch_command: str,
    base_url: str,
    reason: str,
) -> str:
    """Replace one managed generation without ever interrupting a live call.

    This is the transactional model-switch primitive.  It intentionally
    bypasses the crash-loop rate limit because the caller made one explicit
    replacement decision, but it keeps every ownership, loopback, process-tree
    and pid-reuse guard used by :func:`ensure_running`.
    """
    current = (current_command or "").strip()
    command = (launch_command or "").strip()
    current_root = managed_install_root(current)
    managed_root = managed_install_root(command)
    host, port = _host_port(base_url)
    if not command:
        return "refused:no-launch-command"
    if managed_root is None or (current and current_root != managed_root):
        return "refused:not-managed"
    if not _is_loopback(host):
        return "refused:not-local"

    with lifecycle_guard() as guarded:
        if not guarded:
            return "refused:lifecycle-busy"
        try:
            from jarvis.realtime.local_server import install

            if bool(install.snapshot().get("running")):
                return "refused:install-running"
        except Exception:  # noqa: BLE001 - install state is advisory here
            log.debug("supervisor: install snapshot unavailable during switch", exc_info=True)

        pool = probe_runtime(base_url, timeout=0.75)
        if pool is not None and pool.get("in_use", 0) > 0:
            return "refused:call-active"
        _pid, alive = _owned_process()
        if pool is None and alive:
            # No model-pool verdict means the process may be loading or may
            # still own a client.  Never turn uncertainty into a destructive
            # model switch.
            return "refused:runtime-state-unknown"
        if pool is None and _port_open(port, timeout=0.25):
            return "refused:port-in-use"

        _request_runtime_monitor_stop()
        changed, stop_message = _stop_owned_unlocked(
            owned_only=True,
            install_root=managed_root,
        )
        if not changed and pool is not None:
            log.warning(
                "local-realtime supervisor: idle replacement could not stop "
                "the serving generation (%s)",
                stop_message,
            )
            return "refused:stuck-process"
        if _port_open(port, timeout=0.25):
            return "refused:port-still-in-use"

        command = _force_loopback_bind(command)
        command = _force_low_latency_ollama_backend(command)
        command = _force_stable_turn_detection(command)
        try:
            command = prepare_voice_brain_command(command)
        except RuntimeError as exc:
            log.warning(
                "local-realtime supervisor: switch brain profile failed: %s",
                exc,
            )
            return "refused:brain-context-profile"

        clear_pidfile()
        spawned_pid = _spawn(command, reason=reason)
        if spawned_pid is None:
            return "refused:spawn-failed"
        if not _write_pidfile(spawned_pid, port, command):
            _kill_pid_tree(spawned_pid)
            _kill_by_install_root(managed_root)
            return "refused:ownership-failed"
        global _last_spawn_at
        _last_spawn_at = time.monotonic()
        log.info(
            "local-realtime supervisor: replaced idle server with pid=%s (%s)",
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
    return _WINDOWS_NO_WINDOW | _WINDOWS_DETACHED_PROCESS | _WINDOWS_BREAKAWAY_FROM_JOB


def _rotate_server_log_if_large() -> None:
    """Keep the shared append-only server log from growing without bound.

    Every readiness poll writes one uvicorn access line, so a server that is
    doing its job perfectly still appends around a megabyte a day forever —
    and the crash forensics only ever read the tail. Rotating at the spawn
    boundary is the one moment no process holds the file open on Windows, and
    it additionally guarantees a fresh generation's stage parse cannot meet a
    predecessor's lines. One generation of history is kept.
    """
    path = _server_log()
    try:
        if path.stat().st_size <= MAX_SERVER_LOG_BYTES:
            return
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        # A held handle or a racing writer simply means no rotation this time.
        log.debug("supervisor: server-log rotation skipped", exc_info=True)
        return
    log.info("local-realtime supervisor: rotated the managed server log")


def _spawn(command: str, *, reason: str) -> int | None:
    """Detached, window-less, log-sinked spawn. ``None`` when it failed."""

    _rotate_server_log_if_large()
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
    if not alive and not reconcile_ready_ownership(
        launch_command=command,
        base_url=base_url,
    ):
        return False
    key = (command, _pool_url(base_url))

    global _monitor_thread, _monitor_stop, _monitor_key
    with _MONITOR_LOCK:
        if _monitor_thread is not None and _monitor_thread.is_alive() and _monitor_key == key:
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
    unavailable_generation: tuple[int, float, str] | None = None,
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
        replace_unavailable_generation=unavailable_generation,
    )
    if outcome != "spawned":
        return outcome
    ready = wait_until_ready(
        base_url,
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
    next_brain_warm = time.monotonic() + BRAIN_REWARM_INTERVAL_S
    unready_since: float | None = None
    unhealthy_kind = ""
    last_outcome = ""
    # The monitor only arms on a verified ready server, so the first
    # alive→gone transition it sees is a genuine unexpected exit.
    was_alive = True
    try:
        while not stop_event.wait(RUNTIME_MONITOR_POLL_S):
            try:
                if managed_install_root(launch_command) is None:
                    return
                _pid, alive = _owned_process()
                if alive:
                    was_alive = True
                elif was_alive:
                    was_alive = False
                    _log_crash_tail()
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
                        unhealthy_kind = ""
                        next_pool_probe = time.monotonic() + RUNTIME_MONITOR_POOL_INTERVAL_S
                        # The revive warmed the brain itself.
                        next_brain_warm = time.monotonic() + BRAIN_REWARM_INTERVAL_S
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
                pool = probe_runtime(base_url, timeout=0.75)
                kind = (
                    "unready"
                    if pool is None
                    else "unavailable"
                    if _pool_has_no_usable_capacity(pool)
                    else ""
                )
                if not kind:
                    unready_since = None
                    unhealthy_kind = ""
                    last_outcome = ""
                    if now >= next_brain_warm:
                        # Re-arm the brain's residency deadline on a proven
                        # healthy server, so an overnight gap cannot make the
                        # first sentence of the morning pay a cold model load.
                        next_brain_warm = now + BRAIN_REWARM_INTERVAL_S
                        warm_brain(launch_command=launch_command)
                    continue
                if unready_since is None or unhealthy_kind != kind:
                    unready_since = now
                    unhealthy_kind = kind
                    last_outcome = ""
                    log.warning(
                        "local-realtime monitor: owned process has %s; waiting "
                        "for the recovery grace period",
                        (
                            "no usable pool capacity and no active client"
                            if kind == "unavailable"
                            else "stopped reporting a ready model pool"
                        ),
                    )
                    continue
                if now - unready_since < RUNTIME_MONITOR_UNREADY_GRACE_S:
                    continue
                unavailable_generation = None
                if kind == "unavailable":
                    unavailable_generation = _owned_generation()
                    if unavailable_generation is None:
                        outcome = "refused:unverified-generation"
                        if outcome != last_outcome:
                            log.warning(
                                "local-realtime monitor: unavailable recovery deferred (%s)",
                                outcome,
                            )
                        last_outcome = outcome
                        continue
                outcome = _revive_from_monitor(
                    launch_command=launch_command,
                    base_url=base_url,
                    reason=f"watchdog-{kind}",
                    cancel_event=stop_event,
                    unavailable_generation=unavailable_generation,
                )
                if outcome == "ready":
                    log.info("local-realtime monitor: wedged runtime recovered")
                    unready_since = None
                    unhealthy_kind = ""
                    next_pool_probe = time.monotonic() + RUNTIME_MONITOR_POOL_INTERVAL_S
                    # The revive warmed the brain itself.
                    next_brain_warm = time.monotonic() + BRAIN_REWARM_INTERVAL_S
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
    swept, survivors = _kill_by_install_root(install_root) if install_root is not None else (0, 0)
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
        # Whatever the stop outcome, this generation provably never became
        # ready inside its whole budget — that is the crash-loop signal the
        # provider card surfaces after repeated failures.
        _record_boot_timeout(expected_generation[2])
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
            # Already gone — reported through the returned True, same as a
            # confirmed kill.
            return True
        except (PermissionError, OSError):
            # No process group to signal — fall back to killing just the pid.
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _process_create_time(pid) is None and not _pid_exists(pid):
                return True
            time.sleep(0.2)
        try:
            os.killpg(pid, signal.SIGKILL)  # type: ignore[attr-defined]
        except ProcessLookupError:
            # Already gone — reported through the returned True, same as a
            # confirmed kill.
            return True
        except (PermissionError, OSError):
            # No process group to signal — fall back to killing just the pid.
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
        # No such pid — reported through the returned False.
        return False
    except PermissionError:
        # Exists but not ours to signal — still alive, reported through True.
        return True
    except OSError:
        # Any other kill(0) failure means the pid does not resolve to a live process.
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
        # Unresolvable path — reported through the returned False.
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
        # Paths on different drives/roots — reported through the returned False.
        return False


def _process_identity_is_managed(exe: str, cmdline: tuple[str, ...], root: Path) -> bool:
    """Match only executable identity, never arbitrary opened-file arguments."""
    if _lexical_path_is_within_root(exe, root) or bool(
        cmdline and _lexical_path_is_within_root(cmdline[0], root)
    ):
        return True
    # uv-managed Windows environments can hand execution from the venv
    # launcher to uv's base Python.  The first program argument is then the
    # managed console-script executable; it is executable identity, not an
    # arbitrary opened data file.  Without this case the listener survived
    # while its short-lived launcher PID made ownership disappear.
    interpreter = Path(exe or (cmdline[0] if cmdline else "")).name.lower()
    return bool(
        os.name == "nt"
        and interpreter in {"python", "python.exe", "pythonw", "pythonw.exe"}
        and len(cmdline) > 1
        and Path(cmdline[1].strip("\"'")).name.lower() == "speech-to-speech.exe"
        and _lexical_path_is_within_root(cmdline[1], root)
    )


def reconcile_ready_ownership(*, launch_command: str, base_url: str) -> bool:
    """Adopt the unique managed process that actually owns the ready port.

    Windows console-script launchers may exit after delegating to a Python
    child.  Recording only the initial ``Popen.pid`` therefore loses
    ownership even though the server keeps listening.  This bounded adoption
    requires all three proofs: a ready pool, executable identity under the
    managed install, and the exact loopback listening port.
    """
    root = managed_install_root(launch_command)
    host, port = _host_port(base_url)
    if root is None or not _is_loopback(host):
        return False
    with lifecycle_guard() as guarded:
        if not guarded or probe_runtime(base_url, timeout=0.75) is None:
            return False
        try:
            import psutil  # type: ignore[import-untyped]
        except ImportError:
            # psutil not installed — adoption is skipped, reported via the returned False.
            return False

        candidates: list[Any] = []
        for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
            try:
                exe = str(proc.info.get("exe") or "")
                cmdline = tuple(str(item) for item in (proc.info.get("cmdline") or ()))
                if not _process_identity_is_managed(exe, cmdline, root):
                    continue
                if any(
                    connection.status == psutil.CONN_LISTEN
                    and connection.laddr
                    and int(connection.laddr.port) == port
                    for connection in proc.net_connections(kind="inet")
                ):
                    candidates.append(proc)
            except (psutil.Error, OSError, TypeError, ValueError):
                log.debug("supervisor: ready-owner inspection failed", exc_info=True)
        if len(candidates) != 1:
            log.warning(
                "local-realtime supervisor: expected one managed listener on %s, found %s",
                port,
                len(candidates),
            )
            return False
        proc = candidates[0]
        pid = int(proc.pid)
        record = _read_pidfile() or {}
        command = str(record.get("command") or launch_command)
        if not _write_pidfile(pid, port, command):
            return False
        log.info("local-realtime supervisor: adopted ready listener pid=%s", pid)
        return True


def _kill_by_install_root(root: Path) -> tuple[int, int]:
    """Return ``(stopped, survivors)`` for processes in OUR managed tree."""
    try:
        resolved_root = root.resolve()
    except OSError:
        # Unresolvable root — nothing to sweep, reported through the (0, 0) result.
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
            # Already gone — tracked via the vanished counter, not a failure to log.
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
    launch_command = _effective_owned_launch_command(launch_command)
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


def prepare_voice_brain_command(launch_command: str, *, timeout: float = 30.0) -> str:
    """Return a managed-server command using a bounded Ollama context profile.

    Ollama documents that its OpenAI-compatible API has no context-size field;
    a model created with ``num_ctx`` is the supported control surface.  Creating
    this alias changes no weights and performs no download.  Cloud/BYO commands
    are returned unchanged.
    """
    model, brain_url, api_key = _brain_endpoint_details(launch_command)
    if not model or not brain_url or api_key.lower() != "ollama":
        return launch_command
    root = brain_url[: -len("/v1")] if brain_url.endswith("/v1") else brain_url
    if not root.startswith(("http://", "https://")):
        return launch_command

    source_model, voice_model = _voice_context_models(model)
    cache_key = (root, source_model, voice_model)
    with _VOICE_MODEL_PREPARE_LOCK:
        if cache_key not in _prepared_voice_models:
            payload = json.dumps(
                {
                    "model": voice_model,
                    "from": source_model,
                    "parameters": {"num_ctx": VOICE_BRAIN_CONTEXT_TOKENS},
                    "stream": False,
                }
            ).encode("utf-8")
            import urllib.error
            import urllib.request

            url = f"{root}/api/create"
            request = urllib.request.Request(  # noqa: S310 - scheme checked above
                url, data=payload, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310
                    pass
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise RuntimeError(
                    f"could not create the {VOICE_BRAIN_CONTEXT_TOKENS}-token "
                    f"Ollama voice profile for {source_model}: {exc}"
                ) from exc
            _prepared_voice_models.add(cache_key)
            log.info(
                "local-realtime supervisor: prepared bounded Ollama model %s from %s (num_ctx=%s)",
                voice_model,
                source_model,
                VOICE_BRAIN_CONTEXT_TOKENS,
            )
    return _replace_brain_model(launch_command, voice_model)


def _voice_context_models(model: str) -> tuple[str, str]:
    """Return ``(base, bounded_alias)`` for an Ollama model tag."""
    cleaned = model.strip()
    if ":" in cleaned:
        name, tag = cleaned.rsplit(":", 1)
    else:
        name, tag = cleaned, "latest"
    if tag.endswith(_VOICE_MODEL_SUFFIX):
        return f"{name}:{tag[: -len(_VOICE_MODEL_SUFFIX)]}", cleaned
    return cleaned, f"{name}:{tag}{_VOICE_MODEL_SUFFIX}"


_MODEL_NAME_FLAG = re.compile(
    r"(?<!\S)(?P<flag>--model_name)(?P<sep>\s+|=)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)


def _replace_brain_model(command: str, model: str) -> str:
    """Replace only the managed server's model flag, preserving its quoting."""

    def _replacement(match: re.Match[str]) -> str:
        value = match.group("value")
        quote = value[0] if value[:1] in {'"', "'"} else ""
        rendered = f"{quote}{model}{quote}" if quote else model
        return f"{match.group('flag')}{match.group('sep')}{rendered}"

    return _MODEL_NAME_FLAG.sub(_replacement, command, count=1)


def _effective_owned_launch_command(configured_command: str) -> str:
    """Use the exact owned generation's command when warming its brain."""
    configured_root = managed_install_root(configured_command)
    if configured_root is None:
        return configured_command
    active_command = _verified_owned_command()
    if active_command is not None and managed_install_root(active_command) == configured_root:
        return active_command
    return configured_command


def _brain_endpoint(launch_command: str) -> tuple[str, str]:
    """(model, responses_api_base_url) parsed from a launch command."""
    model, base, _api_key = _brain_endpoint_details(launch_command)
    return model, base


def _brain_endpoint_details(launch_command: str) -> tuple[str, str, str]:
    """(model, Responses base URL, placeholder key) parsed from a command."""
    model = _cli_flag_value(launch_command or "", "--model_name") or ""
    base = _cli_flag_value(launch_command or "", "--responses_api_base_url") or ""
    api_key = _cli_flag_value(launch_command or "", "--responses_api_api_key") or ""
    return model, base.rstrip("/"), api_key


# ── Test support ─────────────────────────────────────────────────────────


def _reset_for_tests() -> None:
    """Reset module-level rate-limit and monitor state between tests."""
    global _last_spawn_at, _monitor_thread, _monitor_stop, _monitor_key
    _last_spawn_at = float("-inf")
    _prepared_voice_models.clear()
    with _MONITOR_LOCK:
        if _monitor_stop is not None:
            _monitor_stop.set()
        _monitor_thread = None
        _monitor_stop = None
        _monitor_key = None
