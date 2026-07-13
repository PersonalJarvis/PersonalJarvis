"""The Silero VAD model ships in the BASE install (regression guard).

This pins the fix for the "voice only works on the maintainer's machine" bug
(2026-07-04). End-of-speech detection (``jarvis/audio/vad.py``) runs the Silero
ONNX model torch-free via base ``onnxruntime`` — but the model file used to be
reachable only inside the ``silero-vad`` pip package. So on a fresh base install
the wake word fired, then the
first voice frame raised ``RuntimeError('silero_vad package not installed')`` and
the utterance was never captured: Jarvis kept "listening" and never advanced.

The model is now bundled as an ~2.2 MB MIT asset under ``jarvis/assets/vad/`` and
loaded from there directly. These tests fail-closed against that recurrence: the
asset must ship in wheels and frozen builds, the loader must use it without the
package, and the redundant torch-pulling package must stay absent from every
installation profile.
"""
from __future__ import annotations

import importlib.util
import runpy
import sys
import tomllib
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from packaging.requirements import Requirement

from jarvis.assets import bundled_silero_vad_model
from jarvis.audio.vad import SileroEndpointer
from jarvis.core.protocols import AudioChunk

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYINSTALLER_SPEC = REPO_ROOT / "jarvis.spec"
UV_LOCK = REPO_ROOT / "uv.lock"


def _base_dep_names() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    return {Requirement(d).name.lower().replace("_", "-") for d in deps}


def _optional_dep_names() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data.get("project", {}).get("optional-dependencies", {})
    return {
        Requirement(d).name.lower().replace("_", "-")
        for dependencies in extras.values()
        for d in dependencies
    }


def test_silero_vad_model_is_bundled() -> None:
    path = bundled_silero_vad_model()
    assert path is not None, (
        "The Silero VAD model must be bundled under jarvis/assets/vad/ so "
        "end-of-speech detection works on a fresh base install. Without it the "
        "voice loop hears the wake word but never closes the utterance."
    )
    assert path.is_file()
    assert path.suffix == ".onnx"
    assert path.stat().st_size > 1_000_000, "the Silero ONNX model looks truncated"


def test_wheel_package_data_includes_bundled_assets() -> None:
    """Setuptools must carry the model from the source tree into every wheel."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    patterns = data.get("tool", {}).get("setuptools", {}).get("package-data", {})
    assert "assets/**/*" in patterns.get("jarvis", [])


def test_silero_vad_package_is_absent_from_every_install_profile() -> None:
    """The ONNX model is bundled; its torch-pulling package is unnecessary."""
    base = _base_dep_names()
    assert "silero-vad" not in base, (
        "silero-vad pulls torch and must NOT be a base dependency; the model is "
        "bundled instead (jarvis/assets/vad/), so base needs only onnxruntime."
    )
    assert "onnxruntime" in base
    assert "silero-vad" not in _optional_dep_names(), (
        "silero-vad is redundant and must not re-enter local-voice, full, or dev"
    )


def test_resolved_dependency_set_contains_no_silero_or_torch() -> None:
    """The lock must prove that no optional profile resolves the removed stack."""
    data = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    names = {
        package["name"].lower().replace("_", "-")
        for package in data.get("package", [])
    }
    assert names.isdisjoint({"silero-vad", "torch", "torchaudio", "torchvision"})


def test_pyinstaller_spec_collects_bundled_vad_asset(monkeypatch) -> None:
    """The frozen desktop build must preserve the package-relative VAD path."""
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = lambda _package: []
    hooks.collect_submodules = lambda _package: []
    hooks.copy_metadata = lambda _distribution: []
    utils = ModuleType("PyInstaller.utils")
    utils.hooks = hooks
    pyinstaller = ModuleType("PyInstaller")
    pyinstaller.utils = utils
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils", utils)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)

    class _Analysis:
        def __init__(self, *_args, **_kwargs) -> None:
            self.pure = []
            self.zipped_data = []
            self.scripts = []
            self.binaries = []
            self.zipfiles = []
            self.datas = []

    globals_after_run = runpy.run_path(
        str(PYINSTALLER_SPEC),
        init_globals={
            "SPECPATH": str(REPO_ROOT),
            "Analysis": _Analysis,
            "PYZ": lambda *_args, **_kwargs: SimpleNamespace(),
            "EXE": lambda *_args, **_kwargs: SimpleNamespace(),
            "COLLECT": lambda *_args, **_kwargs: SimpleNamespace(),
        },
    )
    bundled = bundled_silero_vad_model()
    assert bundled is not None
    expected = (str(bundled), str(Path("jarvis/assets/vad")))
    assert expected in globals_after_run["datas"]


def test_ensure_model_loads_from_bundle_without_the_package(monkeypatch) -> None:
    """``_ensure_model`` must load the bundled asset and never touch the package.

    We make ``importlib.util.find_spec('silero_vad')`` explode: if the loader falls
    through to the package path (the old behaviour) the test fails, proving the
    bundled asset is used. ``onnxruntime`` is a base dependency, so the real
    inference session is built here — no mocking of the runtime.
    """
    original_find_spec = importlib.util.find_spec

    def _guard(name: str, *args, **kwargs):
        if name == "silero_vad":
            raise AssertionError(
                "the bundled asset must be used; the silero_vad PACKAGE path was "
                "reached; end-of-speech detection would break on a base install"
            )
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _guard)

    ep = SileroEndpointer()
    ep._ensure_model()  # must NOT raise and must NOT consult the package

    assert ep._session is not None


def test_missing_onnxruntime_uses_portable_energy_endpointing(monkeypatch) -> None:
    """An unsupported native runtime must not disable voice capture."""
    monkeypatch.setitem(sys.modules, "onnxruntime", None)

    ep = SileroEndpointer(min_speech_rms=0.002)
    ep._ensure_model()

    assert ep._session is None
    assert ep._energy_only is True
    assert ep._prob(np.zeros(512, dtype=np.float32)) == 0.0
    assert ep._prob(np.full(512, 0.01, dtype=np.float32)) == 1.0


def test_failed_native_inference_switches_to_energy_endpointing() -> None:
    """A runtime that loads but fails later must degrade in the same turn."""

    class _BrokenSession:
        def run(self, *_args, **_kwargs):
            raise RuntimeError("unsupported execution provider")

    ep = SileroEndpointer(min_speech_rms=0.002)
    ep._session = _BrokenSession()
    ep._vad_state = np.zeros((2, 1, 128), dtype=np.float32)
    ep._vad_context = np.zeros((1, 64), dtype=np.float32)

    assert ep._prob(np.full(512, 0.01, dtype=np.float32)) == 1.0
    assert ep._energy_only is True
    assert ep._session is None


@pytest.mark.asyncio
async def test_energy_fallback_captures_and_ends_an_utterance(monkeypatch) -> None:
    """The fallback must drive the complete endpoint state machine."""
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    ep = SileroEndpointer(
        silence_ms=96,
        min_speech_ms=64,
        min_speech_rms=0.002,
    )

    loud = (np.full(512, 0.02) * 32767.0).astype(np.int16).tobytes()
    quiet = np.zeros(512, dtype=np.int16).tobytes()

    async def chunks():
        for index, pcm in enumerate([loud] * 4 + [quiet] * 4):
            yield AudioChunk(
                pcm=pcm,
                sample_rate=16_000,
                timestamp_ns=index,
                channels=1,
            )

    utterances = [utterance async for utterance in ep.utterances(chunks())]

    assert len(utterances) == 1
    assert utterances[0]
    assert ep._energy_only is True
