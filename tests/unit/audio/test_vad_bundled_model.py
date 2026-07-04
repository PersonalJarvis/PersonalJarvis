"""The Silero VAD model ships in the BASE install (regression guard).

This pins the fix for the "voice only works on the maintainer's machine" bug
(2026-07-04). End-of-speech detection (``jarvis/audio/vad.py``) runs the Silero
ONNX model torch-free via base ``onnxruntime`` — but the model FILE used to be
reachable only inside the ``silero-vad`` pip package, which lives in the opt-in
``[local-voice]`` extra. So on a fresh base install the wake word fired, then the
first voice frame raised ``RuntimeError('silero_vad package not installed')`` and
the utterance was never captured: Jarvis kept "listening" and never advanced.

The model is now bundled as an ~2.2 MB MIT asset under ``jarvis/assets/vad/`` and
loaded from there first (the ``silero-vad`` package is only a fallback). These
tests fail-closed against that recurring: the asset must ship, the loader must use
it without the package, and the (heavier, torch-pulling) package must stay an extra.
"""
from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

from jarvis.assets import bundled_silero_vad_model
from jarvis.audio.vad import SileroEndpointer

REPO_ROOT = Path(__file__).resolve().parents[3]
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


def test_silero_vad_package_stays_in_local_voice_not_base() -> None:
    """The MODEL is base; the torch-pulling PACKAGE stays an opt-in extra.

    Bundling the model is exactly what lets the ``silero-vad`` package (which drags
    torch) stay out of the base install without breaking the default VAD.
    """
    base = _base_dep_names()
    assert "silero-vad" not in base, (
        "silero-vad pulls torch and must NOT be a base dependency — the model is "
        "bundled instead (jarvis/assets/vad/), so base needs only onnxruntime."
    )
    assert "silero-vad" in _local_voice_names()


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
                "reached — end-of-speech detection would break on a base install"
            )
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _guard)

    ep = SileroEndpointer()
    ep._ensure_model()  # must NOT raise and must NOT consult the package

    assert ep._session is not None
