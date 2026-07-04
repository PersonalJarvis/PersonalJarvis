"""Silero VAD runs torch-free (onnxruntime + numpy) on the voice-boot path.

The torch ``silero_vad`` package import was the dominant voice-boot cost
(``vad-load`` 6-16 s — ``import torch`` starved in the serve-first boot storm),
and it gated honest ``VoiceBootStatus(ready=True)``. ``SileroEndpointer`` now
runs the bundled ``silero_vad.onnx`` model directly via onnxruntime (already warm
from the wake model) with numpy-managed recurrent state, so the VAD load never
imports torch and drops to ~0.1 s.

These tests pin that contract: (1) the VAD path imports NO torch (checked in a
fresh subprocess so an unrelated earlier import can't mask a regression), and
(2) per-frame probabilities are well-formed (in [0, 1], silence reads low). The
bit-for-bit match against the torch model is verified out-of-band (it would pull
the heavy torch import into the test process); here we guard the boot-cost win.
"""
from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest

from jarvis.audio.vad import SileroEndpointer


def _bare_endpointer() -> SileroEndpointer:
    """A SileroEndpointer with only the fields ``_prob`` touches (no __init__)."""
    ep = SileroEndpointer.__new__(SileroEndpointer)
    ep._session = None
    ep._vad_state = None
    ep._vad_context = None
    ep._energy_only = False  # Silero present here → real ONNX prob path, not the floor
    return ep


def _silero_onnx_available() -> bool:
    import importlib.util

    spec = importlib.util.find_spec("silero_vad")
    if spec is None or spec.origin is None:
        return False
    import os

    return os.path.exists(
        os.path.join(os.path.dirname(spec.origin), "data", "silero_vad.onnx")
    )


pytestmark = pytest.mark.skipif(
    not _silero_onnx_available(), reason="silero_vad ONNX model not installed"
)


def test_prob_is_well_formed_and_silence_reads_low() -> None:
    ep = _bare_endpointer()
    # Silence must score low; a couple of frames to let the RNN settle.
    probs = [ep._prob(np.zeros(512, dtype=np.float32)) for _ in range(5)]
    for p in probs:
        assert 0.0 <= p <= 1.0
    assert max(probs) < 0.3, f"silence should read low, got {probs}"


def test_prob_accepts_exactly_512_float_samples() -> None:
    ep = _bare_endpointer()
    p = ep._prob(np.linspace(-0.1, 0.1, 512).astype(np.float32))
    assert 0.0 <= p <= 1.0


def test_vad_path_imports_no_torch() -> None:
    """The whole point of the change: exercising the VAD must not import torch.

    Run in a FRESH interpreter so a torch already imported by another test in
    this process cannot mask a regression. Exit code 0 == torch absent.
    """
    script = (
        "import sys, numpy as np;\n"
        "from jarvis.audio.vad import SileroEndpointer;\n"
        "ep = SileroEndpointer.__new__(SileroEndpointer);\n"
        "ep._session = None; ep._vad_state = None; ep._vad_context = None;\n"
        "ep._energy_only = False;\n"
        "ep._prob(np.zeros(512, dtype=np.float32));\n"
        "assert 'torch' not in sys.modules, 'VAD path imported torch';\n"
        "assert 'onnxruntime' in sys.modules, 'VAD should use onnxruntime';\n"
        "print('OK')\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, (
        f"VAD imported torch or failed.\nstdout={res.stdout}\nstderr={res.stderr}"
    )
