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
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.realtime.local_server.brain_link import BrainResolution
from jarvis.realtime.local_server.patching import (
    PATCH_TARGET_VERSION,
    apply_create_response_patch,
    apply_pocket_language_patch,
    patch_state,
)
from jarvis.realtime.local_server.preflight import (
    PreflightReport,
    report_payload,
    run_preflight,
)

log = logging.getLogger(__name__)

_PINS_FILE = Path(__file__).parent / "pins" / f"core-{PATCH_TARGET_VERSION}.txt"
_SMOKE_MARKER_SCHEMA = 1

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
    # The VENV's own layout, not the host interpreter's: deriving the folder
    # from this process's sys.version_info breaks the moment the venv was
    # created by a different Python than the one running Jarvis (containered
    # installs, pyenv hosts). Glob the real directory; fall back to the host
    # figure only while the venv does not exist yet.
    candidates = sorted((_venv_dir() / "lib").glob("python*/site-packages"))
    if candidates:
        return candidates[0]
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return _venv_dir() / "lib" / version / "site-packages"


def _smoke_marker() -> Path:
    return install_root() / "smoke_ok.json"


def _read_smoke_marker_payload() -> dict[str, object] | None:
    try:
        payload = json.loads(_smoke_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Absent or corrupt marker means "not proven yet" — the caller re-runs smoke.
        return None
    return payload if isinstance(payload, dict) else None


def _smoke_marker_valid() -> bool:
    """Validate the durable proof for this exact pinned server build."""
    payload = _read_smoke_marker_payload()
    if payload is None:
        return False
    at = payload.get("at")
    valid_at = (
        isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0.0
    )
    return (
        payload.get("schema") == _SMOKE_MARKER_SCHEMA
        and payload.get("patch_version") == PATCH_TARGET_VERSION
        and valid_at
        and isinstance(payload.get("tier"), str)
        and bool(payload.get("tier"))
        and payload.get("brain") in {"ollama", "cloud-openai"}
        and isinstance(payload.get("preflight"), dict)
    )


def _invalidate_smoke_marker() -> None:
    """Remove old success proof before any reinstall can mutate the venv."""
    _smoke_marker().unlink(missing_ok=True)


def _write_smoke_marker(payload: dict[str, object]) -> None:
    """Durably publish success only after the smoke child was fully reaped."""
    from jarvis.realtime.local_server.supervisor import _atomic_write_json

    if not _atomic_write_json(_smoke_marker(), payload):
        raise OSError("could not atomically write the managed-server smoke marker")


def repair_smoke_marker_from_live_runtime(base_url: str) -> bool:
    """Recover missing or legacy proof from a verified live managed runtime.

    A ready pool owned by this exact patched install is stronger smoke evidence
    than a marker written by an older Jarvis version. Foreign listeners,
    partial installs, failed preflight, and drifted patches remain fail-closed.
    """
    if _smoke_marker_valid():
        return True
    from jarvis.realtime.local_server import supervisor

    # Marker publication must be serialized with install/uninstall. Otherwise
    # a warm worker could restore a valid marker after a failed reinstall had
    # deliberately invalidated it, falsely advertising a half-mutated venv.
    with supervisor.lifecycle_guard() as guarded:
        if not guarded:
            return False
        return _repair_smoke_marker_from_live_runtime_unlocked(base_url)


def _repair_smoke_marker_from_live_runtime_unlocked(base_url: str) -> bool:
    """Repair proof while the caller owns the server lifecycle lease."""
    if _smoke_marker_valid():
        return True
    if (
        not _venv_python().exists()
        or not _server_entrypoint().exists()
        or patch_state(_site_packages()) != "patched"
    ):
        return False
    try:
        from jarvis.realtime.local_server import supervisor

        runtime = supervisor.status(base_url)
        if not bool(runtime.get("ready")) or not bool(runtime.get("owned")):
            return False
        report = run_preflight(install_root())
        if not report.ok or report.tier is None or report.brain is None:
            return False
        if report.brain.kind not in {"ollama", "cloud-openai"}:
            return False
        _write_smoke_marker(
            {
                "schema": _SMOKE_MARKER_SCHEMA,
                "patch_version": PATCH_TARGET_VERSION,
                "at": time.time(),
                "tier": report.tier.key,
                "brain": report.brain.kind,
                "preflight": report_payload(report),
                "repair_source": "owned-live-runtime",
            }
        )
    except Exception:  # noqa: BLE001 - readiness repair is advisory, never fatal
        log.warning("local-realtime: live smoke-proof repair failed", exc_info=True)
        return False
    log.info("local-realtime: repaired smoke proof from the owned live runtime")
    return True


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
        "smoke_boot": _smoke_marker_valid(),
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
        "--ws_host 127.0.0.1",
        "--tts qwen3",
        f"--qwen3_tts_device {tts_device}",
        "--qwen3_tts_dtype bfloat16",
        "--qwen3_tts_backend torch",
        "--qwen3_tts_speaker Aiden",
        "--parakeet_tdt_device cpu",
        # Jarvis consumes only final input transcripts. Progressive Parakeet
        # passes therefore contend with the final pass without improving the
        # product surface. A short acoustic silence gate removes breath-sized
        # splits; Smart Turn still keeps semantically incomplete speech open
        # for its full two-second window before any final can escape.
        "--no_enable_live_transcription",
        "--min_silence_ms 320",
        "--smart_turn_incomplete_delay_ms 2000",
        "--unanswered_reopen_ms 2000",
        "--num_pipelines 1",
        f"--model_name {brain.model}",
    ]
    if brain.kind == "ollama":
        parts += [
            # Ollama exposes its reliable no-thinking control on Chat
            # Completions. Its Responses endpoint does not accept that field,
            # which made Qwen spend hundreds of hidden reasoning tokens before
            # even a four-word voice reply.
            "--llm_backend chat-completions",
            "--responses_api_reasoning_effort none",
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
    popen_kwargs: dict[str, Any] = {}
    if os.name != "nt":
        # The timeout path kills this process group. Without a fresh session,
        # pip's build-backend children can outlive the launcher and continue
        # mutating the venv after the lifecycle lease is released.
        popen_kwargs["start_new_session"] = True
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
        **popen_kwargs,
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


def _kill_tree(proc: subprocess.Popen) -> bool:
    """Terminate a step's whole process tree, not just the launcher.

    POSIX: every installer child spawns with ``start_new_session=True``, so
    the pid is its process-group leader — SIGTERM the group, grace, then
    SIGKILL what remains. Terminating only the launcher (the old behavior)
    orphaned build backends and server workers.
    """
    if proc.poll() is not None:
        return True
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Reported through the returned bool, which the caller below
                # logs as an incomplete teardown.
                return False
            return proc.poll() is not None
        else:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGTERM)  # type: ignore[attr-defined]
            except (ProcessLookupError, PermissionError, OSError):
                # No process group to signal — fall back to killing just the launcher.
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # SIGTERM did not land in time — escalate to SIGKILL; the
                # final poll() below still reports overall success/failure.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[attr-defined]
                except (ProcessLookupError, PermissionError, OSError):
                    # No process group to signal — fall back to killing just the launcher.
                    proc.kill()
                proc.wait(timeout=10)
            return proc.poll() is not None
    except Exception:  # pragma: no cover - teardown failure is reported by caller
        log.warning("local-realtime install: process-tree teardown incomplete", exc_info=True)
        return False


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        # Connection refused/timed out — the port simply is not open yet.
        return False


