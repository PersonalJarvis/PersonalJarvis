"""Set up, describe and prove the local voice engine on this machine (ADR-0037).

Everything the Local voice card needs lives here: the one-click setup that
builds the engine's own environment, the status the card renders, and the
self-test that proves a real round trip. Nothing in this module runs at boot:
setup starts only from the card (``POST /api/providers/local-voice/setup``),
and the status read only looks at files and the already-running engine.

Setup steps (``docs/local-live-voice-rebuild.md`` section 4.10):

1. ``uv`` — the pinned bootstrap binary (``jarvis.society.browser.bootstrap``).
2. ``python`` — ``uv venv`` with a uv-managed CPython 3.12 under the engine
   home, independent of the app's interpreter.
3. ``packages`` — ``jarvis/voice_engine/requirements-engine.txt`` with CPU torch.
4. ``engine`` — a copy of ``jarvis/voice_engine`` for frozen builds.
5. ``models`` — the speech models from the pinned registry, checksummed.
6. ``voice`` — Pocket TTS weights, fetched once so the worker runs offline.
7. ``llm`` — the language model in the local Ollama server, installed first.
8. ``selftest`` — start the engine and speak, hear and answer once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Card phases. Mirrored by ``LOCAL_VOICE_PHASES`` in the frontend
#: (``src/lib/localVoice.ts``); ``tests/unit/realtime/test_local_voice_parity.py``
#: keeps the two in lockstep (AP-4).
PHASES: tuple[str, ...] = ("not_installed", "installing", "stopped", "starting", "ready", "failed")
#: Machine classes of plan section 4.8 (what decides the defaults).
MACHINE_CLASSES: tuple[str, ...] = ("nvidia", "apple", "gpu", "cpu")
#: Where an expected latency comes from: a bench run on that class, a
#: documented estimate, or nothing until this machine's self-test.
LATENCY_BASES: tuple[str, ...] = ("measured", "estimate", "selftest")
#: Setup stages in order, as the card names them.
SETUP_STAGES: tuple[str, ...] = (
    "uv", "python", "packages", "engine", "models", "voice", "llm", "selftest",
)

_STAGE_WEIGHTS: dict[str, float] = {
    "uv": 0.03, "python": 0.07, "packages": 0.35, "engine": 0.02,
    "models": 0.33, "voice": 0.10, "llm": 0.07, "selftest": 0.03,
}
#: The CPU-only default (plan section 12.3): first clause ~1.5 s on a CPU.
CPU_LLM = "qwen3.5:2b"
GPU_LLM = "qwen3.5:4b"
#: Installed-first candidates per class: an already downloaded model that
#: measured well beats a multi-gigabyte download (plan 12.3).
_LLM_CANDIDATES: dict[str, tuple[str, ...]] = {
    "gpu": (GPU_LLM, "granite4.2:8b", "gemma4:12b-it-qat", CPU_LLM),
    "cpu": (CPU_LLM, GPU_LLM),
}
#: Accelerator memory below this runs the CPU default (6-8 GB is the smallest
#: GPU class plan section 4.8 sizes for).
_GPU_MIN_GB = 6.0
_PYTHON_VERSION = "3.12"
_SETUP_STATE = "setup.json"
_SELFTEST_STATE = "selftest.json"
_COMMAND_TIMEOUT_S = 3600
_LLM_PULL_TIMEOUT_S = 3600.0
_SELFTEST_READY_TIMEOUT_S = 180.0


class SetupError(RuntimeError):
    """A setup step failed; ``str()`` is one sentence for the card."""


# ---------------------------------------------------------------- locations


def engine_home() -> Path:
    from jarvis.voice_engine.paths import engine_home as _home  # noqa: PLC0415

    return _home()


def requirements_file() -> Path:
    return Path(__file__).resolve().parent.parent / "voice_engine" / "requirements-engine.txt"


def requirements_sha256() -> str:
    try:
        return hashlib.sha256(requirements_file().read_bytes()).hexdigest()
    except OSError:
        return ""


def engine_version() -> str:
    from jarvis.voice_engine import ENGINE_VERSION  # noqa: PLC0415

    return ENGINE_VERSION


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


# ------------------------------------------------------------ machine class


def platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def unsupported_reason(
    *,
    system: str | None = None,
    arch: str | None = None,
    mac_version: str | None = None,
    glibc: str | None = None,
) -> str:
    """Why the pinned speech runtimes cannot install here; ``""`` when they can.

    Read from the wheels the pins resolve to (``uv pip compile`` per platform):
    onnxruntime 1.30 ships macOS builds for Apple Silicon on macOS 14+ only
    and Linux builds for glibc 2.28+; torch has no Intel Mac builds. Saying so
    before a download beats a failed install halfway through.
    """
    import platform as _platform  # noqa: PLC0415

    system = system if system is not None else sys.platform
    arch = (arch if arch is not None else _platform.machine()).lower()
    if system == "darwin":
        if arch not in ("arm64", "aarch64"):
            return ("Local voice needs a Mac with Apple Silicon; its speech runtimes have "
                    "no builds for Intel Macs.")
        version = mac_version if mac_version is not None else _platform.mac_ver()[0]
        major = int(version.split(".")[0]) if version.split(".")[0].isdigit() else 0
        if 0 < major < 14:
            return "Local voice needs macOS 14 or newer."
    elif system.startswith("linux"):
        libc = glibc if glibc is not None else _platform.libc_ver()[1]
        parts = [int(p) for p in libc.split(".")[:2] if p.isdigit()]
        if len(parts) == 2 and tuple(parts) < (2, 28):
            return "Local voice needs a Linux with glibc 2.28 or newer."
    return ""


def machine_class(probe: Callable[[], tuple[float, str]] | None = None) -> str:
    """``nvidia`` | ``apple`` | ``gpu`` | ``cpu`` from the shared accelerator probe.

    Hardware only decides what to try; the self-test decides what works
    (plan section 4.8). A probe failure reads as ``cpu``: the slower, safe class.
    """
    if probe is None:
        from jarvis.hardware.detection import usable_accelerator_gb  # noqa: PLC0415

        probe = usable_accelerator_gb
    try:
        gb, source = probe()
    except Exception:  # noqa: BLE001 - a broken probe must not break the card
        log.warning("local voice: accelerator probe failed; assuming a CPU-only machine",
                    exc_info=True)
        return "cpu"
    if gb < _GPU_MIN_GB:
        return "cpu"
    if source == "nvidia-smi":
        return "nvidia"
    if source == "apple-unified":
        return "apple"
    return "gpu"


def default_llm(machine: str) -> str:
    return CPU_LLM if machine == "cpu" else GPU_LLM


def _same_tag(a: str, b: str) -> bool:
    def norm(tag: str) -> str:
        tag = tag.strip()
        return tag[: -len(":latest")] if tag.endswith(":latest") else tag

    return norm(a) == norm(b)


def choose_llm(machine: str, installed: Iterable[str]) -> tuple[str, bool]:
    """``(model, already installed)`` — installed models first, then the default.

    A candidate counts when its exact tag is installed; the class default is
    returned (to be pulled) only when no candidate is on this machine.
    """
    names = [str(n) for n in installed]
    for candidate in _LLM_CANDIDATES["cpu" if machine == "cpu" else "gpu"]:
        if any(_same_tag(candidate, name) for name in names):
            return candidate, True
    return default_llm(machine), False


def expected_latency(machine: str) -> dict[str, Any]:
    """Speech end to first audio, with where the number comes from (plan 4.8, 12.4)."""
    if machine == "nvidia":
        # Bench e2e on a 16 GB NVIDIA card: dialog p50 749-856 ms (section 12.4).
        return {"low_s": 0.75, "high_s": 0.9, "basis": "measured"}
    if machine == "apple":
        return {"low_s": 1.2, "high_s": 1.8, "basis": "estimate"}
    if machine == "cpu":
        return {"low_s": 2.0, "high_s": 2.0, "basis": "estimate"}
    return {"low_s": None, "high_s": None, "basis": "selftest"}


def os_verified(machine: str, platform: str | None = None) -> bool:
    """Whether the engine ever ran on this OS and machine class (plan section 11).

    P0/P1 ran on Windows with an NVIDIA card and in a CPU-only Linux
    container; everything else is honest "not verified yet".
    """
    return (platform or platform_name(), machine) in {("windows", "nvidia"), ("linux", "cpu")}


# --------------------------------------------------------------- setup run


@dataclass
class _Run:
    running: bool = False
    stage: str = ""
    progress: float = 0.0
    detail: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    finished_at: float | None = None


_lock = threading.Lock()
_run = _Run()


def setup_snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "running": _run.running, "stage": _run.stage, "progress": round(_run.progress, 3),
            "detail": _run.detail, "error": _run.error, "warnings": list(_run.warnings),
            "finished_at": _run.finished_at,
        }


def _reset_for_tests() -> None:
    global _run
    with _lock:
        _run = _Run()


def _progress(stage: str, fraction: float, detail: str = "") -> None:
    done = sum(_STAGE_WEIGHTS[s] for s in SETUP_STAGES[: SETUP_STAGES.index(stage)])
    with _lock:
        _run.stage = stage
        _run.progress = min(1.0, done + _STAGE_WEIGHTS[stage] * max(0.0, min(1.0, fraction)))
        _run.detail = detail


@dataclass
class SetupDeps:
    """The outside world setup touches; tests hand in fakes (``tests/fakes``)."""

    home: Path
    ensure_uv: Callable[[Path], str]
    run: Callable[[list[str], dict[str, str], int], None]
    fetch_model: Callable[[str, Path, Callable[[str, int, int], None]], None]
    installed_llms: Callable[[], tuple[set[str], str | None]]
    start_ollama: Callable[[], tuple[bool, str]]
    pull_llm: Callable[[str, Callable[[float], None]], None]
    machine: Callable[[], str]
    configured_llm: Callable[[], str]
    configured_voice: Callable[[], str]
    languages: Callable[[], list[str]]
    selftest: Callable[[], dict[str, Any]] | None = None
    unsupported: Callable[[], str] = unsupported_reason
    package_source: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "voice_engine")


def start_setup(deps: SetupDeps) -> tuple[bool, str]:
    """Start setup in a background thread, or join the one already running."""
    with _lock:
        if _run.running:
            return False, "Setup is already running."
        _run.running = True
        _run.stage, _run.progress, _run.detail, _run.error = SETUP_STAGES[0], 0.0, "", ""
        _run.warnings = []
        _run.finished_at = None
    threading.Thread(target=_run_guarded, args=(deps,), name="local-voice-setup",
                     daemon=True).start()
    return True, "Setup started."


def run_setup_blocking(deps: SetupDeps) -> None:
    """Run setup on the calling thread (tests and the CLI)."""
    with _lock:
        _run.running = True
        _run.error = ""
        _run.warnings = []
    _run_guarded(deps)


def _run_guarded(deps: SetupDeps) -> None:
    try:
        _run_setup(deps)
    except SetupError as exc:
        log.warning("local voice setup failed at %s: %s", _run.stage, exc)
        with _lock:
            _run.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - every failure must reach the card
        log.exception("local voice setup failed at %s", _run.stage)
        with _lock:
            _run.error = f"Setup failed while {_run.stage or 'starting'}: {exc}"
    finally:
        with _lock:
            _run.running = False
            _run.finished_at = time.time()


def _uv_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    # Never install into whatever environment the app itself runs in.
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env.update({
        "UV_PYTHON_INSTALL_DIR": str(home / "python"),
        "UV_CACHE_DIR": str(home / "cache"),
        # A uv-managed CPython, never a store stub or a system build with
        # missing pieces: the engine's interpreter is the same on every OS.
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_NO_PROGRESS": "1",
        "PYTHONUTF8": "1",
    })
    return env


def _engine_python(home: Path) -> Path:
    from jarvis.voice_engine.paths import venv_python  # noqa: PLC0415

    return venv_python(home)


def _models_for(languages: list[str]) -> list[str]:
    from jarvis.voice_engine.models import REGISTRY  # noqa: PLC0415

    core = ["silero-vad-v6", "smart-turn-v3.2", "parakeet-tdt-0.6b-v3-int8"]
    piper = [name for lang in languages for name in REGISTRY if name.startswith(f"piper-{lang}-")]
    return core + piper


def _copy_engine(source: Path, home: Path) -> None:
    """Copy the worker package for builds without an importable source tree."""
    if not (source / "worker.py").is_file():
        raise SetupError("The voice engine's code is missing from this installation.")
    target = home / "app"
    staging = home / "app.new"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging / "jarvis" / "voice_engine",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # A bare namespace marker: the worker must never import the app's package.
    (staging / "jarvis" / "__init__.py").write_text("", encoding="utf-8")
    # Windows briefly locks freshly written folders (virus scanner, indexer):
    # the rename failed with "access denied" in about 1 of 60 test runs.
    for delay in (0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 0.0):
        shutil.rmtree(target, ignore_errors=True)
        try:
            os.replace(staging, target)
            return
        except PermissionError:
            if not delay:
                raise
            log.info("engine folder is locked; retrying the copy in %.2f s", delay)
            time.sleep(delay)


def _run_setup(deps: SetupDeps) -> None:
    home = deps.home
    blocked = deps.unsupported()
    if blocked:
        raise SetupError(blocked)
    home.mkdir(parents=True, exist_ok=True)
    env = _uv_env(home)

    _progress("uv", 0.0, "Getting the Python installer")
    uv = deps.ensure_uv(home / "bin")

    _progress("python", 0.0, f"Creating the engine's Python {_PYTHON_VERSION}")
    python = _engine_python(home)
    if not python.is_file():
        deps.run([uv, "venv", "--python", _PYTHON_VERSION, str(home / "venv")], env,
                 _COMMAND_TIMEOUT_S)

    _progress("packages", 0.0, "Installing the speech runtimes")
    deps.run([uv, "pip", "install", "--python", str(python), "--torch-backend", "cpu",
              "-r", str(requirements_file())], env, _COMMAND_TIMEOUT_S)

    _progress("engine", 0.0, "Copying the voice engine")
    _copy_engine(deps.package_source, home)

    languages = deps.languages()
    names = _models_for(languages)
    for index, name in enumerate(names):
        def report(_file: str, done: int, total: int, index: int = index, name: str = name) -> None:
            part = done / total if total else 0.0
            _progress("models", (index + part) / len(names), f"Downloading {name}")

        _progress("models", index / len(names), f"Downloading {name}")
        deps.fetch_model(name, home / "models", report)

    voice = deps.configured_voice()
    _progress("voice", 0.0, f"Preparing the {voice} voice")
    if voice == "pocket":
        prefetch = ("import sys\nfrom jarvis.voice_engine.tts.pocket import PocketTts\n"
                    "for language in sys.argv[1:]:\n    PocketTts(language)\n")
        voice_env = dict(env)
        voice_env.update({"PYTHONPATH": str(home / "app"), "HF_HOME": str(home / "hf"),
                          "HF_HUB_DISABLE_PROGRESS_BARS": "1"})
        try:
            deps.run([str(python), "-c", prefetch, *languages], voice_env, _COMMAND_TIMEOUT_S)
        except SetupError as exc:
            # Degrade, don't die: the engine falls back to Piper per language.
            with _lock:
                _run.warnings.append(
                    f"The natural voice could not be prepared ({exc}); calls use Piper instead.")

    _progress("llm", 0.0, "Checking the local language model")
    machine = deps.machine()
    model = deps.configured_llm()
    installed, error = deps.installed_llms()
    if error:
        started, detail = deps.start_ollama()
        if not started:
            raise SetupError(f"The local language model server is not available: {detail}")
        installed, error = deps.installed_llms()
        if error:
            raise SetupError(error)
    if model:
        present = any(_same_tag(model, name) for name in installed)
    else:
        model, present = choose_llm(machine, installed)
    if not present:
        _progress("llm", 0.0, f"Downloading {model}")
        deps.pull_llm(model, lambda pct: _progress("llm", pct / 100.0, f"Downloading {model}"))

    _write_json(home / _SETUP_STATE, {
        "engine_version": engine_version(),
        "requirements_sha256": requirements_sha256(),
        "llm_model": model,
        "tts": voice,
        "machine_class": machine,
        "platform": platform_name(),
        "completed_at": time.time(),
    })
    shutil.rmtree(home / "cache", ignore_errors=True)

    _progress("selftest", 0.0, "Testing the voice")
    if deps.selftest is not None:
        report = deps.selftest()
        if not report.get("ok"):
            raise SetupError(str(report.get("reason") or "The self-test did not pass."))
    _progress("selftest", 1.0, "Done")


# ---------------------------------------------------------------- self-test


def selftest_fingerprint(settings: Any) -> dict[str, Any]:
    """What a self-test result is bound to; any change makes it stale (plan 4.10)."""
    return {
        "engine_version": engine_version(),
        "requirements_sha256": requirements_sha256(),
        "llm_model": str(getattr(settings, "llm_model", "")),
        "tts": str(getattr(settings, "tts", "")),
        "languages": list(getattr(settings, "languages", [])),
        "tts_options": dict(getattr(settings, "tts_options", {})),
        "llm_base_url": str(getattr(settings, "llm_base_url", "")),
    }


def save_selftest(home: Path, report: dict[str, Any], settings: Any) -> dict[str, Any]:
    record = {"at": time.time(), "fingerprint": selftest_fingerprint(settings),
              "report": report}
    _write_json(home / _SELFTEST_STATE, record)
    return record


def load_selftest(home: Path, settings: Any) -> dict[str, Any] | None:
    record = _read_json(home / _SELFTEST_STATE)
    report = record.get("report")
    if not isinstance(report, dict):
        return None
    return {
        "ok": bool(report.get("ok")),
        "at": record.get("at"),
        "stale": record.get("fingerprint") != selftest_fingerprint(settings),
        "reason": str(report.get("reason") or ""),
        "languages": report.get("languages") if isinstance(report.get("languages"), dict)
        else {},
        "llm": report.get("llm") if isinstance(report.get("llm"), dict) else None,
    }


async def run_engine_selftest(engine: Any, *, home: Path, settings: Any) -> dict[str, Any]:
    """Start the engine if needed, wait until it is ready, prove one round trip.

    The result is stored even when it fails, so the card shows the last honest
    verdict instead of a stale success.
    """
    engine.reset_failures()
    try:
        await engine.ensure_started()
        ready = await engine.wait_ready(_SELFTEST_READY_TIMEOUT_S)
        if not ready:
            report = {"ok": False,
                      "reason": engine.reason or "The local voice did not finish loading."}
        else:
            report = await engine.selftest()
    except (OSError, RuntimeError, TimeoutError) as exc:
        report = {"ok": False, "reason": f"The self-test could not run: {exc}"}
    save_selftest(home, report, settings)
    return report


class SelftestRunner:
    """One self-test at a time, on the app's event loop; the card polls status."""

    def __init__(self) -> None:
        self._task: asyncio.Task[dict[str, Any]] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, factory: Callable[[], Awaitable[dict[str, Any]]]) -> bool:
        if self.running:
            return False
        self._task = asyncio.get_running_loop().create_task(factory())  # type: ignore[arg-type]
        return True


