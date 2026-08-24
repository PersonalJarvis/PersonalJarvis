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
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: SimpleNamespace(pid=1))
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
    monkeypatch.setattr(ollama_runtime.Path, "exists", lambda self: False, raising=False)
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
    monkeypatch.setattr(ollama_runtime, "_run_command", lambda cmd, timeout: commands.append(cmd))
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
        lambda url, target: (
            downloads.append(url)
            or target.parent.mkdir(parents=True, exist_ok=True)
            or target.write_bytes(b"exe")
        ),
    )
    monkeypatch.setattr(ollama_runtime, "_run_command", lambda cmd, timeout: commands.append(cmd))
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
    monkeypatch.setattr(ollama_runtime, "start_server", lambda: (True, "Ollama started."))
    ollama_runtime._run_install()
    assert installs == []  # nothing was installed — it only needed a start
    assert ollama_runtime.install_snapshot()["phase"] == "done"


def test_a_failing_step_lands_in_the_error_state(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_runtime,
        "runtime_status",
        lambda: {"installed": True, "running": False, "detail": "", "version": "", "binary": "b"},
    )
    monkeypatch.setattr(ollama_runtime, "start_server", lambda: (False, "did not bind"))
    ollama_runtime._run_install()
    snap = ollama_runtime.install_snapshot()
    assert snap["phase"] == "error"
    assert "did not bind" in str(snap["error"])


def test_second_start_install_joins_instead_of_duplicating(monkeypatch) -> None:
    import threading

    release = threading.Event()
    monkeypatch.setattr(ollama_runtime, "_run_install", lambda: release.wait(timeout=5))
    started, _ = ollama_runtime.start_install()
    assert started is True
    joined, message = ollama_runtime.start_install()
    assert joined is False
    assert "already running" in message
    release.set()


def test_marker_records_the_method(tmp_path: Path) -> None:
    ollama_runtime._record_marker("winget")
    import json

    payload = json.loads((tmp_path / "ollama_installed_by_jarvis.json").read_text(encoding="utf-8"))
    assert payload["method"] == "winget"


# ── stop_server: only the pid Jarvis spawned ─────────────────────────────
class _FakeProcess:
    """A psutil.Process stand-in: alive until terminated, named like Ollama."""

    instances: list[_FakeProcess] = []

    def __init__(self, pid: int, name: str = "ollama.exe") -> None:
        self.pid = pid
        self._name = name
        self.terminated = False
        self.killed = False
        _FakeProcess.instances.append(self)

    def name(self) -> str:
        return self._name

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_start_records_the_spawned_pid_beside_the_log(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: None)
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: SimpleNamespace(pid=4711))
    monkeypatch.setattr(ollama_runtime, "_port_open", lambda port, timeout=1.0: True)
    ok, _ = ollama_runtime.start_server()
    assert ok is True
    import json

    record = json.loads((tmp_path / "ollama_server.pid").read_text(encoding="utf-8"))
    assert record["pid"] == 4711
    assert ollama_runtime._recorded_pid() == 4711


def test_stop_without_a_pid_file_refuses_honestly() -> None:
    """A server Jarvis did not start is never touched — the sentence says
    where to stop it instead of pretending nothing is running."""
    ok, detail = ollama_runtime.stop_server()
    assert ok is False
    assert "not started by Jarvis" in detail
    assert "stop it where you started it" in detail


def test_stop_with_a_dead_pid_forgets_the_record(monkeypatch, tmp_path) -> None:
    import psutil

    ollama_runtime._record_pid(99999, "/usr/bin/ollama")

    def gone(pid: int) -> _FakeProcess:
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", gone)
    ok, detail = ollama_runtime.stop_server()
    assert ok is False
    assert "no longer running" in detail
    assert not (tmp_path / "ollama_server.pid").exists()
    # The next answer is the plain "not ours" sentence again.
    assert "not started by Jarvis" in ollama_runtime.stop_server()[1]


def test_stop_terminates_only_the_recorded_ollama_process(monkeypatch, tmp_path) -> None:
    import psutil

    _FakeProcess.instances.clear()
    ollama_runtime._record_pid(4711, "/usr/bin/ollama")
    monkeypatch.setattr(psutil, "Process", _FakeProcess)
    ok, detail = ollama_runtime.stop_server()
    assert ok is True
    assert detail == "Ollama stopped."
    assert [p.pid for p in _FakeProcess.instances] == [4711]
    assert _FakeProcess.instances[0].terminated is True
    assert not (tmp_path / "ollama_server.pid").exists()


def test_stop_refuses_a_recycled_pid(monkeypatch, tmp_path) -> None:
    """The OS may hand a dead server's pid to an unrelated program; that
    program is never signalled."""
    import psutil

    _FakeProcess.instances.clear()
    ollama_runtime._record_pid(4711, "/usr/bin/ollama")
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess(pid, "python.exe"))
    ok, detail = ollama_runtime.stop_server()
    assert ok is False
    assert "no longer belongs to Ollama" in detail
    assert _FakeProcess.instances[0].terminated is False
    assert not (tmp_path / "ollama_server.pid").exists()


# ── tail_log ─────────────────────────────────────────────────────────────
def test_tail_log_without_a_log_is_empty() -> None:
    assert ollama_runtime.tail_log() == []


def test_tail_log_returns_the_last_lines_utf8_safe(tmp_path) -> None:
    lines = [f"line {i}" for i in range(50)]
    body = "\n".join(lines) + "\n"
    (tmp_path / "ollama_server.log").write_bytes(body.encode("utf-8") + b"bad \xff byte\n")
    tail = ollama_runtime.tail_log(lines=3)
    assert tail == ["line 48", "line 49", "bad \ufffd byte"]
    assert ollama_runtime.tail_log(lines=0) == []