def _smoke_boot(launch_command: str, brain: BrainResolution) -> None:
    """Boot the freshly installed server once on a throwaway port.

    Proves the whole stack (imports, patched service, model downloads into
    the HF cache, and a live model pool) before the install may call itself
    ready. A TCP bind alone is not model readiness.
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
    from jarvis.realtime.local_server.supervisor import (
        _kill_by_install_root,
        hardened_child_env,
        probe_runtime,
    )

    env = hardened_child_env(inject_openai_key=False)
    if brain.kind == "cloud-openai" and brain.api_key:
        env.setdefault("OPENAI_API_KEY", brain.api_key)
    popen_kwargs: dict[str, Any] = {}
    if os.name != "nt":
        # Its own process group so _kill_tree's killpg reaches every worker.
        popen_kwargs["start_new_session"] = True
    with open(smoke_log, "ab") as sink:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=sink,
            env=env,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            **popen_kwargs,
        )
    try:
        deadline = time.monotonic() + _SMOKE_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"server exited during smoke boot (code {proc.returncode}); see {smoke_log}"
                )
            pool = probe_runtime(f"http://127.0.0.1:{_SMOKE_PORT}", timeout=1.0)
            if pool is not None:
                # The port pre-check above makes this OUR server; still
                # require the process to be alive at success so a crash
                # racing the pool response cannot slip through.
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"server exited right after binding (code {proc.returncode}); "
                        f"see {smoke_log}"
                    )
                _set("smoke", 94, "testing a complete text-to-speech response")
                from jarvis.realtime.local_server.smoke import (
                    probe_voice_roundtrip_sync,
                )

                probe_voice_roundtrip_sync(
                    f"http://127.0.0.1:{_SMOKE_PORT}", timeout_s=90.0
                )
                return
            elapsed = int(time.monotonic() - (deadline - _SMOKE_TIMEOUT_S))
            _set("smoke", 70 + min(25, elapsed // 30), "waiting for first boot (downloads models)")
            time.sleep(5)
        raise TimeoutError(f"server did not come up within {_SMOKE_TIMEOUT_S}s; see {smoke_log}")
    finally:
        tree_stopped = _kill_tree(proc)
        _swept, survivors = _kill_by_install_root(install_root())
        if survivors or (not tree_stopped and proc.poll() is None):
            raise RuntimeError(
                f"smoke-boot process {proc.pid} survived teardown; see {smoke_log}"
            )


def start_install(
    *,
    confirmed_brain: str = "",
    brain_model: str = "",
    voice_model: str = "",
) -> tuple[bool, str]:
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
            args=(
                confirmed_brain.strip(),
                brain_model.strip(),
                voice_model.strip(),
            ),
            daemon=True,
        )
        _STATE.thread = thread
    thread.start()
    return True, "install started"


def _run_install(
    confirmed_brain: str = "",
    brain_model: str = "",
    voice_model: str = "",
) -> None:
    """Hold the cross-process server lifecycle lease for the whole install."""
    from jarvis.realtime.local_server import supervisor

    with supervisor.lifecycle_guard() as guarded:
        if not guarded:
            _fail("another local-realtime lifecycle operation is already running")
            return
        if brain_model or voice_model:
            _run_install_guarded(confirmed_brain, brain_model, voice_model)
        else:
            # Keep the long-standing one-argument seam used by embedders and
            # tests while extending new installs with explicit model choices.
            _run_install_guarded(confirmed_brain)


def _pull_brain_model(model: str) -> None:
    """Pull one Ollama model synchronously, forwarding progress into the state.

    The pull machinery is async and keeps its download task on the calling
    loop, so the WHOLE pull must live inside one ``asyncio.run`` — starting
    it in one loop and polling from another would orphan the task the
    moment the first loop closes.
    """
    import asyncio

    async def _pull_and_wait() -> None:
        from jarvis.brain.ollama_pull import pull_status, start_pull

        first = await start_pull(model)
        if str(first.get("state")) == "error":
            raise RuntimeError(str(first.get("message") or "model download failed"))
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            status = await pull_status(model)
            state = str(status.get("state"))
            if state == "done":
                return
            if state == "error":
                raise RuntimeError(
                    str(
                        status.get("error")
                        or status.get("message")
                        or "model download failed"
                    )
                )
            percent = float(status.get("percent") or 0.0)
            _set(
                "brain-setup",
                6,
                f"downloading the brain model {model} ({percent:.0f}%)",
            )
            await asyncio.sleep(3)
        raise TimeoutError(f"the brain model download did not finish: {model}")

    asyncio.run(_pull_and_wait())


def resolve_brain_for_install(preferred_model: str = "") -> BrainResolution:
    """The brain resolution the install engine trusts (own seam for tests)."""
    from jarvis.realtime.local_server.brain_link import resolve_brain
    from jarvis.realtime.local_server.preflight import (
        _preferred_brain_model,
        _usable_accelerator_gb,
    )

    usable_gb, _source = _usable_accelerator_gb()
    return resolve_brain(
        preferred_model=preferred_model or _preferred_brain_model(),
        usable_gb=usable_gb,
    )


def _setup_local_brain(preferred_model: str = "") -> None:
    """Make the Ollama brain real: runtime installed + running + model present.

    The single-click promise (maintainer directive 2026-08-08): selecting
    local models must never end in a terminal command. Raises with the
    honest platform reason when a silent setup is impossible — the install
    then fails with that exact sentence instead of a generic one.
    """
    from jarvis.brain import ollama_runtime
    from jarvis.realtime.local_server.brain_link import _PREFERRED_MODELS

    _set("brain-setup", 4, "setting up the local brain (Ollama)")
    ok, detail = ollama_runtime.ensure_runtime_blocking()
    if not ok:
        raise RuntimeError(detail)
    resolved = (
        resolve_brain_for_install(preferred_model)
        if preferred_model
        else resolve_brain_for_install()
    )
    if resolved.ok and (not preferred_model or resolved.model == preferred_model):
        return
    _set("brain-setup", 5, "no usable brain model yet — downloading one")
    _pull_brain_model(preferred_model or _PREFERRED_MODELS[0])

    if not preferred_model:
        return
    verified = resolve_brain_for_install(preferred_model)
    if not verified.ok or verified.model != preferred_model:
        raise RuntimeError(
            f"the selected brain model is not usable after download: {preferred_model}"
        )


def _run_install_guarded(
    confirmed_brain: str = "",
    brain_model: str = "",
    voice_model: str = "",
) -> None:
    try:
        # Marker-schema upgrades are metadata debt, not grounds to tear down a
        # healthy multi-gigabyte runtime. Only this exact patched install's
        # owned, fully-ready pool may take the non-destructive repair path.
        if (
            not brain_model
            and not voice_model
            and not _smoke_marker_valid()
            and _repair_smoke_marker_from_live_runtime_unlocked(
                f"http://127.0.0.1:{SERVE_PORT}"
            )
        ):
            marker = _read_smoke_marker_payload() or {}
            repaired_brain = str(marker.get("brain") or "")
            if not confirmed_brain or repaired_brain == confirmed_brain:
                with _LOCK:
                    _STATE.phase = "done"
                    _STATE.percent = 100
                    _STATE.detail = "installed; smoke proof repaired from the live server"
                    _STATE.error = ""
                    _STATE.finished_at = time.time()
                log.info("local-realtime: install proof repaired without server teardown")
                return
        _set("preflight", 2, "checking hardware, disk, and brain")
        report: PreflightReport = (
            run_preflight(install_root(), preferred_model=brain_model)
            if brain_model
            else run_preflight(install_root())
        )
        if (
            confirmed_brain == "ollama"
            and report.tier is not None
            and report.brain is not None
            and (
                report.brain.kind == "blocked"
                or (brain_model and report.brain.model != brain_model)
            )
        ):
            # Hardware and disk are fine; only the BRAIN is missing — and
            # the user just confirmed a fully-local install. Set it up
            # (install/start Ollama, pull the model) and check again.
            if brain_model:
                _setup_local_brain(brain_model)
            else:
                _setup_local_brain()
            _set("preflight", 7, "re-checking after the brain setup")
            report = (
                run_preflight(install_root(), preferred_model=brain_model)
                if brain_model
                else run_preflight(install_root())
            )
        if not report.ok:
            _fail(report.blocker)
            return
        assert report.brain is not None and report.tier is not None
        if brain_model and report.brain.model != brain_model:
            _fail(
                f"the selected brain model is not available or does not fit: {brain_model}"
            )
            return
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

            stopped, stop_message = supervisor._stop_owned_unlocked(
                owned_only=True,
                install_root=install_root(),
            )
            if not stopped and stop_message != "no owned server process found":
                _fail(f"could not stop the existing managed server: {stop_message}")
                return
        except Exception as exc:  # noqa: BLE001 - never install under a live server
            log.warning("install: pre-install server stop failed", exc_info=True)
            _fail(f"could not stop the existing managed server safely: {exc}")
            return

        # From this point onward a failure means the previous installation is
        # no longer proven. Invalidate before the first venv mutation so a
        # failed reinstall can never leave stale "ready" evidence behind.
        _invalidate_smoke_marker()

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

        _set("patch", 60, "applying the vendored realtime compatibility patches")
        response_outcome = apply_create_response_patch(_site_packages())
        pocket_outcome = apply_pocket_language_patch(_site_packages())
        _set("patch", 62, f"protocol {response_outcome}; Pocket TTS {pocket_outcome}")

        _set("smoke", 70, "first boot (downloads voice/hearing models once)")
        launch_command = derive_launch_command(report.brain, memory_source=report.memory_source)
        if voice_model:
            from jarvis.realtime.local_server.model_catalog import apply_voice_profile

            launch_command = apply_voice_profile(launch_command, voice_model)
        if report.brain.kind == "ollama":
            # The OpenAI-compatible Ollama API cannot carry num_ctx per request.
            # Create/use the managed 8k profile before the first smoke boot so
            # installation itself cannot reserve a model's full native context
            # and push the desktop into shared-GPU-memory paging.
            from jarvis.realtime.local_server import supervisor as local_supervisor

            launch_command = local_supervisor.prepare_voice_brain_command(
                launch_command
            )
        _smoke_boot(launch_command, report.brain)

        _set("configure", 96, "writing the launch configuration")
        _write_smoke_marker(
            {
                "schema": _SMOKE_MARKER_SCHEMA,
                "patch_version": PATCH_TARGET_VERSION,
                "at": time.time(),
                "tier": report.tier.key,
                "brain": report.brain.kind,
                "brain_model": report.brain.model,
                "voice_model": voice_model or "qwen3-tts-1.7b",
                "preflight": report_payload(report),
            }
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
            # Vanished during the retry — already gone, same as the check above.
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
    """Serialize the entire removal against every server lifecycle action."""
    from jarvis.realtime.local_server import supervisor

    with supervisor.lifecycle_guard() as guarded:
        if not guarded:
            return False, "another local-realtime lifecycle operation is already running"
        return _uninstall_guarded()


def _uninstall_guarded() -> tuple[bool, str]:
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

        stopped, stop_message = supervisor._stop_owned_unlocked(
            owned_only=True,
            install_root=root,
        )
        if not stopped and stop_message != "no owned server process found":
            return False, f"could not stop the managed server safely: {stop_message}"
        supervisor.clear_pidfile()
    except Exception as exc:  # noqa: BLE001 - never delete under a live server
        log.warning("uninstall: supervisor stop failed", exc_info=True)
        return False, f"could not stop the managed server safely: {exc}"
    try:
        _rmtree_tolerant(root)
    except OSError as exc:
        # Reported through the returned (ok, message) tuple, not a log call.
        return False, f"could not remove {root}: {exc}"
    _clear_managed_config(root)
    with _LOCK:
        _STATE.phase = "idle"
        _STATE.percent = 0
        _STATE.detail = ""
        _STATE.error = ""
    return True, f"removed {root}"