selftests = SelftestRunner()


def selftest_from_thread(loop: asyncio.AbstractEventLoop,
                         factory: Callable[[], Awaitable[dict[str, Any]]],
                         timeout_s: float = _SELFTEST_READY_TIMEOUT_S + 150.0) -> dict[str, Any]:
    """Run a self-test coroutine on the app loop from setup's worker thread."""
    future: Future[dict[str, Any]] = asyncio.run_coroutine_threadsafe(factory(), loop)  # type: ignore[arg-type]
    return future.result(timeout=timeout_s)


# ------------------------------------------------------------------ status


def _live_state(engine: Any) -> tuple[str, str, float, str]:
    """``(phase, stage, progress, reason)`` of the running engine, if any."""
    if engine is None:
        return "stopped", "", 0.0, ""
    if engine._client is None:  # noqa: SLF001 - the adapter's own state, read-only
        if engine.phase == "failed":
            return "failed", "", 0.0, engine.reason
        if engine.phase == "starting":
            return "starting", engine.stage or "process", engine.progress, ""
        return "stopped", "", 0.0, ""
    if engine.phase == "ready":
        return "ready", "", 1.0, ""
    if engine.phase == "failed":
        return "failed", engine.stage, engine.progress, engine.reason
    return "starting", engine.stage or "process", engine.progress, ""