# ── probe_host ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_probe_host_reports_version_and_latency() -> None:
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"version": "0.32.15"})

    result = await ollama_runtime.probe_host(
        "gpu-box:11434/v1", transport=httpx.MockTransport(handler)
    )
    assert seen == ["http://gpu-box:11434/api/version"]
    assert result["ok"] is True
    assert result["version"] == "0.32.15"
    assert isinstance(result["latency_ms"], int)
    assert "0.32.15" in str(result["detail"])


@pytest.mark.asyncio
async def test_probe_host_names_a_non_ollama_answer() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    result = await ollama_runtime.probe_host(
        "http://web-server:8080", transport=httpx.MockTransport(handler)
    )
    assert result["ok"] is False
    assert "HTTP 404" in str(result["detail"])
    assert "not an Ollama server" in str(result["detail"])


@pytest.mark.asyncio
async def test_probe_host_unreachable_is_one_sentence() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await ollama_runtime.probe_host(
        "http://127.0.0.1:9", transport=httpx.MockTransport(handler)
    )
    assert result["ok"] is False
    assert result["version"] == ""
    assert "No Ollama answered at http://127.0.0.1:9" in str(result["detail"])


# ── env_guide per OS ─────────────────────────────────────────────────────
_ENV_EXPECTED_KEYS = [
    "OLLAMA_HOST",
    "OLLAMA_MODELS",
    "OLLAMA_KEEP_ALIVE",
    "OLLAMA_NUM_PARALLEL",
    "OLLAMA_MAX_LOADED_MODELS",
    "OLLAMA_FLASH_ATTENTION",
    "OLLAMA_KV_CACHE_TYPE",
]


@pytest.mark.parametrize(
    ("os_name", "marker"),
    [
        ("windows", "setx OLLAMA_HOST "),
        ("macos", "launchctl setenv OLLAMA_HOST "),
        ("linux", "systemctl edit ollama.service"),
    ],
)
def test_env_guide_recipes_per_os(os_name: str, marker: str) -> None:
    rows = ollama_runtime.env_guide(os_name)
    assert [row["key"] for row in rows] == _ENV_EXPECTED_KEYS
    assert marker in rows[0]["command"]
    for row in rows:
        assert set(row) == {"key", "purpose", "command", "restart"}
        assert row["purpose"].endswith(".")  # one plain sentence each
        assert row["key"] in row["command"]


def test_env_guide_linux_uses_a_systemd_drop_in() -> None:
    row = ollama_runtime.env_guide("linux")[0]
    assert 'Environment="OLLAMA_HOST=0.0.0.0"' in row["command"]
    assert "systemctl restart ollama" in row["restart"]


def test_env_guide_accepts_platform_spellings() -> None:
    assert ollama_runtime.env_guide("darwin")[0]["command"].startswith("launchctl")
    assert ollama_runtime.env_guide("win32")[0]["command"].startswith("setx")
    assert ollama_runtime.env_guide("nt")[0]["command"].startswith("setx")


# ── models_dir per OS ────────────────────────────────────────────────────
def test_models_dir_env_override_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "weights"))
    assert ollama_runtime.models_dir("linux") == tmp_path / "weights"


def test_models_dir_windows_and_macos_default_to_the_home_folder(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(ollama_runtime.Path, "home", classmethod(lambda cls: tmp_path))
    assert ollama_runtime.models_dir("windows") == tmp_path / ".ollama" / "models"
    assert ollama_runtime.models_dir("macos") == tmp_path / ".ollama" / "models"


def test_models_dir_linux_prefers_the_service_user_when_present(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(ollama_runtime.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        ollama_runtime.Path,
        "is_dir",
        lambda self: self.as_posix() == "/usr/share/ollama/.ollama/models",
        raising=False,
    )
    assert ollama_runtime.models_dir("linux").as_posix() == "/usr/share/ollama/.ollama/models"
    monkeypatch.setattr(ollama_runtime.Path, "is_dir", lambda self: False, raising=False)
    assert ollama_runtime.models_dir("linux") == tmp_path / ".ollama" / "models"


def test_models_dir_follows_the_running_os_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(ollama_runtime, "_current_os", lambda: "macos")
    monkeypatch.setattr(ollama_runtime.Path, "home", classmethod(lambda cls: tmp_path))
    assert ollama_runtime.models_dir() == tmp_path / ".ollama" / "models"


# ── host_kind + runtime_status shape ─────────────────────────────────────
@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("http://127.0.0.1:11434", "local"),
        ("http://localhost:11434", "local"),
        ("http://[::1]:11434", "local"),
        ("http://0.0.0.0:11434", "local"),
        ("http://gpu-box.lan:11434", "remote"),
        ("https://ollama.example.com", "remote"),
    ],
)
def test_host_kind(url: str, kind: str) -> None:
    assert ollama_runtime.host_kind(url) == kind


def test_host_kind_treats_this_machines_name_as_local(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime.socket, "gethostname", lambda: "Studio-PC")
    assert ollama_runtime.host_kind("http://studio-pc:11434") == "local"
    assert ollama_runtime.host_kind("http://studio-pc.local:11434") == "local"


def test_runtime_status_carries_host_kind_models_dir_and_version(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "find_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(ollama_runtime, "_server_version", lambda timeout=1.5: "0.32.15")
    monkeypatch.setattr(ollama_runtime, "_server_root", lambda: "http://gpu-box:11434")
    monkeypatch.setenv("OLLAMA_MODELS", "/srv/weights")
    status = ollama_runtime.runtime_status()
    assert status["version"] == "0.32.15"
    assert status["host_kind"] == "remote"
    assert status["base_url"] == "http://gpu-box:11434"
    assert Path(str(status["models_dir"])) == Path("/srv/weights")
