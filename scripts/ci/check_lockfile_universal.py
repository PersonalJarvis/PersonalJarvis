#!/usr/bin/env python3
"""Fail-closed guard: the compiled requirements.txt lockfile must stay universal.

Companion to ``check_requirements_sync.py``. That guard proves the *source*
(``requirements.in``) mirrors ``pyproject.toml [project].dependencies``. This one
inspects the *output* — the committed, hash-pinned ``requirements.txt`` lockfile
itself — and closes the last gap in the shipped-broken-installer bug class
(AP-23): a lockfile regenerated the wrong way silently re-baking the maintainer's
GPU/CUDA + single-OS wheels into the file every downloader installs with
``pip install --require-hashes -r requirements.txt``.

Two independent checks, both fail-closed:

1. UNIVERSAL MARKER — the lockfile header must record that it was compiled with
   ``uv pip compile --universal`` (per-OS environment markers so ONE lockfile
   installs on Windows/macOS/Linux). A lockfile compiled without ``--universal``
   (e.g. plain ``pip-compile`` on the maintainer's Windows box) pins that one
   platform's wheels and breaks everyone else.

2. NO GPU / CUDA / TORCH WHEELS — no ``nvidia-*``, ``torch*``, CUDA runtime
   (``cudnn``/``cublas``/``cufile``/…), ``ctranslate2``/``faster-whisper``,
   ``onnxruntime-gpu``, or other GPU-only stacks may appear in the base lockfile.
   These belong exclusively in the opt-in ``[local-voice]`` extra and must never
   enter the file a plain ``pip install`` resolves (``nvidia-cufile`` does not
   even exist on plain PyPI — it is unresolvable on any non-maintainer machine).

Exit 0 when the lockfile is universal and GPU-free (or absent — a source-only
checkout has nothing to guard); exit 1 with the offending lines otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_TXT = REPO_ROOT / "requirements.txt"

# Exact package names that must never appear in the base lockfile.
_FORBIDDEN_EXACT = frozenset(
    {
        "torch",
        "torchvision",
        "torchaudio",
        "ctranslate2",
        "faster-whisper",
        "onnxruntime-gpu",
        "tensorrt",
        "tensorrt-libs",
        "xformers",
        "bitsandbytes",
        "triton",
        "pytorch-triton",
        "deepspeed",
        "flash-attn",
        "apex",
    }
)

# Package-name prefixes that must never appear (covers the whole NVIDIA CUDA
# runtime wheel family: nvidia-cufile, nvidia-cublas-cu12, cudnn-cu12, …).
_FORBIDDEN_PREFIXES = (
    "nvidia-",
    "cuda-",
    "cudnn",
    "cublas",
    "cufft",
    "cufile",
    "curand",
    "cusolver",
    "cusparse",
    "cusparselt",
    "nccl",
    "nvjitlink",
    "cupy",
    "tensorrt-",
)

# A CUDA-pinned local version tag anywhere on a line (e.g. ``2.1.0+cu121``).
_CUDA_LOCAL_TAG = re.compile(r"\+cu\d", re.IGNORECASE)

# Where the package name ends on a requirements line.
_NAME_END = re.compile(r"[=<>!~;\[ ]")


def _package_name(line: str) -> str | None:
    """Return the normalized package name a requirement line declares, else None.

    Continuation lines (``    --hash=...``, ``    # via ...``), blank lines and
    comments are not package declarations and return None.
    """
    if not line or line[0].isspace():
        return None
    stripped = line.strip()
    if stripped.startswith("#") or stripped.startswith("-"):
        return None
    token = _NAME_END.split(stripped, 1)[0]
    if not token:
        return None
    return token.lower().replace("_", "-")


def _is_forbidden(name: str) -> bool:
    if name in _FORBIDDEN_EXACT:
        return True
    return any(name.startswith(p) for p in _FORBIDDEN_PREFIXES)


def main() -> int:
    if not REQUIREMENTS_TXT.exists():
        print(f"SKIP: {REQUIREMENTS_TXT.name} not present; nothing to guard.")
        return 0

    text = REQUIREMENTS_TXT.read_text(encoding="utf-8")
    lines = text.splitlines()

    problems: list[str] = []

    # Check 1 — universal marker in the header comment.
    header = "\n".join(line for line in lines[:12] if line.lstrip().startswith("#"))
    if "--universal" not in header:
        problems.append(
            "  MISSING universal marker: the lockfile header does not record a\n"
            "  `uv pip compile --universal` invocation. Regenerate it with:\n"
            "    uv pip compile --universal --generate-hashes "
            "--python-version 3.11 --output-file=requirements.txt requirements.in"
        )

    # Check 2 — no GPU / CUDA / torch wheels.
    for n, raw in enumerate(lines, start=1):
        name = _package_name(raw)
        if name and _is_forbidden(name):
            problems.append(f"  line {n}: GPU/CUDA/torch wheel in base lockfile -> {raw.strip()}")
        elif _CUDA_LOCAL_TAG.search(raw):
            problems.append(f"  line {n}: CUDA-pinned local version tag -> {raw.strip()}")

    if not problems:
        print("OK: requirements.txt is platform-universal and free of GPU/CUDA/torch wheels.")
        return 0

    print("FAIL: requirements.txt is not safe for an arbitrary, no-GPU downloader.")
    print("      The base lockfile must be `--universal` and GPU-free (CLAUDE.md §3, AP-23).")
    print("      GPU/local-voice wheels belong only in the opt-in [local-voice] extra.")
    print()
    for p in problems:
        print(p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
