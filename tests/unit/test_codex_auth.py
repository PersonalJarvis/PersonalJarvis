"""Unit tests for the rebuilt CodexAuthService.

The original module was lost (only a stub was ever committed); these tests pin
the behaviour the UI + provider routes rely on: an honest status snapshot that
reads ``~/.codex/auth.json`` (or ``$CODEX_HOME``) and reports whether Codex is
connected via the ChatGPT subscription (OAuth) or an OpenAI API key.

No real ``codex`` binary and no network are touched — the binary resolution and
version probe are seams that the tests stub; the auth file is a real temp file.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import jarvis.codex_auth as codex_auth_module
from jarvis.codex_auth import CodexAuthService, CodexAuthStatus, _derive_auth


def _write_auth(home: Path, payload: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps(payload), encoding="utf-8")


def _jwt_with_email(email: str) -> str:
    """A minimal unsigned JWT whose payload carries an email claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


# ----------------------------------------------------------------------
# _derive_auth — pure (connected, mode) decision
# ----------------------------------------------------------------------


def test_derive_auth_chatgpt_when_oauth_tokens_present() -> None:
    connected, mode = _derive_auth({"tokens": {"access_token": "abc"}})
    assert connected is True
    assert mode == "chatgpt"


def test_derive_auth_api_key_when_openai_key_present() -> None:
    connected, mode = _derive_auth({"OPENAI_API_KEY": "sk-test-123"})
    assert connected is True
    assert mode == "api_key"


def test_derive_auth_unknown_when_empty() -> None:
    assert _derive_auth(None) == (False, "unknown")
    assert _derive_auth({}) == (False, "unknown")
    assert _derive_auth({"OPENAI_API_KEY": ""}) == (False, "unknown")


# ----------------------------------------------------------------------
# CodexAuthStatus — wire contract the frontend + provider_routes consume
# ----------------------------------------------------------------------


def test_status_to_dict_contains_frontend_contract_fields() -> None:
    status = CodexAuthStatus(
        installed=True,
        connected=True,
        mode="chatgpt",
        message="Connected via ChatGPT",
        version="codex 1.2.3",
        accountLabel="ChatGPT/Codex-Login",
    )
    d = status.to_dict()
    for key in ("installed", "connected", "mode", "message", "version"):
        assert key in d, f"to_dict() must expose {key!r} for the UI"
    assert d["mode"] == "chatgpt"
    assert d["message"] == "Connected via ChatGPT"


# ----------------------------------------------------------------------
# CodexAuthService.status() — composes binary + auth.json
# ----------------------------------------------------------------------


def test_windows_generic_binary_prefers_the_official_npm_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def which(name: str) -> str | None:
        calls.append(name)
        return {
            "codex.cmd": r"C:\npm\codex.cmd",
            "codex": r"C:\WindowsApps\codex.exe",
        }.get(name)

    monkeypatch.setattr(codex_auth_module.sys, "platform", "win32")
    monkeypatch.setattr(codex_auth_module.shutil, "which", which)
    monkeypatch.setattr("jarvis.core.path_augment.ensure_cli_paths", lambda: [])

    assert CodexAuthService()._resolve_binary() == r"C:\npm\codex.cmd"
    assert calls == ["codex.cmd"]


def test_status_not_installed_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: None)
    status = svc.status()
    assert status.installed is False
    assert status.connected is False
    assert status.mode == "unknown"
    assert status.message  # never empty — UI shows this instead of "loading"


def test_status_detects_chatgpt_from_auth_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, {"tokens": {"access_token": "live-token"}})
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()
    assert status.installed is True
    assert status.connected is True
    assert status.mode == "chatgpt"
    assert status.version == "codex 1.2.3"


def test_status_detects_api_key_from_auth_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, {"OPENAI_API_KEY": "sk-codex-xyz"})
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()
    assert status.connected is True
    assert status.mode == "api_key"


def test_status_unknown_when_auth_file_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))  # dir exists, no auth.json
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()
    assert status.installed is True
    assert status.connected is False
    assert status.mode == "unknown"


def test_status_extracts_email_from_id_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(
        tmp_path,
        {"tokens": {"access_token": "x", "id_token": _jwt_with_email("you@example.com")}},
    )
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()
    assert status.user_email == "you@example.com"


def test_status_tolerates_corrupt_auth_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "auth.json").write_text("{ this is not json", encoding="utf-8")
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()  # must not raise
    assert status.connected is False
    assert status.mode == "unknown"


