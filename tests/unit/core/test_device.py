"""CPU-first device-selection policy (``jarvis.core.device``).

Pins the cloud-first contract of ADR-0024: the default is always CPU, a GPU is
adopted only on an EXPLICIT request with a VERIFIED capability, and a known-bad
GPU degrades to CPU with ``fell_back=True``. The capability verdict is injected
(``cuda_usable``), so these tests never touch torch / ctranslate2.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from jarvis.core.device import CPU, DeviceResolution, resolve_device


@pytest.mark.parametrize("value", ["cpu", "CPU", " cpu "])
def test_explicit_cpu_stays_cpu(value: str) -> None:
    res = resolve_device(value)
    assert res.device == CPU
    assert res.fell_back is False


@pytest.mark.parametrize("value", ["", "auto", None])
def test_auto_and_empty_default_to_cpu(value: str | None) -> None:
    # auto / empty / None with no verified GPU -> cloud-first CPU floor.
    assert resolve_device(value).device == CPU
    assert resolve_device(value, cuda_usable=False).device == CPU


def test_auto_opts_up_to_cuda_only_when_verified() -> None:
    res = resolve_device("auto", cuda_usable=True)
    assert res.device == "cuda"
    assert res.fell_back is False


def test_explicit_gpu_honored_when_verified() -> None:
    res = resolve_device("cuda", cuda_usable=True)
    assert res.device == "cuda"
    assert res.fell_back is False


def test_explicit_gpu_falls_back_when_known_unusable() -> None:
    res = resolve_device("cuda", cuda_usable=False)
    assert res.device == CPU
    assert res.fell_back is True
    assert "CPU" in res.reason


def test_explicit_gpu_honored_when_capability_unknown() -> None:
    # None = not pre-verified: honor the explicit opt-in, let the backend
    # self-heal. This preserves a power user's `device = "cuda"` in jarvis.toml.
    res = resolve_device("cuda", cuda_usable=None)
    assert res.device == "cuda"
    assert res.fell_back is False


def test_indexed_cuda_spec_is_preserved() -> None:
    res = resolve_device("cuda:1", cuda_usable=True)
    assert res.device == "cuda:1"


def test_indexed_gpu_falls_back_to_plain_cpu_when_unusable() -> None:
    res = resolve_device("cuda:0", cuda_usable=False)
    assert res.device == CPU
    assert res.fell_back is True


@pytest.mark.parametrize("value", ["mps", "xpu", "0", "banana"])
def test_unrecognized_device_fails_closed_to_cpu(value: str) -> None:
    # Anything the policy does not recognize resolves to the safe device, never
    # a silent GPU escalation.
    res = resolve_device(value, cuda_usable=True)
    assert res.device == CPU
    assert res.fell_back is False


def test_gpu_alias_canonicalizes_to_cuda() -> None:
    # The "gpu" alias is normalized to the backend-valid "cuda" spec when honored.
    assert resolve_device("gpu", cuda_usable=True).device == "cuda"
    assert resolve_device("gpu:1", cuda_usable=True).device == "cuda:1"
    assert resolve_device("gpu", cuda_usable=False).device == CPU


def test_result_is_a_frozen_resolution() -> None:
    res = resolve_device("cpu")
    assert isinstance(res, DeviceResolution)
    with pytest.raises(FrozenInstanceError):
        res.device = "cuda"  # type: ignore[misc]  # frozen dataclass