def _visible_models(names: Iterable[str]) -> list[str]:
    """Installed tags a user picks from, without Jarvis's own derived aliases."""
    try:
        from jarvis.brain.ollama_inventory import is_hidden_alias  # noqa: PLC0415
    except ImportError:
        return sorted(names)
    return sorted(n for n in names if not is_hidden_alias(n))


async def _without_toolless(names: set[str]) -> set[str]:
    """Drop models that declare capabilities but not ``tools`` (embedding, OCR).

    Live voice needs tool calls; an embedding model on the picker can never
    answer. A model whose capabilities are unknown stays (empty = unknown,
    never "can do nothing"), and so does every name when Ollama's inventory
    cannot be read.
    """
    try:
        from jarvis.brain.ollama_inventory import (  # noqa: PLC0415
            OllamaServerError,
            cached_snapshot,
            same_model,
        )
        from jarvis.brain.ollama_pull import server_root  # noqa: PLC0415
    except ImportError:
        return names
    try:
        snapshot = await cached_snapshot(server_root())
    except OllamaServerError as exc:
        log.info("Ollama inventory unreadable; offering every installed model: %s", exc)
        return names
    toolless = [m.name for m in snapshot.models if m.capabilities and "tools" not in m.capabilities]
    return {n for n in names if not any(same_model(n, t) for t in toolless)}