# ----------------------------------------------------------------------
# version-probe caching — the cold-start latency fix
# ----------------------------------------------------------------------


def test_probe_version_cached_across_status_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`codex --version` is invariant at runtime but is the dominant cold-start
    cost — /api/providers used to spawn it 2-4x PER request. It must be probed
    once per binary and cached, so every later status() is a pure auth-file read.
    """
    import jarvis.codex_auth as codex_mod

    codex_mod.clear_version_cache()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, {"tokens": {"access_token": "t"}})

    runs = {"n": 0}

    def _fake_run(_argv, **_kw):  # noqa: ANN001, ANN003
        runs["n"] += 1

        class _R:
            stdout = "codex 1.2.3"
            stderr = ""
            returncode = 0

        return _R()

    monkeypatch.setattr(codex_mod.subprocess, "run", _fake_run)
    # Stub on the class so a fresh instance resolves the SAME binary key (and
    # thus shares the module-level cache).
    monkeypatch.setattr(CodexAuthService, "_resolve_binary", lambda self: "codex")

    v1 = CodexAuthService("codex").status()
    v2 = CodexAuthService("codex").status()
    v3 = CodexAuthService("codex").status()  # a fresh instance shares the cache

    assert v1.version == v2.version == v3.version == "codex 1.2.3"
    assert runs["n"] == 1, "codex --version must run once, then be cached"


def test_probe_version_caches_failure_to_avoid_repeated_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hanging/absent codex must not pay a 4s subprocess timeout on every
    status() call — the failed probe is cached too."""
    import jarvis.codex_auth as codex_mod

    codex_mod.clear_version_cache()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, {"tokens": {"access_token": "t"}})

    runs = {"n": 0}

    def _fake_run(_argv, **_kw):  # noqa: ANN001, ANN003
        runs["n"] += 1
        raise codex_mod.subprocess.TimeoutExpired(cmd="codex", timeout=4.0)

    monkeypatch.setattr(codex_mod.subprocess, "run", _fake_run)
    monkeypatch.setattr(CodexAuthService, "_resolve_binary", lambda self: "codex")

    assert CodexAuthService("codex").status().version is None
    assert CodexAuthService("codex").status().version is None
    assert runs["n"] == 1, "a failed probe must be cached, not retried every call"


# ----------------------------------------------------------------------
# start_login / logout — spawn discipline (cross-platform, AP-1)
# ----------------------------------------------------------------------


def test_start_login_raises_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: None)
    with pytest.raises(FileNotFoundError):
        svc.start_login()


