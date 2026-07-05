"""The GPU wake-inference probe: verdicts, caching, and the marker contract.

Why this exists (AP-25, re-measured 2026-07-05): CUDA *presence* and CUDA
*usability* diverged on a Blackwell host — ``get_cuda_device_count() > 0``
while every CTranslate2 inference hung under the then-current runtime. The
probe runs ONE real turbo/cuda inference in a killable subprocess and caches
the verdict per ctranslate2 version, so a runtime upgrade (which may fix or
re-introduce the hang) triggers exactly one re-probe.

Two hard contracts pinned here:
- Success is the stdout MARKER, never the exit code — a CUDA process can die
  in native teardown (observed exit 127 on 2026-07-05) after doing all its
  work correctly.
- A hang (subprocess timeout) persists ``ok: false`` so no later build ever
  re-runs the hanging inference in-process.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import jarvis.plugins.stt as stt_pkg
from jarvis.plugins.stt import (
    _run_wake_gpu_probe_subprocess,
    _wake_gpu_inference_verified,
    _wake_gpu_probe_cache_path,
    mark_wake_gpu_bad,
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Point the probe cache at a temp dir and clear the in-process memo."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    _wake_gpu_inference_verified.cache_clear()
    yield tmp_path
    _wake_gpu_inference_verified.cache_clear()


def _write_cache(tmp_path, *, ok: bool, ct2: str) -> None:
    (tmp_path / "wake_gpu_probe.json").write_text(
        json.dumps({"ok": ok, "ctranslate2": ct2}), encoding="utf-8"
    )


def _pin_version(monkeypatch, version: str) -> None:
    monkeypatch.setattr(stt_pkg, "_ctranslate2_version", lambda: version)


def _forbid_subprocess(monkeypatch) -> None:
    def _boom() -> bool:
        raise AssertionError("cache hit must not launch the probe subprocess")

    monkeypatch.setattr(stt_pkg, "_run_wake_gpu_probe_subprocess", _boom)


def test_cache_path_honours_data_dir_env(tmp_path) -> None:
    assert _wake_gpu_probe_cache_path() == tmp_path / "wake_gpu_probe.json"


def test_cache_hit_ok_true_skips_the_subprocess(tmp_path, monkeypatch) -> None:
    _pin_version(monkeypatch, "4.7.1")
    _write_cache(tmp_path, ok=True, ct2="4.7.1")
    _forbid_subprocess(monkeypatch)
    assert _wake_gpu_inference_verified() is True


def test_cache_hit_ok_false_skips_the_subprocess(tmp_path, monkeypatch) -> None:
    _pin_version(monkeypatch, "4.7.1")
    _write_cache(tmp_path, ok=False, ct2="4.7.1")
    _forbid_subprocess(monkeypatch)
    assert _wake_gpu_inference_verified() is False


def test_version_mismatch_reprobes_and_rewrites_the_cache(
    tmp_path, monkeypatch
) -> None:
    # A cached "unusable" verdict from an OLD runtime must not outlive a
    # ctranslate2 upgrade — exactly the AP-25 situation this session resolved.
    _pin_version(monkeypatch, "4.8.0")
    _write_cache(tmp_path, ok=False, ct2="4.7.1")
    monkeypatch.setattr(stt_pkg, "_run_wake_gpu_probe_subprocess", lambda: True)

    assert _wake_gpu_inference_verified() is True
    cached = json.loads(
        (tmp_path / "wake_gpu_probe.json").read_text(encoding="utf-8")
    )
    assert cached == {"ok": True, "ctranslate2": "4.8.0", "model": "large-v3-turbo"}


def test_corrupt_cache_reprobes(tmp_path, monkeypatch) -> None:
    _pin_version(monkeypatch, "4.7.1")
    (tmp_path / "wake_gpu_probe.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(stt_pkg, "_run_wake_gpu_probe_subprocess", lambda: False)

    assert _wake_gpu_inference_verified() is False
    cached = json.loads(
        (tmp_path / "wake_gpu_probe.json").read_text(encoding="utf-8")
    )
    assert cached["ok"] is False


def test_failed_probe_persists_ok_false(tmp_path, monkeypatch) -> None:
    # A hang/failure verdict must survive restarts: the next build reads the
    # cache and never re-runs the hanging inference.
    _pin_version(monkeypatch, "4.7.1")
    monkeypatch.setattr(stt_pkg, "_run_wake_gpu_probe_subprocess", lambda: False)

    assert _wake_gpu_inference_verified() is False
    cached = json.loads(
        (tmp_path / "wake_gpu_probe.json").read_text(encoding="utf-8")
    )
    assert cached["ok"] is False and cached["ctranslate2"] == "4.7.1"


def test_mark_wake_gpu_bad_overrides_a_verified_cache(tmp_path, monkeypatch) -> None:
    # The live backstop: a wedge on the swapped-in GPU model demotes the host
    # even though the one-off probe once said OK.
    _pin_version(monkeypatch, "4.7.1")
    _write_cache(tmp_path, ok=True, ct2="4.7.1")
    assert _wake_gpu_inference_verified() is True

    mark_wake_gpu_bad()

    _forbid_subprocess(monkeypatch)  # verdict must come from the rewritten cache
    assert _wake_gpu_inference_verified() is False


# --- the subprocess wrapper's marker contract --------------------------------


def _fake_run(stdout: str, returncode: int):
    def _run(*_a, **_k):
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    return _run


def test_probe_success_is_the_marker_not_the_exit_code(monkeypatch) -> None:
    # Observed 2026-07-05: the probe printed its marker, then the CUDA teardown
    # killed the process with exit 127. That is a PASS.
    monkeypatch.setattr(
        subprocess, "run", _fake_run("WAKE_GPU_PROBE_OK\n", returncode=127)
    )
    assert _run_wake_gpu_probe_subprocess() is True


def test_probe_clean_exit_without_marker_fails(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run("", returncode=0))
    assert _run_wake_gpu_probe_subprocess() is False


def test_probe_timeout_fails(monkeypatch) -> None:
    def _hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="python", timeout=180.0)

    monkeypatch.setattr(subprocess, "run", _hang)
    assert _run_wake_gpu_probe_subprocess() is False