async def card_status(
    cfg: Any,
    *,
    installed_llms: Callable[[], Awaitable[tuple[set[str], str | None]]] | None = None,
    machine: str | None = None,
    tool_capable: Callable[[set[str]], Awaitable[set[str]]] | None = None,
) -> dict[str, Any]:
    """Everything the Local voice card renders, in one payload.

    Reads files, the setup run, the already-running engine and Ollama's tag
    list; it never starts the engine and never downloads anything.
    """
    from jarvis.core.config import VOICE_ENGINE_VOICES  # noqa: PLC0415
    from jarvis.plugins.realtime.local_voice import (  # noqa: PLC0415
        NOT_SET_UP_REASON,
        EngineSettings,
        LocalVoiceProvider,
        engine_installed,
    )
    from jarvis.voice_engine.paths import read_setup_state  # noqa: PLC0415

    settings = EngineSettings.from_config(cfg)
    home = Path(settings.home) if settings.home else engine_home()
    installed = await asyncio.to_thread(engine_installed, settings)
    run = setup_snapshot()
    if machine is None:
        machine = await asyncio.to_thread(machine_class)
    if installed_llms is None:
        from jarvis.brain.ollama_pull import installed_models  # noqa: PLC0415

        installed_llms = installed_models
    names, llm_error = await installed_llms()
    choices = set(names)
    if choices and not llm_error:
        choices = await (tool_capable or _without_toolless)(choices)

    section = getattr(cfg, "voice_engine", None)
    configured = str(getattr(section, "llm_model", "") or "")
    recorded = await asyncio.to_thread(read_setup_state, home)
    if configured:
        llm_model, llm_source = configured, "config"
    elif recorded.get("llm_model"):
        llm_model, llm_source = str(recorded["llm_model"]), "setup"
    else:
        llm_model, _present = choose_llm(machine, choices)
        llm_source = "default"
    llm_installed = None if llm_error else any(_same_tag(llm_model, n) for n in names)

    blocked = unsupported_reason()
    engine = LocalVoiceProvider._engine  # noqa: SLF001 - never create one for a status read
    live = engine if engine is not None and engine.settings == settings else None
    if run["running"]:
        phase, stage, progress, reason = "installing", run["stage"], run["progress"], run["detail"]
    elif not installed:
        phase = "failed" if run["error"] else "not_installed"
        stage, progress, reason = "", 0.0, run["error"] or blocked or NOT_SET_UP_REASON
    else:
        phase, stage, progress, reason = _live_state(live)
    selftest = await asyncio.to_thread(load_selftest, home, settings)
    platform = platform_name()
    return {
        "phase": phase,
        "stage": stage,
        "progress": round(float(progress), 3),
        "reason": reason,
        "installed": installed,
        # False where the pinned runtimes have no builds (Intel Mac, macOS < 14,
        # old glibc); ``reason`` then says why and the card offers no setup.
        "supported": not blocked,
        "setup": run,
        "llm_model": llm_model,
        "llm_source": llm_source,
        "llm_installed": llm_installed,
        "llm_error": llm_error or "",
        "llm_choices": _visible_models(choices),
        "voice": settings.tts,
        "voices": list(VOICE_ENGINE_VOICES),
        "machine_class": machine,
        "expected_latency": expected_latency(machine),
        "platform": platform,
        "os_verified": os_verified(machine, platform),
        "selftest": selftest,
        "selftest_running": selftests.running,
    }


