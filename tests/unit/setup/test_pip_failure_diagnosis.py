"""Honest pip-failure diagnosis + wheel-only install (BUG-059).

On the first real-Mac onboarding, enabling the local speech pack failed with
"Failed to build 'av'" (no cp314/macOS wheel -> pip fell back to a SOURCE
build that needs FFmpeg dev libraries no end user has) — and the UI blamed
the user's internet connection. Two contracts locked here:

1. ``classify_pip_failure`` names the real cause: a missing prebuilt wheel /
   source-build failure is reported as such (with the running Python
   version), and only genuine network errors mention the network.
2. ``install_pip_package(..., only_binary=True)`` passes
   ``--only-binary=:all:`` so an end-user install NEVER attempts a source
   build — it fails fast with the honest no-wheel message instead.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from jarvis.setup import dependencies as deps

_FFMPEG_BUILD_STDERR = (
    "Package 'libavformat' not found Package 'libavcodec' not found "
    "pkg-config could not find libraries ['avformat', 'avcodec'] "
    "[end of output] note: This error originates from a subprocess, and is "
    "likely not a problem with pip. ERROR: Failed to build 'av' when "
    "getting requirements to build wheel"
)


def test_source_build_failure_is_diagnosed_as_missing_wheel() -> None:
    msg = deps.classify_pip_failure(_FFMPEG_BUILD_STDERR)
    assert msg is not None
    assert "prebuilt" in msg.lower()
    ver = f"{sys.version_info[0]}.{sys.version_info[1]}"
    assert ver in msg  # names the running Python so the fix is actionable
    assert "internet" not in msg.lower()


def test_no_matching_distribution_is_diagnosed_as_missing_wheel() -> None:
    msg = deps.classify_pip_failure(
        "ERROR: Could not find a version that satisfies the requirement av "
        "(from versions: none)\nERROR: No matching distribution found for av"
    )
    assert msg is not None
    assert "prebuilt" in msg.lower()


def test_network_failure_is_diagnosed_as_network() -> None:
    msg = deps.classify_pip_failure(
        "WARNING: Retrying (Retry(total=0)) after connection broken by "
        "'NewConnectionError(': Failed to establish a new connection: "
        "[Errno -3] Temporary failure in name resolution')'"
    )
    assert msg is not None
    assert "network" in msg.lower()


def test_unknown_failure_returns_none_for_generic_fallback() -> None:
    assert deps.classify_pip_failure("something entirely unexpected") is None


def _fake_run_recorder(record: list) -> object:
    def _run(cmd, **_kw):
        record.append(cmd)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    return _run


def test_only_binary_flag_forbids_source_builds(monkeypatch) -> None:
    cmds: list[list[str]] = []
    monkeypatch.setattr(deps.subprocess, "run", _fake_run_recorder(cmds))
    ok, _ = deps.install_pip_package("faster-whisper>=1.0", only_binary=True)
    assert ok is True
    assert "--only-binary" in cmds[0]
    assert ":all:" in cmds[0]


def test_default_install_keeps_todays_command_shape(monkeypatch) -> None:
    cmds: list[list[str]] = []
    monkeypatch.setattr(deps.subprocess, "run", _fake_run_recorder(cmds))
    deps.install_pip_package("rich")
    assert "--only-binary" not in cmds[0]  # opt-in only — AD-7 for other callers


def test_failed_install_message_leads_with_the_diagnosis(monkeypatch) -> None:
    def _run(cmd, **_kw):
        return SimpleNamespace(returncode=1, stdout="", stderr=_FFMPEG_BUILD_STDERR)

    monkeypatch.setattr(deps.subprocess, "run", _run)
    ok, msg = deps.install_pip_package("faster-whisper>=1.0", only_binary=True)
    assert ok is False
    assert msg.lower().startswith("no prebuilt")
    assert "pip exited 1" in msg  # raw tail preserved below the diagnosis
