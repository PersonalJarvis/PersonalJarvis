"""Hardware detection & Whisper model recommendation.

On first run, Jarvis analyses the local hardware and recommends the optimal
STT configuration (local Whisper model vs. cloud API) to the user.

All checks are read-only; nothing is installed or modified.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

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
    provider: str          # "faster-whisper" | "openai-api"
    model: str             # tiny | base | small | distil-small | distil-large-v3 | large-v3
    device: str            # cuda | cpu
    compute_type: str      # int8_float16 | fp16 | int8
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
    except Exception:  # noqa: BLE001
        pass

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
        "pvporcupine",
        "chromadb",
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


def recommend_whisper(report: HardwareReport) -> WhisperRecommendation:
    """Maps a HardwareReport to the recommended Whisper configuration.

    Heuristic:
    - NVIDIA GPU with >= 8 GB VRAM → distil-large-v3 (multilingual, fast)
    - NVIDIA GPU with 4-8 GB VRAM  → distil-small or small
    - NVIDIA GPU with < 4 GB VRAM  → tiny/base CPU or OpenAI API
    - No CUDA but plenty of RAM    → CPU faster-whisper tiny/base
    - Otherwise                    → OpenAI Whisper API
    """
    if not report.has_nvidia_gpu or not report.torch_cuda_available:
        if report.ram_total_mb >= 8192:
            return WhisperRecommendation(
                provider="faster-whisper",
                model="base",
                device="cpu",
                compute_type="int8",
                expected_latency_ms=1200,
                rationale=(
                    "Keine CUDA-fähige GPU erkannt. CPU-Mode mit 'base' liefert "
                    "akzeptable Qualität bei ~1s Latenz für 5s Audio. "
                    "Für bessere Latenz: OpenAI Whisper API als Fallback konfigurieren."
                ),
            )
        return WhisperRecommendation(
            provider="openai-api",
            model="whisper-1",
            device="cloud",
            compute_type="fp16",
            expected_latency_ms=400,
            rationale=(
                "Wenig lokale Ressourcen. OpenAI Whisper API empfohlen — "
                "API-Key wird im Setup-Wizard abgefragt."
            ),
        )

    vram = report.total_vram_mb
    if vram >= 8000:
        return WhisperRecommendation(
            provider="faster-whisper",
            model="distil-large-v3",
            device="cuda",
            compute_type="int8_float16",
            expected_latency_ms=250,
            rationale=(
                f"NVIDIA-GPU mit {vram} MB VRAM — läuft distil-large-v3 (multilingual DE+EN) "
                f"mit ~250ms Latenz. Optimum für lokale Privacy + niedrige Latenz."
            ),
        )
    if vram >= 4000:
        return WhisperRecommendation(
            provider="faster-whisper",
            model="distil-small",
            device="cuda",
            compute_type="int8_float16",
            expected_latency_ms=200,
            rationale=(
                f"NVIDIA-GPU mit {vram} MB VRAM — distil-small empfohlen "
                f"(~200ms Latenz, 97% Qualität). Für höhere Präzision später auf "
                f"distil-large-v3 umschaltbar wenn VRAM frei."
            ),
        )
    return WhisperRecommendation(
        provider="faster-whisper",
        model="base",
        device="cuda",
        compute_type="int8_float16",
        expected_latency_ms=180,
        rationale=(
            f"NVIDIA-GPU mit nur {vram} MB VRAM — 'base' Modell passt. "
            f"Qualität reicht für Deutsch/Englisch, Latenz ~180ms."
        ),
    )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _format_report(report: HardwareReport, rec: WhisperRecommendation) -> str:
    lines = [
        "╔══════════════════════════════════════════════════════════╗",
        "║  Jarvis Hardware-Analyse                                 ║",
        "╚══════════════════════════════════════════════════════════╝",
        "",
        f"OS:            {report.os_name} {report.os_version.split()[0] if report.os_version else ''}",
        f"Python:        {report.python_version} ({report.python_executable})",
        f"CPU:           {report.cpu_name}",
        f"               {report.cpu_cores_physical} phys / {report.cpu_cores_logical} log cores",
        f"RAM:           {report.ram_total_mb} MB total, {report.ram_available_mb} MB frei",
        "",
    ]
    if report.gpus:
        for i, gpu in enumerate(report.gpus):
            lines.append(f"GPU {i}:         {gpu.name} ({gpu.vram_mb} MB VRAM)")
            if gpu.compute_capability:
                lines.append(f"               Compute Capability: {gpu.compute_capability}")
    else:
        lines.append("GPU:           keine NVIDIA-GPU erkannt")

    lines.extend(
        [
            f"CUDA Runtime:  {report.cuda_runtime or 'nicht installiert'}",
            f"PyTorch CUDA:  {'✓ verfügbar' if report.torch_cuda_available else '✗ nicht verfügbar'}",
            f"ffmpeg:        {report.ffmpeg_version or 'NICHT gefunden — bitte installieren'}",
            "",
            "Vorinstallierte Packages:",
        ]
    )
    if report.existing_installs:
        for pkg, version in sorted(report.existing_installs.items()):
            lines.append(f"  - {pkg}: {version}")
    else:
        lines.append("  (keine relevanten Jarvis-Dependencies vorinstalliert)")

    lines.extend(
        [
            "",
            "╔══════════════════════════════════════════════════════════╗",
            "║  Whisper-Empfehlung                                      ║",
            "╚══════════════════════════════════════════════════════════╝",
            "",
            f"Provider:      {rec.provider}",
            f"Modell:        {rec.model}",
            f"Device:        {rec.device}",
            f"Compute-Type:  {rec.compute_type}",
            f"Latenz (erw.): ~{rec.expected_latency_ms}ms für 5s Audio",
            "",
            "Begründung:",
            f"  {rec.rationale}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    report = analyze()
    rec = recommend_whisper(report)
    print(_format_report(report, rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
