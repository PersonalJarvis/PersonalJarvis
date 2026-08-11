"""Ollama runtime lifecycle: detect / install / start without a terminal.

What these tests pin: the three-state truth an HTTP probe cannot give
(not-installed vs installed-but-stopped vs running), the honest per-OS
refusals (no hidden password prompt, no unofficial download URL), and the
poll-shaped installer contract shared with the managed-server install.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain import ollama_runtime


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    ollama_runtime._reset_for_tests()
    yield
    ollama_runtime._reset_for_tests()


# ── runtime_status: the three states ─────────────────────────────────────
def test_running_server_reports_running(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: "0.9.1")
    status = ollama_runtime.runtime_status()
    assert status["installed"] is True
    assert status["running"] is True
    assert "running" in str(status["detail"])
    assert status["version"] == "0.9.1"


def test_installed_but_stopped_is_its_own_state(monkeypatch) -> None:
    """This state needs a START button, not an INSTALL button — a pure HTTP
    probe collapses it into 'unreachable' and offers the wrong fix."""
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "C:\\x\\ollama.exe")
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: None)
    status = ollama_runtime.runtime_status()
    assert status["installed"] is True
    assert status["running"] is False
    assert "not running" in str(status["detail"])


def test_absent_binary_reports_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "")
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: None)
    status = ollama_runtime.runtime_status()
    assert status["installed"] is False
    assert status["running"] is False
    assert "not installed" in str(status["detail"])


def test_a_running_server_counts_as_installed_even_without_a_binary(
    monkeypatch,
) -> None:
    """A server on a custom OLLAMA_HOST (or a nonstandard install) is real:
    running implies installed, whatever PATH says."""
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "")
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: "0.9.1")
    status = ollama_runtime.runtime_status()
    assert status["installed"] is True
    assert status["running"] is True


# ── start_server ─────────────────────────────────────────────────────────
def test_start_is_a_noop_when_already_running(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: "0.9.1")
    ok, detail = ollama_runtime.start_server()
    assert ok is True
    assert "already running" in detail


def test_start_without_a_binary_names_the_fix(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: None)
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "")
    ok, detail = ollama_runtime.start_server()
    assert ok is False
    assert "install" in detail.lower()


def test_start_spawns_detached_and_waits_for_the_port(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: None)
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "/usr/bin/ollama")
    spawned: list[dict[str, Any]] = []

    def fake_popen(argv: Any, **kwargs: Any) -> SimpleNamespace:
        spawned.append({"argv": argv, **kwargs})
        return SimpleNamespace(pid=4711)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ollama_runtime, "_port_open", lambda port, timeout=1.0: True)
    ok, detail = ollama_runtime.start_server()
    assert ok is True
    assert spawned[0]["argv"] == ["/usr/bin/ollama", "serve"]
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    assert spawned[0]["creationflags"] == NO_WINDOW_CREATIONFLAGS  # AP-1


def test_start_reports_a_server_that_never_binds(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: None)
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(
        subprocess, "Popen", lambda argv, **kwargs: SimpleNamespace(pid=1)
    )
    monkeypatch.setattr(ollama_runtime, "_port_open", lambda port, timeout=1.0: False)
    monkeypatch.setattr(ollama_runtime, "_START_WAIT_S", 0.1)
    ok, detail = ollama_runtime.start_server()
    assert ok is False
    assert "ollama_server.log" in detail


# ── installer: refusals and honesty ──────────────────────────────────────
def test_download_refuses_unofficial_urls(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="non-official"):
        ollama_runtime._download("https://evil.example/o.exe", tmp_path / "o.exe")


def test_linux_without_sudo_refuses_with_the_one_command(monkeypatch) -> None:
    """A hidden password prompt would hang the daemon thread forever; the
    honest refusal names the exact terminal command instead."""
    monkeypatch.setattr(ollama_runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(ollama_runtime.os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(RuntimeError, match="install.sh"):
        ollama_runtime._install_linux()


def test_macos_without_brew_refuses_with_the_dmg_pointer(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        ollama_runtime.Path, "exists", lambda self: False, raising=False
    )
    with pytest.raises(RuntimeError, match="ollama.com/download"):
        ollama_runtime._install_macos()


def test_macos_intel_brew_prefix_is_found_without_path(monkeypatch) -> None:
    """Homebrew lives at /usr/local on Intel Macs; a GUI-launched app whose
    PATH misses it must still find brew there instead of refusing."""
    monkeypatch.setattr(ollama_runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        ollama_runtime.Path,
        "exists",
        lambda self: self.as_posix() == "/usr/local/bin/brew",
        raising=False,
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        ollama_runtime, "_run_command", lambda cmd, timeout: commands.append(cmd)
    )
    assert ollama_runtime._install_macos() == "homebrew"
    assert commands[0][0] == "/usr/local/bin/brew"


def test_windows_prefers_winget(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_runtime.shutil,
        "which",
        lambda name: "C:\\winget.exe" if name == "winget" else None,
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        ollama_runtime,
        "_run_command",
        lambda cmd, timeout: commands.append(cmd),
    )
    assert ollama_runtime._install_windows() == "winget"
    assert commands[0][0] == "C:\\winget.exe"
    assert "Ollama.Ollama" in commands[0]
    assert "--silent" in commands[0]


def test_windows_falls_back_to_the_official_installer(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ollama_runtime.shutil, "which", lambda name: None)
    downloads: list[str] = []
    commands: list[list[str]] = []
    monkeypatch.setattr(
        ollama_runtime,
        "_download",
        lambda url, target: downloads.append(url) or target.parent.mkdir(
            parents=True, exist_ok=True
        )
        or target.write_bytes(b"exe"),
    )
    monkeypatch.setattr(
        ollama_runtime, "_run_command", lambda cmd, timeout: commands.append(cmd)
    )
    assert ollama_runtime._install_windows() == "installer-exe"
    assert downloads == [ollama_runtime._WINDOWS_INSTALLER_URL]
    assert "/VERYSILENT" in commands[0]


# ── installer: the poll-shaped job ───────────────────────────────────────
def test_install_snapshot_shape() -> None:
    snap = ollama_runtime.install_snapshot()
    assert set(snap) == {"phase", "percent", "detail", "error", "running", "log_tail"}


def test_already_running_short_circuits_to_done(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_runtime,
        "runtime_status",
        lambda: {"installed": True, "running": True, "detail": "", "version": "x", "binary": "b"},
    )
    ollama_runtime._run_install()
    snap = ollama_runtime.install_snapshot()
    assert snap["phase"] == "done"
    assert snap["error"] == ""


def test_installed_but_stopped_only_starts(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_runtime,
        "runtime_status",
        lambda: {"installed": True, "running": False, "detail": "", "version": "", "binary": "b"},
    )
    installs: list[str] = []
    monkeypatch.setattr(
        ollama_runtime, "_install_windows", lambda: installs.append("x") or "winget"
    )
    monkeypatch.setattr(
        ollama_runtime, "start_server", lambda: (True, "Ollama started.")
    )
    ollama_runtime._run_install()
    assert installs == []  # nothing was installed — it only needed a start
    assert ollama_runtime.install_snapshot()["phase"] == "done"


def test_a_failing_step_lands_in_the_error_state(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_runtime,
        "runtime_status",
        lambda: {"installed": True, "running": False, "detail": "", "version": "", "binary": "b"},
    )
    monkeypatch.setattr(
        ollama_runtime, "start_server", lambda: (False, "did not bind")
    )
    ollama_runtime._run_install()
    snap = ollama_runtime.install_snapshot()
    assert snap["phase"] == "error"
    assert "did not bind" in str(snap["error"])


def test_second_start_install_joins_instead_of_duplicating(monkeypatch) -> None:
    import threading

    release = threading.Event()
    monkeypatch.setattr(
        ollama_runtime, "_run_install", lambda: release.wait(timeout=5)
    )
    started, _ = ollama_runtime.start_install()
    assert started is True
    joined, message = ollama_runtime.start_install()
    assert joined is False
    assert "already running" in message
    release.set()


def test_marker_records_the_method(tmp_path: Path) -> None:
    ollama_runtime._record_marker("winget")
    import json

    payload = json.loads(
        (tmp_path / "ollama_installed_by_jarvis.json").read_text(encoding="utf-8")
    )
    assert payload["method"] == "winget"
