"""Describe the machine a bench run happened on, including how busy it was."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from typing import Any

# Same flag as jarvis.core.process_utils.NO_WINDOW_CREATIONFLAGS (AP-1); the
# engine package cannot import jarvis.* because it runs in its own environment.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run(argv: list[str], timeout: float = 10.0) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # A missing or hung probe tool means "unknown", which the report shows.
        return ""
    return completed.stdout.strip()


def cpu_name() -> str:
    if sys.platform == "darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
    if sys.platform == "win32":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"])
        return out or platform.processor()
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        # Not every Linux exposes cpuinfo (some containers); fall through.
        pass
    return platform.processor() or platform.machine()


def gpus() -> list[dict[str, str]]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader,nounits"])
    rows = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4:
            rows.append({"name": parts[0], "memory_total_mib": parts[1],
                         "memory_used_mib": parts[2], "driver": parts[3]})
    return rows


def load_snapshot() -> dict[str, Any]:
    try:
        import psutil  # noqa: PLC0415 - optional

        return {
            "cpu_percent": psutil.cpu_percent(interval=1.0),
            "ram_available_gb": round(psutil.virtual_memory().available / 1e9, 1),
        }
    except ImportError:  # optional module; the report simply omits it
        return {}


def describe() -> dict[str, Any]:
    info: dict[str, Any] = {
        "os": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu": cpu_name(),
        "cpu_count": os.cpu_count(),
        "gpus": gpus(),
    }
    try:
        import psutil  # noqa: PLC0415 - optional

        info["ram_total_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except ImportError:
        # psutil is optional; RAM is simply not reported without it.
        pass
    for module in ("onnxruntime", "sherpa_onnx", "torch", "pocket_tts", "mlx", "soxr", "numpy"):
        try:
            imported = __import__(module)
            info[f"version_{module}"] = getattr(imported, "__version__", "?")
        except ImportError:  # optional module; the report records it as absent
            info[f"version_{module}"] = None
    info["load"] = load_snapshot()
    return info
