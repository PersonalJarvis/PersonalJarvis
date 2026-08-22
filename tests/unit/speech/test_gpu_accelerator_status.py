"""The GPU is used when it exists, and the app says so when it cannot.

What this guards (live 2026-08-22): the desktop app ran on an interpreter
without the cuBLAS/cuDNN wheels, every local model silently self-healed onto
the CPU (36 times in one log), the preview ran on the CPU beside the microphone
thread and the cloud fallback took 48 s for a 70 s recording. Nothing in the
app said why. Now the provider card carries the accelerator truth and offers
the install from inside the app; the ``cuda`` extra and the in-app package
list are pinned to each other.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from jarvis.plugins.stt.fwhisper import GPU_LIBRARY_PACKAGES
from jarvis.speech import local_install, local_models
from jarvis.speech.local_models import accelerator_status

REPO = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------
# The extra and the in-app installer name the same packages
# --------------------------------------------------------------------------


def test_cuda_extra_mirrors_the_in_app_package_list() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    extra = pyproject["project"]["optional-dependencies"]["cuda"]
    declared = [line.split(";", 1)[0].strip() for line in extra]
    assert declared == list(GPU_LIBRARY_PACKAGES)
    # macOS has no CUDA; every line carries the marker.
    assert all("sys_platform != 'darwin'" in line for line in extra)


def test_cuda_extra_stays_out_of_the_full_profile() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    full = pyproject["project"]["optional-dependencies"]["full"][0]
    bundled = full.split("[", 1)[1].split("]", 1)[0].split(",")
    assert "cuda" not in bundled


# --------------------------------------------------------------------------
# accelerator_status — every reason code, decided on facts on disk only
# --------------------------------------------------------------------------


def _force(monkeypatch: pytest.MonkeyPatch, *, libraries: bool, verified: bool | None) -> None:
    import jarvis.plugins.stt as stt_pkg
    import jarvis.plugins.stt.fwhisper as fwhisper

    monkeypatch.setattr(fwhisper, "cuda_runtime_libraries_present", lambda: libraries)
    monkeypatch.setattr(stt_pkg, "wake_gpu_probe_cached", lambda: verified)
    monkeypatch.setattr(local_models.sys, "platform", "win32")


def test_cpu_as_configured_is_not_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _force(monkeypatch, libraries=False, verified=None)
    status = accelerator_status("faster-whisper", requested_device="cpu")
    assert status is not None
    assert (status.effective, status.reason, status.installable) == ("cpu", "not_requested", False)


def test_missing_libraries_are_named_and_installable(monkeypatch: pytest.MonkeyPatch) -> None:
    _force(monkeypatch, libraries=False, verified=None)
    status = accelerator_status("faster-whisper", requested_device="cuda")
    assert status is not None
    assert status.effective == "cpu"
    assert status.reason == "cuda_libraries_missing"
    assert status.installable is True
    assert "CPU" in status.detail


def test_a_failed_probe_keeps_the_cpu_and_is_not_installable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force(monkeypatch, libraries=True, verified=False)
    status = accelerator_status("faster-whisper", requested_device="cuda")
    assert status is not None
    assert status.effective == "cpu"
    assert status.reason == "gpu_probe_failed"
    assert status.installable is False


def test_libraries_present_means_the_gpu_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    _force(monkeypatch, libraries=True, verified=None)
    unverified = accelerator_status("faster-whisper", requested_device="cuda")
    assert unverified is not None
    assert (unverified.effective, unverified.reason) == ("cuda", "unverified")

    _force(monkeypatch, libraries=True, verified=True)
    verified = accelerator_status("faster-whisper", requested_device="cuda")
    assert verified is not None
    assert (verified.effective, verified.reason) == ("cuda", "")


def test_macos_has_no_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    _force(monkeypatch, libraries=True, verified=True)
    monkeypatch.setattr(local_models.sys, "platform", "darwin")
    status = accelerator_status("faster-whisper", requested_device="cuda")
    assert status is not None
    assert (status.effective, status.reason) == ("cpu", "unsupported_os")


def test_runtimes_without_a_gpu_path_report_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _force(monkeypatch, libraries=True, verified=True)
    assert accelerator_status("sherpa-onnx", requested_device="cuda") is None


# --------------------------------------------------------------------------
# The in-app install: pip per package, then the on-disk truth decides
# --------------------------------------------------------------------------


def _fresh_run() -> None:
    with local_install._runs_guard:
        local_install._runs.pop(local_install.GPU_LIBRARIES_RUN_KEY, None)


def test_install_runs_pip_for_every_package_and_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.stt as stt_pkg
    import jarvis.plugins.stt.fwhisper as fwhisper
    import jarvis.setup.dependencies as deps

    _fresh_run()
    installed: list[str] = []
    present = {"value": False}
    monkeypatch.setattr(fwhisper, "cuda_runtime_libraries_present", lambda: present["value"])
    forgotten: list[bool] = []
    monkeypatch.setattr(stt_pkg, "forget_gpu_probe_results", lambda: forgotten.append(True))

    # The first verify after the pip runs finds the libraries.
    def _install_and_flip(package: str, **_kw: object) -> tuple[bool, str]:
        installed.append(package)
        present["value"] = True
        return True, "ok"

    monkeypatch.setattr(deps, "install_pip_package", _install_and_flip)
    local_install._run_gpu_libraries_install()

    status = local_install.gpu_libraries_install_status()
    assert installed == list(GPU_LIBRARY_PACKAGES)
    assert status["state"] == "done"
    assert status["ready"] is True
    assert forgotten == [True]


def test_a_failed_pip_run_is_an_honest_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.stt.fwhisper as fwhisper
    import jarvis.setup.dependencies as deps

    _fresh_run()
    monkeypatch.setattr(deps, "install_pip_package", lambda *_a, **_kw: (False, "no wheel"))
    monkeypatch.setattr(fwhisper, "cuda_runtime_libraries_present", lambda: False)

    local_install._run_gpu_libraries_install()

    status = local_install.gpu_libraries_install_status()
    assert status["state"] == "error"
    assert status["ready"] is False
    assert "no wheel" in status["message"]


def test_a_finished_install_without_libraries_is_not_a_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.plugins.stt.fwhisper as fwhisper
    import jarvis.setup.dependencies as deps

    _fresh_run()
    monkeypatch.setattr(deps, "install_pip_package", lambda *_a, **_kw: (True, "ok"))
    monkeypatch.setattr(fwhisper, "cuda_runtime_libraries_present", lambda: False)

    local_install._run_gpu_libraries_install()

    status = local_install.gpu_libraries_install_status()
    assert status["state"] == "error"
    assert "still cannot be found" in status["message"]
