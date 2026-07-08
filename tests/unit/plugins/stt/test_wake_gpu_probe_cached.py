"""``wake_gpu_probe_cached`` — the NON-blocking reader of the GPU-inference verdict.

Companion to :func:`_wake_gpu_inference_verified` (which BLOCKS on a cache miss to
run one real turbo/cuda inference in a subprocess). Off-critical-path callers such
as the first-run hardware recommender (``jarvis.hardware.detection``) must gate a
GPU recommendation on a REAL, verified inference (AP-21/AP-25) WITHOUT ever paying
the blocking probe. This reader delivers exactly that:

- a cached verdict for the CURRENTLY installed ctranslate2 version -> True / False;
- no cache file, or a verdict left over from a DIFFERENT ctranslate2 version
  (a runtime upgrade may fix or re-introduce the hang) -> ``None`` (unknown);
- it NEVER launches the probe subprocess.
"""
from __future__ import annotations

import json

import pytest

import jarvis.plugins.stt as stt_pkg
from jarvis.plugins.stt import wake_gpu_probe_cached


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    return tmp_path


def _write_cache(tmp_path, *, ok: bool, ct2: str) -> None:
    (tmp_path / "wake_gpu_probe.json").write_text(
        json.dumps({"ok": ok, "ctranslate2": ct2}), encoding="utf-8"
    )


def _pin_version(monkeypatch, version: str) -> None:
    monkeypatch.setattr(stt_pkg, "_ctranslate2_version", lambda: version)


def _forbid_subprocess(monkeypatch) -> None:
    def _boom() -> bool:
        raise AssertionError("the cached reader must never launch the probe")

    monkeypatch.setattr(stt_pkg, "_run_wake_gpu_probe_subprocess", _boom)


def test_no_cache_file_returns_none(tmp_path, monkeypatch) -> None:
    _pin_version(monkeypatch, "4.7.1")
    _forbid_subprocess(monkeypatch)
    assert wake_gpu_probe_cached() is None


def test_verified_verdict_for_current_version_returns_true(tmp_path, monkeypatch) -> None:
    _pin_version(monkeypatch, "4.7.1")
    _write_cache(tmp_path, ok=True, ct2="4.7.1")
    _forbid_subprocess(monkeypatch)
    assert wake_gpu_probe_cached() is True


def test_failed_verdict_for_current_version_returns_false(tmp_path, monkeypatch) -> None:
    _pin_version(monkeypatch, "4.7.1")
    _write_cache(tmp_path, ok=False, ct2="4.7.1")
    _forbid_subprocess(monkeypatch)
    assert wake_gpu_probe_cached() is False


def test_stale_version_verdict_is_treated_as_unknown(tmp_path, monkeypatch) -> None:
    # A verdict written under a DIFFERENT ctranslate2 version must not be trusted:
    # a runtime upgrade can fix or re-introduce the AP-25 hang.
    _pin_version(monkeypatch, "4.8.0")
    _write_cache(tmp_path, ok=True, ct2="4.7.1")
    _forbid_subprocess(monkeypatch)
    assert wake_gpu_probe_cached() is None


def test_corrupt_cache_returns_none(tmp_path, monkeypatch) -> None:
    _pin_version(monkeypatch, "4.7.1")
    (tmp_path / "wake_gpu_probe.json").write_text("{ not json", encoding="utf-8")
    _forbid_subprocess(monkeypatch)
    assert wake_gpu_probe_cached() is None