def test_guarded_login_handoff_runs_real_guardian(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The service and guardian transfer one real cross-process profile lock."""
    import jarvis.codex_auth as codex_mod
    from jarvis.core.exclusive_process_lock import (
        ExclusiveProcessLock,
        ExclusiveProcessLockError,
    )
    from jarvis.core.private_directory import ensure_owner_only_directory

    profile = tmp_path / "profile"
    coordination = tmp_path / "coordination"
    guard_directory = coordination / "login"
    ensure_owner_only_directory(profile, create=True)
    ensure_owner_only_directory(coordination, create=True)
    ensure_owner_only_directory(guard_directory, create=True)
    lock_path = coordination / "owner.lock"
    parent_lock = ExclusiveProcessLock.acquire(
        lock_path,
        protected_directory=profile,
    )
    binary = Path(sys.executable).resolve(strict=True)
    service = CodexAuthService(
        str(binary),
        codex_home=profile,
        force_file_auth_store=True,
        isolate_openai_environment=True,
        log_dir=guard_directory,
        visible_login=False,
        lifetime_lock_path=lock_path,
        login_guard_directory=guard_directory,
        login_guard_handoff=parent_lock.close,
        trusted_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(service, "_resolve_binary", lambda: str(binary))
    if sys.platform == "win32":
        monkeypatch.setattr(
            codex_mod,
            "_NEW_CONSOLE_FLAGS",
            codex_mod.NO_WINDOW_CREATIONFLAGS,
        )

    process = None
    try:
        process = service.start_login()
        assert parent_lock.closed is True
        assert process.wait() == 0
        with pytest.raises(ExclusiveProcessLockError) as caught:
            ExclusiveProcessLock.acquire(
                lock_path,
                protected_directory=profile,
            )
        assert caught.value.reason == "busy"
    finally:
        if process is not None:
            process.release_profile_lock()
        elif not parent_lock.closed:
            parent_lock.close()

    reacquired = ExclusiveProcessLock.acquire(
        lock_path,
        protected_directory=profile,
    )
    reacquired.close()


def test_guarded_windows_login_closes_job_when_guardian_exits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as codex_mod
    import jarvis.core.process_tree as process_tree_module

    guardian_exited = threading.Event()
    job_closed = threading.Event()
    events: list[str] = []

    class FakeProcess:
        pid = 9321

        def poll(self) -> int | None:
            return 74 if guardian_exited.is_set() else None

        def wait(self, timeout: float | None = None) -> int:
            if not guardian_exited.wait(timeout):
                raise subprocess.TimeoutExpired(["guardian"], timeout)
            return 74

        def terminate(self) -> None:
            guardian_exited.set()

    class FakeProcessTree:
        supports_containment = True

        def __init__(self) -> None:
            self.assigned: list[int] = []
            self.close_calls = 0

        def assign(self, pid: int) -> None:
            events.append("assign")
            self.assigned.append(pid)

        def close(self) -> None:
            self.close_calls += 1
            job_closed.set()

    process = FakeProcess()
    process_tree = FakeProcessTree()
    captured: dict[str, object] = {}

    def spawn(command: list[str], **kwargs: object) -> FakeProcess:
        events.append("spawn")
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    def handoff(*_args: object, **_kwargs: object) -> None:
        events.append("handoff")

    monkeypatch.setattr(codex_mod.sys, "platform", "win32")
    monkeypatch.setattr(codex_mod.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        process_tree_module,
        "make_process_tree",
        lambda _name: process_tree,
    )
    monkeypatch.setattr(
        codex_mod._GuardedCodexLoginProcess,
        "establish_handoff",
        staticmethod(handoff),
    )
    service = CodexAuthService(
        "codex.exe",
        lifetime_lock_path=tmp_path / "owner.lock",
        login_guard_handoff=lambda: None,
    )
    monkeypatch.setattr(service, "_resolve_binary", lambda: "codex.exe")
    monkeypatch.setattr(
        service,
        "_guarded_login_command",
        lambda _binary: (
            ["guardian.exe"],
            tmp_path / "login.ack",
            tmp_path / "login.release",
            tmp_path / "login.alive",
        ),
    )

    wrapper = service.start_login()
    assert isinstance(wrapper, codex_mod._GuardedCodexLoginProcess)
    assert events == ["spawn", "assign", "handoff"]
    assert process_tree.assigned == [process.pid]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert int(kwargs["creationflags"]) & codex_mod._CREATE_BREAKAWAY_FROM_JOB

    guardian_exited.set()
    assert job_closed.wait(1.0)
    assert wrapper.wait() == 74
    assert process_tree.close_calls == 1


def test_start_login_posix_detaches_and_redirects_stdio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a headless host the child must NOT inherit the server's stdio and must
    run in its own session (no zombie, no garbled HTTP stream)."""
    import subprocess as sp

    monkeypatch.setattr("jarvis.codex_auth.sys.platform", "linux")
    captured: dict = {}

    class _FakeProc:
        pid = 4321

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr("jarvis.codex_auth.subprocess.Popen", _fake_popen)
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")

    proc = svc.start_login()
    assert proc.pid == 4321
    assert captured["cmd"] == ["codex", "login"]
    assert captured["kwargs"].get("stdout") is sp.DEVNULL
    assert captured["kwargs"].get("stderr") is sp.DEVNULL
    assert captured["kwargs"].get("start_new_session") is True


def test_start_login_windows_uses_visible_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows: a fresh visible console for the device URL — stdout NOT to DEVNULL."""
    import subprocess as sp

    monkeypatch.setattr("jarvis.codex_auth.sys.platform", "win32")
    captured: dict = {}

    class _FakeProc:
        pid = 9

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr("jarvis.codex_auth.subprocess.Popen", _fake_popen)
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")

    svc.start_login()
    assert "creationflags" in captured["kwargs"]
    assert captured["kwargs"].get("stdout") is not sp.DEVNULL


def test_dedicated_login_pins_file_store_log_dir_and_scrubs_api_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as codex_mod

    captured: dict = {}

    class _FakeProc:
        pid = 10

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(codex_mod.sys, "platform", "win32")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-login")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://billing-sink.invalid")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "must-not-reach-login")
    monkeypatch.setenv("HTTPS_PROXY", "http://credential-proxy.invalid")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "must-not-reach-login")
    monkeypatch.setenv("LD_PRELOAD", "must-not-reach-login")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/host/keyring")
    monkeypatch.setattr(codex_mod.subprocess, "Popen", _fake_popen)
    home = tmp_path / "dedicated-home"
    log_dir = tmp_path / "isolated-logs"
    service = CodexAuthService(
        "codex",
        codex_home=home,
        force_file_auth_store=True,
        isolate_openai_environment=True,
        log_dir=log_dir,
    )
    monkeypatch.setattr(service, "_resolve_binary", lambda: "codex")

    service.start_login()

    assert captured["cmd"] == [
        "codex",
        "-c",
        'cli_auth_credentials_store="file"',
        "-c",
        f"log_dir={json.dumps(str(log_dir))}",
        "login",
    ]
    environment = captured["kwargs"]["env"]
    assert environment["CODEX_HOME"] == str(home)
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "AZURE_OPENAI_API_KEY",
        "HTTPS_PROXY",
        "AWS_SESSION_TOKEN",
        "LD_PRELOAD",
        "DBUS_SESSION_BUS_ADDRESS",
    ):
        assert name not in environment


def test_visible_linux_login_uses_a_waiting_desktop_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as codex_mod

    captured: dict = {}

    class _FakeProc:
        pid = 11

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(codex_mod.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":9")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-9")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/9/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/9")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-not-reach-codex.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-reach-codex.invalid")
    monkeypatch.setattr(
        codex_mod.shutil,
        "which",
        lambda name: "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None,
    )
    monkeypatch.setattr(codex_mod.subprocess, "Popen", _fake_popen)
    service = CodexAuthService(
        "codex",
        codex_home=tmp_path / "home",
        force_file_auth_store=True,
        isolate_openai_environment=True,
        visible_login=True,
    )
    monkeypatch.setattr(service, "_resolve_binary", lambda: "/usr/bin/codex")

    service.start_login()

    assert captured["cmd"][:3] == [
        str(Path("/usr/bin/gnome-terminal").resolve()),
        "--wait",
        "--",
    ]
    wrapper = captured["cmd"][3:]
    assert wrapper[0] == str(Path(codex_mod.sys.executable).resolve())
    assert wrapper[1:4] == ["-I", "-S", "-c"]
    child_environment = json.loads(wrapper[5])
    assert wrapper[6:] == [
        "/usr/bin/codex",
        "-c",
        'cli_auth_credentials_store="file"',
        "login",
    ]
    assert child_environment["CODEX_HOME"] == str(tmp_path / "home")
    for name in (
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "HTTPS_PROXY",
    ):
        assert name not in child_environment
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"].get("stdout") is not codex_mod.subprocess.DEVNULL
    launcher_environment = captured["kwargs"]["env"]
    assert launcher_environment["DISPLAY"] == ":9"
    assert launcher_environment["WAYLAND_DISPLAY"] == "wayland-9"
    assert launcher_environment["DBUS_SESSION_BUS_ADDRESS"].endswith("/bus")
    assert launcher_environment["XDG_RUNTIME_DIR"] == "/run/user/9"
    assert "OPENAI_API_KEY" not in launcher_environment
    assert "HTTPS_PROXY" not in launcher_environment


def test_visible_linux_login_resolves_waiting_terminal_alternative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as codex_mod

    terminal = tmp_path / "gnome-terminal"
    alternative = tmp_path / "x-terminal-emulator"
    terminal.write_bytes(b"")
    alternative.write_bytes(b"")
    original_resolve = Path.resolve

    def resolve(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path == alternative:
            return terminal
        return original_resolve(path, *args, **kwargs)

    captured: dict[str, object] = {}

    class _FakeProc:
        pid = 13

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(codex_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        codex_mod.shutil,
        "which",
        lambda name: str(alternative) if name == "x-terminal-emulator" else None,
    )
    monkeypatch.setattr(
        codex_mod.subprocess,
        "Popen",
        lambda command, **kwargs: (
            captured.update(command=command, kwargs=kwargs) or _FakeProc()
        ),
    )
    service = CodexAuthService(
        "/usr/bin/codex",
        codex_home=tmp_path / "home",
        force_file_auth_store=True,
        isolate_openai_environment=True,
        visible_login=True,
    )
    monkeypatch.setattr(service, "_resolve_binary", lambda: "/usr/bin/codex")

    service.start_login()

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:3] == [str(terminal), "--wait", "--"]


def test_visible_macos_login_opens_terminal_and_scrubs_shell_api_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as codex_mod

    captured: dict = {}

    class _FakeProc:
        pid = 12

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(codex_mod.sys, "platform", "darwin")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-reach-codex.invalid")
    monkeypatch.setattr(codex_mod.subprocess, "Popen", _fake_popen)
    home = tmp_path / "dedicated home"
    service = CodexAuthService(
        "/usr/local/bin/codex",
        codex_home=home,
        force_file_auth_store=True,
        isolate_openai_environment=True,
        visible_login=True,
    )
    monkeypatch.setattr(service, "_resolve_binary", lambda: "/usr/local/bin/codex")

    service.start_login()

    assert captured["cmd"][:2] == ["/usr/bin/osascript", "-e"]
    script = captured["cmd"][2]
    assert 'tell application "Terminal"' in script
    assert "repeat while busy of loginTab" in script
    assert "os.execve" in script
    assert "OPENAI_API_KEY" not in script
    assert "HTTPS_PROXY" not in script
    assert "CODEX_HOME" in script
    assert home.name in script
    assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Logged in using ChatGPT\n", (True, "chatgpt")),
        ("Logged in using an API key\n", (True, "api_key")),
        ("user@example.com\n", (False, "unknown")),
    ],
)
def test_login_status_accepts_only_exact_pii_free_mode_strings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output: str,
    expected: tuple[bool, str],
) -> None:
    import jarvis.codex_auth as codex_mod

    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = output
        stderr = ""

    def _fake_run(argv, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(codex_mod.subprocess, "run", _fake_run)
    service = CodexAuthService(
        "codex",
        codex_home=tmp_path / "home",
        force_file_auth_store=True,
        isolate_openai_environment=True,
        log_dir=tmp_path / "logs",
    )
    monkeypatch.setattr(service, "_resolve_binary", lambda: "codex")

    assert service.login_status() == expected
    assert captured["argv"][-2:] == ["login", "status"]
    assert 'cli_auth_credentials_store="file"' in captured["argv"]


def test_logout_returns_false_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: None)
    ok, err = svc.logout_blocking()
    assert ok is False
    assert err


@pytest.mark.parametrize(
    ("available", "expected_flags"),
    [
        ("gnome-terminal", ["--wait", "--"]),
        ("konsole", ["--nofork", "-e"]),
        ("xfce4-terminal", ["--disable-server", "-x"]),
        ("mate-terminal", ["--disable-factory", "-x"]),
        ("tilix", ["--new-process", "-e"]),
        ("terminator", ["--no-dbus", "-x"]),
        ("kitty", []),
        ("alacritty", ["-e"]),
        # --always-new-process is load-bearing: see OS-4 in docs/os-parity.md.
        ("wezterm", ["start", "--always-new-process", "--"]),
        ("foot", []),
        ("xterm", ["-e"]),
    ],
)
def test_linux_login_accepts_every_supported_terminal(
    monkeypatch: pytest.MonkeyPatch,
    available: str,
    expected_flags: list[str],
) -> None:
    """A desktop without GNOME/KDE/xterm could not connect at all.

    The old list held exactly three names, so XFCE, MATE, Cinnamon and anyone
    on kitty/alacritty/foot/wezterm hit a hard "no supported desktop terminal"
    while the Providers card still offered the Connect button.
    """
    import jarvis.codex_auth as codex_mod

    monkeypatch.setattr(codex_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        codex_mod.shutil,
        "which",
        lambda name: f"/usr/bin/{available}" if name == available else None,
    )

    resolved, flags = codex_mod._resolve_linux_login_terminal()

    assert Path(resolved).name == available
    assert list(flags) == expected_flags
    assert codex_mod.linux_login_terminal_available() is True


def test_linux_login_without_any_terminal_is_an_actionable_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No usable terminal must be a named capability gap, not a mystery."""
    import jarvis.codex_auth as codex_mod

    monkeypatch.setattr(codex_mod.sys, "platform", "linux")
    monkeypatch.setattr(codex_mod.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="Install one of"):
        codex_mod._resolve_linux_login_terminal()
    assert codex_mod.linux_login_terminal_available() is False


def test_graphical_linux_without_a_terminal_does_not_invite_a_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-click truth: never offer a Connect that is guaranteed to fail."""
    import jarvis.codex_app_server as transport
    import jarvis.codex_auth as codex_mod

    monkeypatch.setattr(transport.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(codex_mod.shutil, "which", lambda _name: None)

    reason, code = transport._login_required_state("please log in")

    assert code == "lifecycle_unavailable"
    assert "terminal emulator" in reason


def test_pure_wayland_login_names_the_manual_browser_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The card must not leave a pure-Wayland user waiting for a window."""
    import jarvis.codex_app_server as transport
    import jarvis.codex_auth as codex_mod

    monkeypatch.setattr(transport.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(codex_mod, "linux_login_terminal_available", lambda: True)

    reason, code = transport._login_required_state("please log in")

    assert code == "login_required"
    assert "pure-Wayland" in reason
    assert "device-login URL" in reason
    assert "terminal" in reason


def test_every_login_terminal_records_why_its_flags_keep_it_in_the_foreground() -> None:
    """The reason column is the review gate for a new entry, not decoration.

    The guardian holds the profile lock until codex exits, so an entry whose
    launcher returns early releases that lock mid-write. That property cannot be
    checked automatically, so every entry has to carry the argument for it.
    """
    import jarvis.codex_auth as codex_mod

    assert codex_mod._LINUX_LOGIN_TERMINALS
    for name, _flags, reason in codex_mod._LINUX_LOGIN_TERMINALS:
        assert name and name == name.strip()
        assert len(reason) > 20, f"{name} has no usable justification"


def test_wezterm_always_spawns_its_own_process() -> None:
    """Plain ``wezterm start`` delegates to a running wezterm-gui and returns.

    That would let ``cleanup_login`` run its post-check and release the profile
    lock while codex was still writing auth.json.
    """
    import jarvis.codex_auth as codex_mod

    flags = dict(
        (name, flags) for name, flags, _reason in codex_mod._LINUX_LOGIN_TERMINALS
    )["wezterm"]
    assert "--always-new-process" in flags


@pytest.mark.parametrize(
    "rejected",
    [
        # Debian's x-terminal-emulator alternative. It does not accept the
        # gnome-terminal flags, so prefix matching launched something that could
        # never host the login.
        "gnome-terminal.wrapper",
        # The old `st` entry accepted anything merely STARTING with "st".
        "stterm",
        "start-terminal",
        # Client halves of a terminal SERVER: exactly the shape the table exists
        # to keep out.
        "urxvtc",
        "footclient",
    ],
)
def test_terminal_matching_is_exact_not_prefix(rejected: str) -> None:
    import jarvis.codex_auth as codex_mod

    assert codex_mod._linux_login_terminal_entry(rejected) is None


def test_terminal_matching_resolves_known_package_aliases() -> None:
    import jarvis.codex_auth as codex_mod

    entry = codex_mod._linux_login_terminal_entry("rxvt-unicode")
    assert entry is not None
    assert entry[0] == "urxvt"
    assert codex_mod._linux_login_terminal_entry("URXVT") == ("urxvt", ("-e",))


def test_a_terminal_that_never_started_the_guardian_is_named_as_the_cause(
    tmp_path: Path,
) -> None:
    """An unusable terminal used to surface as a guardian handshake error.

    The guardian writes its first acknowledgement before anything else, so a
    missing ack file proves the guardian never ran — which points at whatever
    was supposed to host it, not at the guardian.
    """

    class _Process:
        def poll(self) -> int | None:
            return None

    service = CodexAuthService("codex", visible_login=True)
    original = RuntimeError("The subscription-login guardian did not request lock handoff.")

    explained = service._explain_login_launch_failure(
        original,
        _Process(),  # type: ignore[arg-type]
        acknowledgement=tmp_path / "never-written.ack",
        launch_host="somebody-elses-terminal",
    )

    assert "somebody-elses-terminal" in str(explained)
    assert "could not host" in str(explained)


def test_a_guardian_that_did_acknowledge_keeps_the_original_diagnosis(
    tmp_path: Path,
) -> None:

    class _Process:
        def poll(self) -> int | None:
            return None

    acknowledgement = tmp_path / "written.ack"
    acknowledgement.write_text("waiting", encoding="ascii")
    service = CodexAuthService("codex", visible_login=True)
    original = RuntimeError("The subscription-login guardian did not confirm profile ownership.")

    explained = service._explain_login_launch_failure(
        original,
        _Process(),  # type: ignore[arg-type]
        acknowledgement=acknowledgement,
        launch_host="kitty",
    )

    assert explained is original