# ------------------------------------------------------------- real deps


def _run_command(cmd: list[str], env: dict[str, str], timeout: int) -> None:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    try:
        result = subprocess.run(  # noqa: S603 - fixed argv built above, no shell
            cmd, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"{Path(cmd[0]).name} could not run: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        log.warning("local voice setup: %s exited %s: %s", cmd[:3], result.returncode,
                    "\n".join(tail))
        raise SetupError(" ".join(tail)[-400:] or f"{Path(cmd[0]).name} failed")


def _fetch_model(name: str, root: Path, progress: Callable[[str, int, int], None]) -> None:
    from jarvis.voice_engine import models  # noqa: PLC0415

    try:
        models.fetch(name, root, progress=progress)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SetupError(f"Downloading {name} failed: {exc}") from exc


def _installed_llms() -> tuple[set[str], str | None]:
    from jarvis.brain.ollama_pull import installed_models  # noqa: PLC0415

    return asyncio.run(installed_models())


def _start_ollama() -> tuple[bool, str]:
    from jarvis.brain.ollama_runtime import start_server  # noqa: PLC0415

    return start_server()


def _pull_llm(model: str, progress: Callable[[float], None]) -> None:
    """Pull one Ollama model, all inside one event loop (its task lives there)."""

    async def pull() -> None:
        from jarvis.brain.ollama_pull import pull_status, start_pull  # noqa: PLC0415

        first = await start_pull(model)
        if str(first.get("state")) == "error":
            raise SetupError(str(first.get("message") or f"Downloading {model} failed."))
        deadline = time.monotonic() + _LLM_PULL_TIMEOUT_S
        while time.monotonic() < deadline:
            status = await pull_status(model)
            state = str(status.get("state"))
            if state == "done":
                return
            if state == "error":
                raise SetupError(str(status.get("error") or status.get("message")
                                     or f"Downloading {model} failed."))
            progress(float(status.get("percent") or 0.0))
            await asyncio.sleep(2)
        raise SetupError(f"Downloading {model} did not finish within an hour.")

    asyncio.run(pull())


