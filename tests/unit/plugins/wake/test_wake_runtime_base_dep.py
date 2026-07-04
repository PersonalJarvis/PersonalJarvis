"""The always-on neural wake runtime ships in the BASE install (regression guard).

This pins the fix for the "wake word only works on the maintainer's machine" bug
(2026-07-04). The pretrained wake models always shipped in every wheel, but the
runtime that RUNS them — ``openwakeword`` + CPU ``onnxruntime`` — used to live only
in the opt-in ``[local-voice]`` extra (which also drags the 1.5 GB torch/CUDA
stack). So a fresh download had a wake model and no engine to run it, and the wake
was silent on every non-maintainer machine.

These tests fail-closed against that recurring: the wake runtime must be a base
dependency (never demoted back into an extra), must stay torch-free, and the
provider must degrade to a logged no-op — not crash the speech pipeline — if the
runtime is ever genuinely absent.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from packaging.requirements import Requirement

from jarvis.plugins.wake.openwakeword_provider import OpenWakeWordProvider

REPO_ROOT = Path(__file__).resolve().parents[4]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _base_dep_names() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    return {Requirement(d).name.lower().replace("_", "-") for d in deps}


def _local_voice_names() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data.get("project", {}).get("optional-dependencies", {})
    return {
        Requirement(d).name.lower().replace("_", "-")
        for d in extras.get("local-voice", [])
    }


def test_wake_runtime_is_a_base_dependency() -> None:
    base = _base_dep_names()
    assert "openwakeword" in base, (
        "openwakeword must be a BASE dependency so the neural wake fires "
        "out-of-the-box on any machine — do NOT move it back into an extra."
    )
    assert "onnxruntime" in base, (
        "CPU onnxruntime must be a BASE dependency (it runs the wake ONNX graph). "
        "It is guard-legal — only onnxruntime-GPU is forbidden."
    )


def test_wake_runtime_is_not_hidden_in_the_local_voice_extra() -> None:
    # The whole bug was the runtime being reachable ONLY via [local-voice].
    assert "openwakeword" not in _local_voice_names(), (
        "openwakeword must NOT be in [local-voice] — that is exactly what made a "
        "fresh install unable to run its bundled wake model."
    )


@pytest.mark.skipif(
    importlib.util.find_spec("openwakeword") is None,
    reason="openwakeword not installed in this environment",
)
def test_wake_runtime_imports_no_torch() -> None:
    """Loading the wake runtime must not import torch (base stays torch-free).

    Run in a FRESH interpreter so a torch already imported by another test in
    this process cannot mask a regression. Exit 0 == torch absent.
    """
    script = (
        "import sys\n"
        "import openwakeword, onnxruntime\n"
        "assert 'torch' not in sys.modules, 'wake runtime imported torch'\n"
        "assert 'onnxruntime' in sys.modules, 'wake should use onnxruntime'\n"
        "print('OK')\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, (
        f"wake runtime imported torch or failed.\n"
        f"stdout={res.stdout}\nstderr={res.stderr}"
    )


def test_ensure_model_degrades_when_runtime_missing(monkeypatch) -> None:
    """A missing openWakeWord runtime must degrade, never raise.

    Setting ``sys.modules['openwakeword.model'] = None`` makes the lazy
    ``from openwakeword.model import Model`` raise ImportError — the exact failure
    a broken/partial install would hit. The provider must swallow it and flag the
    runtime unavailable instead of propagating the crash into the speech pipeline.
    """
    monkeypatch.setitem(sys.modules, "openwakeword.model", None)
    provider = OpenWakeWordProvider()

    provider._ensure_model()  # must NOT raise

    assert provider._runtime_unavailable is True
    assert provider._model is None


async def test_detect_is_a_clean_noop_when_runtime_unavailable() -> None:
    """detect() must end the stream cleanly (never crash) if the runtime is gone."""
    provider = OpenWakeWordProvider()
    provider._runtime_unavailable = True  # simulate the degraded state

    async def _chunks():
        # Deliberately never reached: detect() returns before consuming audio.
        if False:  # pragma: no cover
            yield None

    results = [kw async for kw in provider.detect(_chunks())]
    assert results == []
