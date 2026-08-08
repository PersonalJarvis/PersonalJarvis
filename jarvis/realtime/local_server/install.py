"""The one-click install engine for the managed local realtime server.

Turns the hand-built dev-box procedure into a supervised pipeline:
preflight → venv → pinned dependencies → vendored patch → smoke boot →
persist the launch command. Runs in a daemon thread so the REST route
returns immediately; progress is poll-shaped (:func:`snapshot`) and every
failure leaves a resumable state plus a one-sentence reason — never a
half-installed "ready" (readiness itself is :func:`server_status`,
fail-closed).

The smoke boot doubles as the model-download step: the server fetches its
STT/TTS checkpoints into the shared HF cache on first start, so the first
real call never pays that multi-gigabyte surprise.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.realtime.local_server.brain_link import BrainResolution
from jarvis.realtime.local_server.patching import (
    PATCH_TARGET_VERSION,
    apply_create_response_patch,
    patch_state,
)
from jarvis.realtime.local_server.preflight import (
    PreflightReport,
    report_payload,
    run_preflight,
)

log = logging.getLogger(__name__)

_PINS_FILE = Path(__file__).parent / "pins" / f"core-{PATCH_TARGET_VERSION}.txt"

#: torch flavor per probed hardware. The CUDA build ships from the PyTorch
#: index; everything else takes the default wheels (macOS arm64 wheels carry
#: MPS support out of the box).
_TORCH_PINS_CUDA = ("torch==2.13.0+cu130", "torchaudio==2.11.0+cu130")
_TORCH_INDEX_CUDA = "https://download.pytorch.org/whl/cu130"
_TORCH_PINS_DEFAULT = ("torch==2.13.0", "torchaudio==2.11.0")

#: The port the managed server serves on (matches the provider default) and
#: the throwaway port the smoke boot binds so it never fights a live server.
SERVE_PORT = 8765
_SMOKE_PORT = 8876

_PIP_TIMEOUT_S = 3600
_SMOKE_TIMEOUT_S = 1800  # first boot downloads the TTS/STT checkpoints

_PHASES = ("idle", "preflight", "venv", "deps", "patch", "smoke", "configure", "done", "error")


def install_root() -> Path:
    """Permanent home of the managed server, under the Jarvis data dir.

    Absolute on purpose (review 2026-08-07): a CWD-relative ``data`` made
    the persisted launch command break under any other working directory
    (service starts, shortcuts, CLI) and made status probes read a
    different tree than the installer wrote.
    """
    env_dir = os.environ.get("JARVIS_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip()).resolve() / "local_realtime"
    from jarvis.core.config import DATA_DIR  # absolute PROJECT_ROOT/data

    return DATA_DIR / "local_realtime"


def _venv_dir() -> Path:
    return install_root() / "venv"


def _venv_bindir() -> Path:
    return _venv_dir() / ("Scripts" if os.name == "nt" else "bin")


def _venv_python() -> Path:
    return _venv_bindir() / ("python.exe" if os.name == "nt" else "python")


def _server_entrypoint() -> Path:
    return _venv_bindir() / ("speech-to-speech.exe" if os.name == "nt" else "speech-to-speech")


def _site_packages() -> Path:
    if os.name == "nt":
        return _venv_dir() / "Lib" / "site-packages"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return _venv_dir() / "lib" / version / "site-packages"


def _smoke_marker() -> Path:
    return install_root() / "smoke_ok.json"


@dataclass
class _State:
    phase: str = "idle"
    percent: int = 0
    detail: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=30))
    thread: threading.Thread | None = None


_STATE = _State()
_LOCK = threading.Lock()


def _set(phase: str, percent: int, detail: str = "") -> None:
    with _LOCK:
        _STATE.phase = phase
        _STATE.percent = percent
        if detail:
            _STATE.detail = detail
            _STATE.log_tail.append(detail)


def _fail(message: str) -> None:
    log.error("local-realtime install: %s", message)
    with _LOCK:
        _STATE.phase = "error"
        _STATE.error = message
        _STATE.finished_at = time.time()


def _reset_for_tests() -> None:
    """Reset the module-level progress singleton between tests.

    ``_STATE`` leaks across the pytest session otherwise: a test that drove
    an install to "error" left every later test reading that phase.
    """
    with _LOCK:
        _STATE.phase = "idle"
        _STATE.percent = 0
        _STATE.detail = ""
        _STATE.error = ""
        _STATE.started_at = 0.0
        _STATE.finished_at = 0.0
        _STATE.log_tail.clear()
        _STATE.thread = None


def snapshot() -> dict[str, object]:
    """Poll-shaped view of the running (or last) install."""
    with _LOCK:
        return {
            "phase": _STATE.phase,
            "percent": _STATE.percent,
            "detail": _STATE.detail,
            "error": _STATE.error,
            "running": _STATE.thread is not None and _STATE.thread.is_alive(),
            "log_tail": list(_STATE.log_tail),
        }


def _configured_launch_command() -> str:
    """The persisted local-realtime launch command, read cheaply and safely.

    A plain TOML read (no full config model) because this runs inside status
    probes: the only question is whether jarvis.toml still points a launch
    command at the managed tree. Any read problem answers "" — the status then
    simply reports without the staleness refinement (fail-open).
    """
    try:
        import tomllib

        from jarvis.core.config import resolve_config_path

        path = resolve_config_path()
        if not path.exists():
            return ""
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        providers = (data.get("brain") or {}).get("providers") or {}
        block = providers.get("local-realtime") or {}
        return str(block.get("launch_command") or "").strip()
    except Exception:  # noqa: BLE001 — a status probe must never raise
        log.debug("local-realtime: launch-command read failed", exc_info=True)
        return ""


def server_status() -> dict[str, object]:
    """Fail-closed readiness of the managed install (read-only, no network).

    Ready means ALL of: venv python present, server entry point present,
    vendored patch applied, and a recorded successful smoke boot. Each
    component is reported separately so the card can say exactly what is
    missing, in the server's words. ``stale`` flags the poisonous variant:
    jarvis.toml still carries a launch command pointing into this tree while
    the install files are gone (live 2026-08-08 — every call then chased a
    deleted entry point), which deserves a repair sentence, not "not
    installed".
    """
    components = {
        "venv": _venv_python().exists(),
        "entrypoint": _server_entrypoint().exists(),
        "patch": patch_state(_site_packages()) == "patched",
        "smoke_boot": _smoke_marker().exists(),
    }
    ready = all(components.values())
    command = _configured_launch_command()
    root_needle = str(install_root()).lower() if os.name == "nt" else str(install_root())
    command_probe = command.lower() if os.name == "nt" else command
    stale = (
        not components["entrypoint"]
        and bool(command)
        and root_needle in command_probe.replace("/", os.sep)
    )
    if ready:
        sentence = (
            f"Managed server installed (speech-to-speech {PATCH_TARGET_VERSION}, "
            "patched) and smoke-booted."
        )
    elif stale:
        sentence = (
            "Install files are missing but the launch command still points at "
            "them — reinstall to repair, or remove the local server."
        )
    elif not components["venv"] and not components["entrypoint"]:
        sentence = "Managed server not installed."
    else:
        missing = ", ".join(name for name, present in components.items() if not present)
        sentence = f"Managed server incomplete: {missing} missing — reinstall to repair."
    return {"ready": ready, "stale": stale, "components": components, "sentence": sentence}


def derive_launch_command(brain: BrainResolution, *, memory_source: str) -> str:
    """The exact command Jarvis uses to start/revive the managed server (AD-4).

    Derived, never hand-authored: flags follow the probed hardware and the
    resolved brain. The TTS device maps NVIDIA → cuda and Apple unified
    memory → mps; a machine with neither never passes preflight.

    Secrets never enter the command (they would land in jarvis.toml): the
    Ollama path uses the literal placeholder token Ollama expects, and the
    cloud path omits base/key entirely — the server's OpenAI client then
    reads ``OPENAI_API_KEY`` from the spawn environment.
    """
    tts_device = "cuda" if memory_source == "nvidia-smi" else "mps"
    parts = [
        f'"{_server_entrypoint()}"',
        "--mode realtime",
        "--tts qwen3",
        f"--qwen3_tts_device {tts_device}",
        "--qwen3_tts_dtype bfloat16",
        "--qwen3_tts_backend torch",
        "--qwen3_tts_speaker Aiden",
        "--parakeet_tdt_device cpu",
        "--num_pipelines 1",
        f"--model_name {brain.model}",
    ]
    if brain.kind == "ollama":
        parts += [
            f"--responses_api_base_url {brain.base_url}",
            "--responses_api_api_key ollama",
        ]
    return " ".join(parts)


def _run(cmd: list[str], *, timeout: int, cwd: Path | None = None) -> None:
    """Run a build step, streaming its output into the progress tail.

    The deadline is enforced by ``wait(timeout=…)`` on the process itself,
    never by the output stream: a step that stalls SILENTLY (a hung pip
    download emits nothing) previously blocked forever in ``readline`` and
    left the install "running" with no cancel path (review 2026-08-07).
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        env=env,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )

    def _pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                with _LOCK:
                    _STATE.detail = line[:200]
                    _STATE.log_tail.append(line[:200])

    pump = threading.Thread(target=_pump, name="local-realtime-install-pump", daemon=True)
    pump.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        raise TimeoutError(f"step timed out after {timeout}s: {' '.join(cmd[:3])}…") from None
    pump.join(timeout=10)
    if code != 0:
        raise RuntimeError(f"step failed (exit {code}): {' '.join(cmd[:3])}…")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate a step's whole process tree, not just the launcher."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        else:
            proc.terminate()
            proc.wait(timeout=10)
    except Exception:  # pragma: no cover — best-effort teardown
        log.warning("local-realtime install: process-tree teardown incomplete", exc_info=True)


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _smoke_boot(launch_command: str, brain: BrainResolution) -> None:
    """Boot the freshly installed server once on a throwaway port.

    Proves the whole stack (imports, patched service, model downloads into
    the HF cache, port bind) before the install may call itself ready.
    Mirrors the adapter's revive spawn exactly: raw string on Windows,
    shlex on POSIX, and the same hardening environment.

    The probe port must be FREE before the spawn: an unrelated listener on
    it would answer the readiness poll and record a success the freshly
    built server never earned (review 2026-08-07).
    """
    if _port_open(_SMOKE_PORT):
        raise RuntimeError(
            f"smoke-boot port {_SMOKE_PORT} is already in use by another "
            "process — close it and retry the install"
        )
    with_port = f"{launch_command} --ws_port {_SMOKE_PORT}"
    cmd: str | list[str] = with_port if os.name == "nt" else shlex.split(with_port)
    smoke_log = install_root() / "smoke_boot.log"
    # The one shared hardening env (faulthandler, unbuffered, HF symlinks on
    # Windows) — the same the supervisor gives the real server, so the smoke
    # boot proves the environment the production spawn will actually use.
    from jarvis.realtime.local_server.supervisor import hardened_child_env

    env = hardened_child_env(inject_openai_key=False)
    if brain.kind == "cloud-openai" and brain.api_key:
        env.setdefault("OPENAI_API_KEY", brain.api_key)
    with open(smoke_log, "ab") as sink:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=sink,
            env=env,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    try:
        deadline = time.monotonic() + _SMOKE_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"server exited during smoke boot (code {proc.returncode}); see {smoke_log}"
                )
            if _port_open(_SMOKE_PORT):
                # The port pre-check above makes this OUR server; still
                # require the process to be alive at success so a crash
                # racing the poll cannot slip through.
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"server exited right after binding (code {proc.returncode}); "
                        f"see {smoke_log}"
                    )
                return
            elapsed = int(time.monotonic() - (deadline - _SMOKE_TIMEOUT_S))
            _set("smoke", 70 + min(25, elapsed // 30), "waiting for first boot (downloads models)")
            time.sleep(5)
        raise TimeoutError(f"server did not come up within {_SMOKE_TIMEOUT_S}s; see {smoke_log}")
    finally:
        _kill_tree(proc)


def start_install(*, confirmed_brain: str = "") -> tuple[bool, str]:
    """Kick off (or resume) the managed install. Returns immediately.

    ``confirmed_brain`` is the brain kind the user saw in the preflight
    they confirmed ("ollama" / "cloud-openai"). The install re-resolves the
    brain and must FAIL if the kind changed in between — silently swapping
    a confirmed "fully local" setup for cloud reasoning would bypass the
    exact honesty note this flow exists for (review 2026-08-07).
    """
    with _LOCK:
        if _STATE.thread is not None and _STATE.thread.is_alive():
            return False, "an install is already running"
        _STATE.phase = "preflight"
        _STATE.percent = 0
        _STATE.error = ""
        _STATE.detail = ""
        _STATE.started_at = time.time()
        _STATE.finished_at = 0.0
        thread = threading.Thread(
            target=_run_install,
            name="local-realtime-install",
            args=(confirmed_brain.strip(),),
            daemon=True,
        )
        _STATE.thread = thread
    thread.start()
    return True, "install started"


def _run_install(confirmed_brain: str = "") -> None:
    try:
        _set("preflight", 2, "checking hardware, disk, and brain")
        report: PreflightReport = run_preflight(install_root())
        if not report.ok:
            _fail(report.blocker)
            return
        assert report.brain is not None and report.tier is not None
        if confirmed_brain and report.brain.kind != confirmed_brain:
            _fail(
                "the reasoning setup changed since your confirmation (was "
                f"{confirmed_brain}, now {report.brain.kind}: "
                f"{report.brain.note}) — run the check again and confirm "
                "the new setup"
            )
            return
        install_root().mkdir(parents=True, exist_ok=True)

        # A live server holds file locks inside the venv (Windows) and the
        # GPU everywhere; installing under it is the mid-tree abort of
        # 2026-08-07. Stop OUR server first — a foreign listener stays.
        try:
            from jarvis.realtime.local_server import supervisor

            supervisor.stop(owned_only=True, install_root=install_root())
        except Exception:  # noqa: BLE001 — best effort, the install continues
            log.debug("install: pre-install server stop failed", exc_info=True)

        if not _venv_python().exists():
            _set("venv", 8, "creating the server's Python environment")
            _run([sys.executable, "-m", "venv", str(_venv_dir())], timeout=300)
        else:
            _set("venv", 8, "reusing the existing environment")

        _set("deps", 15, "installing pinned packages (resumable)")
        pip = [str(_venv_python()), "-m", "pip", "install"]
        torch_pins: tuple[str, ...]
        if report.memory_source == "nvidia-smi":
            torch_pins = _TORCH_PINS_CUDA
            pip_torch = pip + ["--extra-index-url", _TORCH_INDEX_CUDA, *torch_pins]
        else:
            torch_pins = _TORCH_PINS_DEFAULT
            pip_torch = pip + [*torch_pins]
        _run(pip_torch, timeout=_PIP_TIMEOUT_S)
        _set("deps", 45, "installing the server package set")
        _run(pip + ["-r", str(_PINS_FILE)], timeout=_PIP_TIMEOUT_S)

        _set("patch", 60, "applying the vendored create_response patch")
        outcome = apply_create_response_patch(_site_packages())
        _set("patch", 62, f"patch {outcome}")

        _set("smoke", 70, "first boot (downloads voice/hearing models once)")
        launch_command = derive_launch_command(report.brain, memory_source=report.memory_source)
        _smoke_boot(launch_command, report.brain)

        _set("configure", 96, "writing the launch configuration")
        _smoke_marker().write_text(
            json.dumps(
                {
                    "at": time.time(),
                    "tier": report.tier.key,
                    "brain": report.brain.kind,
                    "preflight": report_payload(report),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        from jarvis.core.config_writer import set_local_realtime_launch_command

        set_local_realtime_launch_command(launch_command)

        with _LOCK:
            _STATE.phase = "done"
            _STATE.percent = 100
            _STATE.detail = "installed, patched, smoke-booted, configured"
            _STATE.finished_at = time.time()
        log.info("local-realtime: managed install completed")
    except Exception as exc:  # noqa: BLE001 — every failure must land in the state
        _fail(str(exc))


def _stop_managed_server(root: Path) -> None:
    """Best-effort stop of any process running out of the install tree.

    Deleting a venv under a LIVE server is exactly the mid-tree abort the
    2026-08-07 screenshot showed (WinError 2 deep inside site-packages).
    psutil is optional; without it the tolerant rmtree below still carries
    the removal, just with more retries.
    """
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        log.debug("uninstall: psutil unavailable — skipping process stop")
        return
    needle = str(root).lower()
    for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
        try:
            exe = (proc.info.get("exe") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or ()).lower()
            if needle in exe or needle in cmdline:
                proc.kill()
                proc.wait(timeout=10)
        except (psutil.Error, OSError):  # pragma: no cover — best effort
            continue


def _rmtree_tolerant(root: Path) -> None:
    """Remove a multi-gigabyte tree without dying on races or stale handles.

    FileNotFoundError mid-walk (AV scanner, racing delete) is ignored;
    read-only files get one chmod+retry; transient locks get three whole
    attempts. Windows additionally takes the extended-length path prefix so
    deep node_modules-style paths cannot fail on MAX_PATH.
    """
    import shutil
    import stat

    target = str(root)
    if os.name == "nt" and not target.startswith("\\\\?\\"):
        target = f"\\\\?\\{root.resolve()}"

    def _onerror(func, path, exc_info) -> None:
        exc = exc_info[1]
        if isinstance(exc, FileNotFoundError):
            return
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except FileNotFoundError:
            return

    last_error: OSError | None = None
    for attempt in range(3):
        if not root.exists():
            return
        try:
            shutil.rmtree(target, onerror=_onerror)
            return
        except OSError as exc:  # a still-held handle — give it a moment
            last_error = exc
            time.sleep(2 * (attempt + 1))
    if root.exists() and last_error is not None:
        raise last_error


def _clear_managed_config(root: Path) -> None:
    """Drop OUR launch command from jarvis.toml after a removal.

    Only a command pointing into the deleted tree is cleared — a
    bring-your-own launch command the user authored stays untouched.
    Without this, every later call spawned a nonexistent entry point and
    then sat out the full 120 s patient-reconnect window on a server the
    user had just removed (review 2026-08-07).
    """
    from jarvis.core.config_writer import clear_local_realtime_launch_command

    clear_local_realtime_launch_command(only_if_under=str(root))


def uninstall() -> tuple[bool, str]:
    """Stop the managed server, delete its tree, and clear its config."""
    with _LOCK:
        if _STATE.thread is not None and _STATE.thread.is_alive():
            return False, "an install is running; wait for it to finish first"
    root = install_root()
    if not root.exists():
        _clear_managed_config(root)
        return True, "nothing installed"
    try:
        from jarvis.realtime.local_server import supervisor

        supervisor.stop(owned_only=True, install_root=root)
        supervisor.clear_pidfile()
    except Exception:  # noqa: BLE001 — the needle scan below still runs
        log.debug("uninstall: supervisor stop failed", exc_info=True)
    _stop_managed_server(root)
    try:
        _rmtree_tolerant(root)
    except OSError as exc:
        return False, f"could not remove {root}: {exc}"
    _clear_managed_config(root)
    with _LOCK:
        _STATE.phase = "idle"
        _STATE.percent = 0
        _STATE.detail = ""
        _STATE.error = ""
    return True, f"removed {root}"
