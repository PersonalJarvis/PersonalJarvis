"""Hardware detection & Whisper model recommendation.

On first run, Jarvis analyses the local hardware and recommends the optimal
STT configuration (local Whisper model vs. cloud API) to the user.

All checks are read-only; nothing is installed or modified.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# DataClasses
# ----------------------------------------------------------------------


@dataclass(slots=True)
class GPUInfo:
    name: str
    vram_mb: int
    cuda_version: str | None = None
    compute_capability: str | None = None


@dataclass(slots=True)
class HardwareReport:
    os_name: str
    os_version: str
    cpu_name: str
    cpu_cores_physical: int
    cpu_cores_logical: int
    ram_total_mb: int
    ram_available_mb: int
    gpus: list[GPUInfo] = field(default_factory=list)
    python_version: str = ""
    python_executable: str = ""
    cuda_runtime: str | None = None
    torch_cuda_available: bool = False
    ffmpeg_version: str | None = None
    existing_installs: dict[str, str] = field(default_factory=dict)

    @property
    def has_nvidia_gpu(self) -> bool:
        return any("nvidia" in g.name.lower() or g.cuda_version for g in self.gpus)

    @property
    def total_vram_mb(self) -> int:
        return sum(g.vram_mb for g in self.gpus)


@dataclass(slots=True)
class WhisperRecommendation:
    """Recommended Whisper configuration based on detected hardware."""

    provider: str  # "faster-whisper" | "openai-api"
    model: str  # tiny | base | small | large-v3-turbo | large-v3
    device: str  # cuda | cpu
    compute_type: str  # int8_float16 | fp16 | int8
    expected_latency_ms: int
    rationale: str


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        return (result.stdout or "") + (result.stderr or "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _detect_cpu() -> tuple[str, int, int]:
    name = platform.processor() or "unknown"
    try:
        import psutil  # type: ignore[import-untyped]

        return name, psutil.cpu_count(logical=False) or 0, psutil.cpu_count(logical=True) or 0
    except ImportError:
        return name, 0, 0


def _detect_ram() -> tuple[int, int]:
    try:
        import psutil  # type: ignore[import-untyped]

        vm = psutil.virtual_memory()
        return vm.total // (1024 * 1024), vm.available // (1024 * 1024)
    except ImportError:
        return 0, 0


def _detect_nvidia_gpus() -> list[GPUInfo]:
    """Tries pynvml first, falls back to nvidia-smi."""
    gpus: list[GPUInfo] = []

    try:
        import pynvml  # type: ignore[import-untyped]

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            cc_major, cc_minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
            gpus.append(
                GPUInfo(
                    name=name,
                    vram_mb=mem.total // (1024 * 1024),
                    compute_capability=f"{cc_major}.{cc_minor}",
                )
            )
        pynvml.nvmlShutdown()
        return gpus
    except Exception:  # noqa: BLE001 — no pynvml / no driver is a normal state
        log.debug("hardware: pynvml probe unavailable, falling back to nvidia-smi", exc_info=True)

    # Fallback: nvidia-smi
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if not out.strip():
        return gpus
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                gpus.append(GPUInfo(name=parts[0], vram_mb=int(parts[1])))
            except ValueError:
                continue
    return gpus


def _detect_cuda_version() -> str | None:
    out = _run(["nvidia-smi"])
    for line in out.splitlines():
        if "CUDA Version:" in line:
            # Format: "| ... CUDA Version: 12.8 |"
            tail = line.split("CUDA Version:")[-1]
            return tail.split("|")[0].strip() or None
    return None


def _detect_torch_cuda() -> bool:
    try:
        import torch  # type: ignore[import-untyped]

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _detect_ffmpeg() -> str | None:
    if not shutil.which("ffmpeg"):
        return None
    out = _run(["ffmpeg", "-version"])
    first = out.splitlines()[0] if out else ""
    # Form: "ffmpeg version 8.0.1 Copyright ..."
    if "ffmpeg version" in first:
        parts = first.split()
        try:
            return parts[2]
        except IndexError:
            return "unknown"
    return None


def _detect_existing_installs() -> dict[str, str]:
    """Checks whether relevant Python packages are already installed."""
    packages = [
        "anthropic",
        "openai",
        "faster-whisper",
        "torch",
        "sounddevice",
        "keyring",
    ]
    found: dict[str, str] = {}
    for pkg in packages:
        try:
            from importlib import metadata

            found[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            continue
    return found


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def analyze() -> HardwareReport:
    """Full hardware analysis (run once during setup and on demand)."""
    cpu_name, cpu_phys, cpu_log = _detect_cpu()
    ram_total, ram_avail = _detect_ram()

    return HardwareReport(
        os_name=platform.system(),
        os_version=platform.version(),
        cpu_name=cpu_name,
        cpu_cores_physical=cpu_phys,
        cpu_cores_logical=cpu_log,
        ram_total_mb=ram_total,
        ram_available_mb=ram_avail,
        gpus=_detect_nvidia_gpus(),
        python_version=sys.version.split()[0],
        python_executable=sys.executable,
        cuda_runtime=_detect_cuda_version(),
        torch_cuda_available=_detect_torch_cuda(),
        ffmpeg_version=_detect_ffmpeg(),
        existing_installs=_detect_existing_installs(),
    )


def recommend_whisper(
    report: HardwareReport,
    gpu_inference_verified: bool | None = None,
) -> WhisperRecommendation:
    """Maps a HardwareReport to the recommended Whisper configuration.

    Heuristic:
    - VERIFIED NVIDIA GPU with >= 4 GB VRAM → large-v3-turbo (fast, MULTILINGUAL)
    - VERIFIED NVIDIA GPU with < 4 GB VRAM  → base (multilingual)
    - No/unverified GPU but plenty of RAM   → CPU faster-whisper base
    - Otherwise                             → OpenAI Whisper API

    ``gpu_inference_verified`` is the CAPABILITY gate (AP-21/AP-25). A GPU
    recommendation is handed out ONLY when a real GPU inference has VERIFIABLY
    completed on this host (``True``). CUDA *presence* is necessary but not
    sufficient: a driver/runtime mismatch — or the Blackwell hang that once left
    every CTranslate2 inference wedged while ``torch.cuda.is_available()`` reported
    ``True`` — must never persist a ``device="cuda"`` the host cannot actually run.
    ``None`` (never probed) and ``False`` (probe failed / wedged) both fall back to
    the CPU-first floor. This keeps the path vendor-neutral: an Apple-Silicon Mac
    (no NVIDIA, no CUDA — CTranslate2 has no Metal backend) lands on CPU int8, the
    correct choice there. A verified GPU is still adopted at runtime by the
    background wake probe; this governs only the FIRST-RUN persisted recommendation.

    Never recommends a Distil-Whisper model: all distil-* checkpoints are
    English-only and mangle German/Spanish (the runtime force-upgrades them to
    large-v3-turbo anyway).
    """
    gpu_usable = (
        report.has_nvidia_gpu and report.torch_cuda_available and gpu_inference_verified is True
    )
    if not gpu_usable:
        cuda_present_unverified = report.has_nvidia_gpu and report.torch_cuda_available
        if report.ram_total_mb >= 8192:
            rationale = (
                "CUDA GPU present but its inference is not verified on this host — "
                "recommending CPU 'base' until the runtime GPU probe passes (it is "
                "verified and adopted automatically once voice boots). This avoids "
                "persisting a 'cuda' choice a driver/runtime mismatch cannot run."
                if cuda_present_unverified
                else "No CUDA-capable GPU detected. CPU mode with 'base' delivers "
                "acceptable quality at ~1s latency for 5s audio. "
                "For better latency, configure the OpenAI Whisper API as a fallback."
            )
            return WhisperRecommendation(
                provider="faster-whisper",
                model="base",
                device="cpu",
                compute_type="int8",
                expected_latency_ms=1200,
                rationale=rationale,
            )
        return WhisperRecommendation(
            provider="openai-api",
            model="whisper-1",
            device="cloud",
            compute_type="fp16",
            expected_latency_ms=400,
            rationale=(
                "Limited local resources. OpenAI Whisper API recommended — "
                "the API key is requested in the setup wizard."
            ),
        )

    vram = report.total_vram_mb
    if vram >= 4000:
        # large-v3-turbo, NOT a Distil model: every distil-* checkpoint is
        # English-only (there is no multilingual distil) and mangles German/
        # Spanish into English words — the runtime already force-upgrades them to
        # large-v3-turbo (jarvis/plugins/stt/fwhisper.py::_ENGLISH_ONLY_MODELS),
        # so recommending distil here only persists a confusing, self-overridden
        # value into jarvis.toml. large-v3-turbo is the fast MULTILINGUAL
        # checkpoint (~1.5 GB, fits from 4 GB VRAM up).
        return WhisperRecommendation(
            provider="faster-whisper",
            model="large-v3-turbo",
            device="cuda",
            compute_type="int8_float16",
            expected_latency_ms=250,
            rationale=(
                f"NVIDIA GPU with {vram} MB VRAM — runs large-v3-turbo (fast, "
                f"MULTILINGUAL incl. DE/EN/ES, ~1.5 GB) at ~250ms latency. Optimal "
                f"for local privacy + low latency."
            ),
        )
    return WhisperRecommendation(
        provider="faster-whisper",
        model="base",
        device="cuda",
        compute_type="int8_float16",
        expected_latency_ms=180,
        rationale=(
            f"NVIDIA GPU with only {vram} MB VRAM — 'base' (multilingual) model "
            f"fits. Quality is sufficient for German/English, latency ~180ms."
        ),
    )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _format_report(report: HardwareReport, rec: WhisperRecommendation) -> str:
    lines = [
        "╔══════════════════════════════════════════════════════════╗",
        "║  Jarvis Hardware Analysis                                ║",
        "╚══════════════════════════════════════════════════════════╝",
        "",
        f"OS:            {report.os_name} "
        f"{report.os_version.split()[0] if report.os_version else ''}",
        f"Python:        {report.python_version} ({report.python_executable})",
        f"CPU:           {report.cpu_name}",
        f"               {report.cpu_cores_physical} phys / {report.cpu_cores_logical} log cores",
        f"RAM:           {report.ram_total_mb} MB total, {report.ram_available_mb} MB free",
        "",
    ]
    if report.gpus:
        for i, gpu in enumerate(report.gpus):
            lines.append(f"GPU {i}:         {gpu.name} ({gpu.vram_mb} MB VRAM)")
            if gpu.compute_capability:
                lines.append(f"               Compute Capability: {gpu.compute_capability}")
    else:
        lines.append("GPU:           no NVIDIA GPU detected")

    lines.extend(
        [
            f"CUDA Runtime:  {report.cuda_runtime or 'not installed'}",
            f"PyTorch CUDA:  {'✓ available' if report.torch_cuda_available else '✗ not available'}",
            f"ffmpeg:        {report.ffmpeg_version or 'NOT FOUND — please install'}",
            "",
            "Pre-installed packages:",
        ]
    )
    if report.existing_installs:
        for pkg, version in sorted(report.existing_installs.items()):
            lines.append(f"  - {pkg}: {version}")
    else:
        lines.append("  (no relevant Jarvis dependencies pre-installed)")

    lines.extend(
        [
            "",
            "╔══════════════════════════════════════════════════════════╗",
            "║  Whisper Recommendation                                  ║",
            "╚══════════════════════════════════════════════════════════╝",
            "",
            f"Provider:      {rec.provider}",
            f"Model:         {rec.model}",
            f"Device:        {rec.device}",
            f"Compute type:  {rec.compute_type}",
            f"Latency (est): ~{rec.expected_latency_ms}ms for 5s audio",
            "",
            "Rationale:",
            f"  {rec.rationale}",
            "",
        ]
    )
    return "\n".join(lines)


def check_records(report: HardwareReport, rec: WhisperRecommendation) -> list[dict[str, object]]:
    """The same preflight as :func:`_format_report`, as machine-readable records.

    One record per component, in the closed vocabulary defined by
    :mod:`jarvis.diagnostics.json_report`. The verdicts here are not invented
    for the JSON path — they are the ones the human report already makes:

      * ``ffmpeg`` missing is the one thing the text report calls out as
        needing action, so it is the one ``warn`` that is always meaningful.
      * ``ram`` uses the SAME 8 GB threshold as :func:`recommend_whisper`. Below
        it, local speech-to-text is not recommended and the box needs a cloud
        provider key to have a voice at all — worth a ``warn``, never a
        ``fail``, because Jarvis runs fine that way.

    Everything else is ``info``: a CPU name or an absent GPU is a fact about
    the host, not a defect, and a script must never fail an install over it.
    """
    from jarvis.diagnostics.json_report import record

    os_version = report.os_version.split()[0] if report.os_version else ""
    records: list[dict[str, object]] = [
        record("os", "info", f"{report.os_name} {os_version}".strip()),
        record(
            "python",
            "info",
            f"{report.python_version} ({report.python_executable})",
        ),
        record(
            "cpu",
            "info",
            f"{report.cpu_name} — {report.cpu_cores_physical} physical / "
            f"{report.cpu_cores_logical} logical cores",
        ),
    ]

    ram_ok = report.ram_total_mb >= 8192
    records.append(
        record(
            "ram",
            "ok" if ram_ok else "warn",
            f"{report.ram_total_mb} MB total, {report.ram_available_mb} MB available",
            None
            if ram_ok
            else "Under 8 GB — local speech-to-text is not recommended here. "
            "Configure a cloud provider key so voice input still works.",
        )
    )

    if report.gpus:
        for i, gpu in enumerate(report.gpus):
            detail = f"{gpu.name} ({gpu.vram_mb} MB VRAM)"
            if gpu.compute_capability:
                detail += f", compute capability {gpu.compute_capability}"
            records.append(record(f"gpu{i}", "ok", detail))
    else:
        records.append(record("gpu", "info", "no NVIDIA GPU detected — everything runs on CPU"))

    records.append(
        record(
            "cuda-runtime",
            "ok" if report.cuda_runtime else "info",
            report.cuda_runtime or "not installed",
        )
    )
    records.append(
        record(
            "torch-cuda",
            "ok" if report.torch_cuda_available else "info",
            "available" if report.torch_cuda_available else "not available",
        )
    )
    records.append(
        record(
            "ffmpeg",
            "ok" if report.ffmpeg_version else "warn",
            report.ffmpeg_version or "not found",
            None if report.ffmpeg_version else "Install ffmpeg and put it on PATH.",
        )
    )

    for pkg, version in sorted(report.existing_installs.items()):
        records.append(record(f"installed:{pkg}", "info", version))

    records.append(
        record(
            "speech-to-text",
            "info",
            f"{rec.provider} / {rec.model} on {rec.device} ({rec.compute_type}), "
            f"~{rec.expected_latency_ms} ms for 5 s of audio",
            rec.rationale,
        )
    )
    return records


def cuda_inference_verified() -> bool | None:
    """Cached GPU-inference verdict for this host (non-blocking), or ``None``.

    Thin, lazy indirection to ``jarvis.plugins.stt.wake_gpu_probe_cached`` so this
    setup-path module never imports the STT / ctranslate2 stack eagerly. Feeds
    ``recommend_whisper``'s capability gate (AP-21/AP-25): the verdict is the one a
    prior background probe already wrote to disk — never a fresh blocking probe on
    this report path (AP-26). Any import/read error → ``None`` (treated as
    unverified → CPU-first).
    """
    try:
        from jarvis.plugins.stt import wake_gpu_probe_cached

        return wake_gpu_probe_cached()
    except Exception:  # noqa: BLE001 — the recommender must stay CPU-first on any error
        return None


#: :func:`usable_accelerator_gb` shells out to ``nvidia-smi`` (or drives
#: pynvml) — 100–400 ms on a Windows box — and the "Local models" section asks
#: it from several panels per paint. A card does not change size while the app
#: runs, so one probe per minute is the honest cadence; the memo is process-wide
#: and thread-safe because the callers hop to a worker thread.
_ACCELERATOR_TTL_S = 60.0
_accelerator_lock = threading.Lock()
_accelerator_memo: tuple[float, tuple[float, str]] | None = None  # (monotonic, answer)


def _reset_for_tests() -> None:
    """Forget the memoised accelerator probe (tests only)."""
    global _accelerator_memo
    with _accelerator_lock:
        _accelerator_memo = None


def usable_accelerator_gb() -> tuple[float, str]:
    """Usable accelerator memory in GiB and where that figure came from.

    Memoised for :data:`_ACCELERATOR_TTL_S`; see :func:`_probe_accelerator_gb`
    for the probe itself.
    """
    global _accelerator_memo
    now = time.monotonic()
    with _accelerator_lock:
        hit = _accelerator_memo
        if hit is not None and now - hit[0] < _ACCELERATOR_TTL_S:
            return hit[1]
    answer = _probe_accelerator_gb()
    with _accelerator_lock:
        _accelerator_memo = (time.monotonic(), answer)
    return answer


#: ``total="15.9 GiB"`` out of Ollama's own startup line. The server prints one
#: ``inference compute`` record per device it will actually use, whatever the
#: vendor — ``library=CUDA`` / ``ROCm`` / ``Metal`` — so this one pattern reads
#: a card three separate vendor SDKs would be needed to read directly.
_OLLAMA_COMPUTE_RE = re.compile(
    r"inference compute.*?\btotal=\"?([0-9.]+)\s*([KMGT]i?B)\"?", re.IGNORECASE
)
#: Keyed by the unit's first letter, so ``GB`` and ``GiB`` both land on the
#: same factor. Ollama prints binary units either way.
_UNIT_GIB = {"k": 1 / 1048576, "m": 1 / 1024, "g": 1.0, "t": 1024.0}


def _gib(value: str, unit: str) -> float:
    """A ``("15.9", "GiB")`` pair as GiB; an unknown unit is 0 (= no answer)."""
    factor = _UNIT_GIB.get(unit.strip().lower()[:1], 0.0)
    if not factor:
        return 0.0
    try:
        return float(value) * factor
    except ValueError:
        return 0.0


def _ollama_log_paths() -> list[Path]:
    """Server logs that may carry the startup detection, newest intent first.

    Jarvis's own log exists only for a server Jarvis spawned; the vendor app
    keeps its own beside it, and BOTH rotate — the line is written once per
    start, so a long-running server's current log can be empty. That is why
    this is one source among several rather than the only one.
    """
    paths: list[Path] = []
    try:
        from jarvis.brain.ollama_runtime import _log_path  # noqa: PLC0415 — lazy (AP-26)

        paths.append(_log_path())
    except Exception:  # noqa: BLE001 — the runtime module is optional on a bare install
        log.debug("hardware: Jarvis's own Ollama log path is unavailable", exc_info=True)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            paths.append(Path(local) / "Ollama" / "server.log")
    else:
        # macOS and Linux both keep it here; the launchd/systemd unit writes
        # the same file the manual `ollama serve` does.
        paths.append(Path.home() / ".ollama" / "logs" / "server.log")
    return paths


def _ollama_reported_gb() -> float:
    """Largest device Ollama itself reported, in GiB; ``0.0`` when unknown.

    Authoritative where it answers: it is the budget the server decided on,
    not one this process guessed for it.
    """
    best = 0.0
    for path in _ollama_log_paths():
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover — unreadable or vanished mid-read
            continue
        for match in _OLLAMA_COMPUTE_RE.finditer(text):
            best = max(best, _gib(match.group(1), match.group(2)))
    return best


def _linux_drm_vram_gb() -> float:
    """Largest discrete card's VRAM from sysfs, in GiB; ``0.0`` when unknown.

    ``amdgpu`` and ``i915`` both publish the total in bytes, so one directory
    walk covers AMD and Intel discrete cards with no vendor tool installed.
    """
    best = 0
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        for leaf in ("device/mem_info_vram_total", "device/lmem_total_bytes"):
            try:
                raw = (card / leaf).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            try:
                best = max(best, int(raw))
            except ValueError:
                continue
    return best / (1024**3) if best else 0.0


def _windows_registry_vram_gb() -> float:
    """Largest adapter's memory from the display-class registry key, in GiB.

    ``qwMemorySize`` is the 64-bit field; the WMI ``AdapterRAM`` most code
    reaches for is a 32-bit one that reports 4 GB for every larger card.
    """
    try:
        import winreg  # noqa: PLC0415 — Windows-only, lazy by construction
    except ImportError:  # pragma: no cover — not Windows
        return 0.0
    root = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    best = 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as parent:
            for index in range(64):
                try:
                    name = winreg.EnumKey(parent, index)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(parent, name) as child:
                        value, _kind = winreg.QueryValueEx(
                            child, "HardwareInformation.qwMemorySize"
                        )
                except OSError:
                    continue
                if isinstance(value, int):
                    best = max(best, value)
    except OSError:  # pragma: no cover — a locked-down or unusual registry
        log.debug("hardware: display-class registry unreadable", exc_info=True)
        return 0.0
    return best / (1024**3) if best else 0.0


def _vendor_neutral_gb() -> tuple[float, str]:
    """``(gb, source)`` for a card ``nvidia-smi`` cannot see; ``(0.0, "")`` if none.

    Ordered by how much the answer can be trusted: Ollama's own startup record
    first (it names the budget the server will actually use, for CUDA, ROCm and
    Metal alike), then the OS's own inventory, which needs no running server
    and no vendor tool but only reports what the card HAS.
    """
    reported = _ollama_reported_gb()
    if reported > 0:
        return reported, "ollama-runtime"
    if os.name == "nt":
        registry = _windows_registry_vram_gb()
        if registry > 0:
            return registry, "windows-registry"
    elif sys.platform.startswith("linux"):
        drm = _linux_drm_vram_gb()
        if drm > 0:
            return drm, "linux-drm"
    return 0.0, ""


def _probe_accelerator_gb() -> tuple[float, str]:
    """The uncached accelerator probe behind :func:`usable_accelerator_gb`.

    ``(gb, source)`` with source ``"nvidia-smi"`` | ``"ollama-runtime"`` |
    ``"windows-registry"`` | ``"linux-drm"`` | ``"apple-unified"`` | ``"none"``.

    NVIDIA is asked first because it is the one reading verified live on real
    hardware. An AMD or Intel card comes next, through sources that do not need
    a vendor SDK: Ollama's own startup record, else the OS inventory. Apple
    Silicon shares its memory with the GPU, so total RAM is the figure — unless
    Ollama already named a smaller Metal budget, which it then wins with.

    ``0.0`` with source ``"none"`` still means "no accelerator memory I can
    vouch for" — a GPU-less box or a locked-down host — and callers must read
    it as *unknown accelerator*, never *no memory*: system RAM still runs the
    model, just slower, which is exactly what the fit verdict then says.

    The LARGEST single device, never the fleet sum: inference runs on one GPU,
    so two 8 GB cards are an 8 GB machine.

    Shared on purpose. The realtime-server preflight and the local-model
    recommender both have to answer "how much can this machine actually run?",
    and two probes drifting apart would show a user two different verdicts
    about one box.
    """
    try:
        gpus = _detect_nvidia_gpus()
    except Exception:  # noqa: BLE001 — nvidia-smi quirks must not crash a caller
        gpus = []
    vram_mb = max((g.vram_mb for g in gpus), default=0)
    if vram_mb > 0:
        return vram_mb / 1024.0, "nvidia-smi"
    try:
        vendor_gb, vendor_source = _vendor_neutral_gb()
    except Exception:  # noqa: BLE001 — a best-effort source must never break the probe
        log.debug("hardware: vendor-neutral accelerator probe failed", exc_info=True)
        vendor_gb, vendor_source = 0.0, ""
    if vendor_gb > 0:
        return vendor_gb, vendor_source
    if sys.platform == "darwin" and platform.machine() == "arm64":
        try:
            total_mb, _available = _detect_ram()
        except Exception:  # noqa: BLE001 — falls back to the "unknown accelerator" 0 above
            total_mb = 0
        if total_mb > 0:
            return total_mb / 1024.0, "apple-unified"
    return 0.0, "none"


def system_ram_gb() -> float | None:
    """Total system memory in GiB, or ``None`` when it cannot be read.

    ``None`` stays a real answer on a locked-down host: a caller then says
    "unknown" instead of inventing a number that would make a 14 GB model look
    safe on a 4 GB box.
    """
    try:
        total_mb, _available = _detect_ram()
    except Exception:  # noqa: BLE001 — locked-down host; None is the real answer, see above
        return None
    return round(total_mb / 1024.0, 1) if total_mb > 0 else None


def main(as_json: bool = False) -> int:
    """Render the hardware preflight.

    ``as_json`` swaps the box-drawing report for JSON Lines so an installer can
    gate on it. The exit code is 0 either way — this command reports, it does
    not judge — so existing callers keep working unchanged.
    """
    report = analyze()
    rec = recommend_whisper(report, gpu_inference_verified=cuda_inference_verified())
    if as_json:
        from jarvis.diagnostics.json_report import dumps

        print(dumps(check_records(report, rec)))
        return 0
    print(_format_report(report, rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
