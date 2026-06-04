"""Smoke-Test: ``python -m overlay --self-test`` exit 0 mit ``OK ...``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
OS_LEVEL_SRC = REPO_ROOT / "OS-Level" / "src"


def test_self_test_exits_zero_with_ok() -> None:
    env_pythonpath = str(OS_LEVEL_SRC)
    result = subprocess.run(
        [sys.executable, "-m", "overlay", "--self-test"],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **_clean_env(),
            "PYTHONPATH": env_pythonpath,
        },
    )
    assert result.returncode == 0, (
        f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert result.stdout.startswith("OK"), result.stdout


def _clean_env() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items()}