def _ensure_uv(root: Path) -> str:
    from jarvis.society.browser.bootstrap import ensure_uv  # noqa: PLC0415

    try:
        # The pinned uv, never an older one from PATH: setup needs
        # ``--torch-backend`` and managed Python downloads.
        return ensure_uv(root, prefer_path=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SetupError(f"The Python installer could not be downloaded: {exc}") from exc


def real_deps(*, loop: asyncio.AbstractEventLoop | None,
              config_loader: Callable[[], Any]) -> SetupDeps:
    """Setup wired to this machine. ``loop`` runs the closing self-test."""

    def section() -> Any:
        return getattr(config_loader(), "voice_engine", None)

    def selftest() -> dict[str, Any]:
        from jarvis.plugins.realtime.local_voice import (  # noqa: PLC0415
            EngineSettings,
            LocalVoiceProvider,
        )

        cfg = config_loader()
        settings = EngineSettings.from_config(cfg)
        engine = LocalVoiceProvider.shared_engine(cfg)
        home = engine_home()
        if loop is None:
            return {"ok": False, "reason": "No event loop to run the self-test on."}
        return selftest_from_thread(
            loop, lambda: run_engine_selftest(engine, home=home, settings=settings))

    return SetupDeps(
        home=engine_home(),
        ensure_uv=_ensure_uv,
        run=_run_command,
        fetch_model=_fetch_model,
        installed_llms=_installed_llms,
        start_ollama=_start_ollama,
        pull_llm=_pull_llm,
        machine=machine_class,
        configured_llm=lambda: str(getattr(section(), "llm_model", "") or ""),
        configured_voice=lambda: str(getattr(section(), "tts", "") or "pocket"),
        languages=lambda: list(getattr(section(), "languages", None) or ["de", "en"]),
        selftest=selftest,
    )
