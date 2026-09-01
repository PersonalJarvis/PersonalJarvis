"""Contract of the free-accelerator-memory capability.

Every consumer (today: the local-realtime spawn gate) relies on exactly this
shape: a non-negative ``(gb, source)`` tuple, ``(0.0, "none")`` for *unknown*
— never a raise, never a negative, never an invented number on a host with no
NVIDIA tooling (Apple unified memory, ROCm, locked-down boxes). The spawn
gate's own contract: a provable shortage refuses, an UNKNOWN reading never
does (AP-22 — refusing on a number nobody can read bricks every non-NVIDIA
box).
"""

from __future__ import annotations

import builtins

import pytest

from jarvis.hardware import detection
from jarvis.realtime.local_server import supervisor

_ALLOWED_SOURCES = {"nvml", "nvidia-smi", "none"}


def test_probe_answers_the_contract_shape_on_this_host() -> None:
    free_gb, source = detection.free_accelerator_gb()
    assert isinstance(free_gb, float)
    assert free_gb >= 0.0
    assert source in _ALLOWED_SOURCES
    if source == "none":
        assert free_gb == 0.0


def test_probe_reports_unknown_not_an_error_without_nvidia_tooling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def _no_pynvml(name: str, *args: object, **kwargs: object) -> object:
        if name == "pynvml":
            raise ImportError("no pynvml on this host")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _no_pynvml)
    monkeypatch.setattr(detection, "_run", lambda _cmd: "")

    assert detection.free_accelerator_gb() == (0.0, "none")


def test_probe_parses_the_nvidia_smi_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def _no_pynvml(name: str, *args: object, **kwargs: object) -> object:
        if name == "pynvml":
            raise ImportError("no pynvml")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _no_pynvml)
    # Two cards: the largest single device wins, matching the total probe.
    monkeypatch.setattr(detection, "_run", lambda _cmd: "1024\n8192\n")

    assert detection.free_accelerator_gb() == (8.0, "nvidia-smi")


class TestSpawnGateContract:
    def test_a_provable_shortage_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(detection, "free_accelerator_gb", lambda: (1.3, "nvml"))
        assert supervisor._accelerator_memory_refusal() == "refused:accelerator-memory"  # noqa: SLF001

    def test_enough_free_memory_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            detection,
            "free_accelerator_gb",
            lambda: (supervisor.VOICE_STACK_RESERVE_GB + 0.1, "nvml"),
        )
        assert supervisor._accelerator_memory_refusal() == ""  # noqa: SLF001

    def test_unknown_free_memory_never_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(detection, "free_accelerator_gb", lambda: (0.0, "none"))
        assert supervisor._accelerator_memory_refusal() == ""  # noqa: SLF001

    def test_a_broken_probe_never_vetoes_the_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom() -> tuple[float, str]:
            raise RuntimeError("driver went away")

        monkeypatch.setattr(detection, "free_accelerator_gb", _boom)
        assert supervisor._accelerator_memory_refusal() == ""  # noqa: SLF001

    def test_the_reserve_now_covers_the_measured_tts_footprint(self) -> None:
        # BUG-204: Qwen3-TTS alone holds ~5.4 GB; a reserve below that never
        # actually reserved anything.
        assert supervisor.VOICE_STACK_RESERVE_GB >= 5.4
